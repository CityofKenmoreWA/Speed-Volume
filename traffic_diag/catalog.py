"""Study catalog: a single index of every study across all year folders.

Instead of walking one year's folders on demand, the whole tree is scanned once
into a table (location, year, install date, path, ...) plus a few headline
metrics (avg speed, 85th %ile, ADT, AWDT), and persisted to a CSV. The dashboard
reads that table to drive a **Location -> Year** picker without re-walking the
disk on every interaction.

Metrics are computed **incrementally**: a refresh keeps the metrics already stored
for a study whose path AND fingerprint are unchanged, and only runs the (relatively
costly) per-study processing for studies that are new or whose data files have
changed. So the first refresh computes everything once; later refreshes only touch
what actually moved.

The fingerprint is what makes an unattended refresh trustworthy. Matching on path
alone would reuse stale metrics forever whenever a study was corrected in place —
a re-pulled ``_Raw.csv``, a ``Limit:`` added to ``_Notes.txt``, a replaced
``_Report.xlsx``. Those edits leave the path identical, so the path is not enough
to tell "already computed" from "computed from older data".
"""
from __future__ import annotations

import glob
import os
from datetime import date
from typing import Optional

import pandas as pd

from .discovery import Study, find_studies
from .pipeline import process_study

CATALOG_NAME = "study_catalog.csv"
STRUCT_COLUMNS = ["location", "year", "install_date", "study_id",
                  "status", "source_name", "study_type", "path", "fingerprint"]
# Files whose content decides a study's metrics; the fingerprint covers these.
FINGERPRINT_GLOBS = ("*_Raw.csv", "*_Notes.txt", "*_Report.xlsx")
# Headline metrics (Merged direction), cached per study.
METRIC_COLUMNS = ["avg_speed", "p85_speed", "adt", "awdt"]
CATALOG_COLUMNS = STRUCT_COLUMNS + METRIC_COLUMNS


def study_fingerprint(path: str) -> str:
    """A cheap change token for one study folder: newest mtime + total size of its
    data files, as "<mtime>:<bytes>:<count>".

    Metadata only — no file contents are read, so a full pass over ~770 studies
    costs about five seconds over the share. Returns "" if the folder cannot be
    read, which is treated as "changed" and simply recomputes.
    """
    newest = 0.0
    total = 0
    count = 0
    try:
        for pattern in FINGERPRINT_GLOBS:
            for f in glob.glob(os.path.join(path, pattern)):
                st = os.stat(f)
                newest = max(newest, st.st_mtime)
                total += st.st_size
                count += 1
    except OSError:
        return ""
    return f"{newest:.0f}:{total}:{count}" if count else ""


def _struct_row(s: Study) -> dict:
    return {
        "location": s.location,
        "year": s.year,
        "install_date": s.install_date.isoformat() if s.install_date else "",
        "study_id": s.study_id,
        "status": s.status,
        "source_name": s.source_name,
        "study_type": s.study_type,
        "path": s.path,
        "fingerprint": study_fingerprint(s.path),
    }


def _study_metrics(study: Study) -> dict:
    """Compute the Merged headline metrics for one study (None on failure)."""
    empty = {c: None for c in METRIC_COLUMNS}
    try:
        m = process_study(study, run_diag=False).merged
    except Exception:
        return empty
    ok = lambda v: v is not None and v == v  # not None, not NaN
    return {
        "avg_speed": round(m.avg_speed, 2) if ok(m.avg_speed) else None,
        "p85_speed": round(m.design_speed, 2) if ok(m.design_speed) else None,
        "adt": round(m.adt, 1) if ok(m.adt) else None,
        "awdt": round(m.avg_weekday_traffic, 1) if ok(m.avg_weekday_traffic) else None,
    }


def _cache_key(path: str) -> str:
    r"""Identify a study by its place in the tree, independent of how the tree was
    reached.

    The same share is addressed by more than one name: the mapped drive
    (``V:\...``) from a desktop, and the UNC path (``\\server\share\...``) from a
    service, because drive mappings do not exist for a service account. Keying the
    cache on the absolute path would make each form invalidate the other's entries
    — every refresh would recompute all ~770 studies, and a 5-minute schedule
    would never converge.

    ``os.path.relpath`` cannot bridge the two (it raises ValueError across
    different mounts), so the key is anchored on the ``<year>`` folder instead and
    keeps everything below it. That covers both tree shapes discovery supports:
    ``<year>/<study>`` and ``<year>/<special subdir>/<study>``.
    """
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    for i in range(len(parts) - 1, -1, -1):
        if len(parts[i]) == 4 and parts[i].isdigit():
            return "/".join(parts[i:]).lower()
    return "/".join(parts[-2:]).lower()      # unexpected layout: fall back to the tail


def _has_metrics(row: dict) -> bool:
    """True if a cached row already carries all metric values."""
    for c in METRIC_COLUMNS:
        v = row.get(c)
        if v is None or v == "" or (isinstance(v, float) and v != v):
            return False
    return True


def build_catalog(base: str, source_name: str = "radar",
                  previous: Optional[pd.DataFrame] = None,
                  compute: bool = True, stats: Optional[dict] = None) -> pd.DataFrame:
    """Scan every year under ``base`` and return one row per study.

    ``previous`` (an existing catalog DataFrame): a row reuses its stored metrics
    when its position under ``base`` appears there, it already has metrics, AND
    its fingerprint still matches — so new studies and edited studies are both recomputed, and
    everything else is left alone. ``compute=False`` skips metric computation for
    those (structure only). ``stats`` is populated with counts.

    A catalog written before fingerprints existed has none stored, so every row
    looks changed and the first refresh recomputes the whole tree once. That is
    intentional: it is also the pass that repairs any metrics that had gone stale
    under the old path-only rule.
    """
    prev: dict[str, dict] = {}
    if previous is not None and not previous.empty and "path" in previous.columns:
        for r in previous.to_dict("records"):
            prev[_cache_key(r.get("path"))] = r

    n_reused = n_computed = 0
    rows = []
    for s in find_studies(base, source_name=source_name):
        row = _struct_row(s)
        cached = prev.get(_cache_key(s.path))
        fresh = (cached is not None
                 and _has_metrics(cached)
                 and str(cached.get("fingerprint") or "") == row["fingerprint"]
                 and row["fingerprint"] != "")
        if fresh:
            row.update({c: cached.get(c) for c in METRIC_COLUMNS})
            n_reused += 1
        elif compute:
            row.update(_study_metrics(s))
            n_computed += 1
        else:
            row.update({c: None for c in METRIC_COLUMNS})
        rows.append(row)

    df = pd.DataFrame(rows, columns=CATALOG_COLUMNS)
    if not df.empty:
        df = df.sort_values(["location", "year", "install_date"], ignore_index=True)
    if stats is not None:
        stats.update({"total": len(df), "reused": n_reused, "computed": n_computed})
    return df


def catalog_path(base: str) -> str:
    return os.path.join(base, CATALOG_NAME)


def atomic_to_csv(df: pd.DataFrame, path: str) -> Optional[str]:
    """Write a CSV via a temp file + atomic replace, so a mid-write failure (network
    hiccup, file open in Excel) never leaves a partial/corrupt file — the previous
    version stays intact. Returns the path on success, else None."""
    tmp = path + ".tmp"
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return None


def write_catalog(df: pd.DataFrame, base: str) -> Optional[str]:
    """Persist the catalog next to the data tree. Returns the path, or None if the
    location is not writable (e.g. a read-only share, or the CSV is open in Excel)."""
    return atomic_to_csv(df, catalog_path(base))


def read_catalog(base: str) -> Optional[pd.DataFrame]:
    """Read the persisted catalog CSV if present and non-empty, else None."""
    path = catalog_path(base)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"install_date": str})
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
        for c in METRIC_COLUMNS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        for c in STRUCT_COLUMNS:
            if c in df.columns and c != "year":
                df[c] = df[c].fillna("")
        return df if not df.empty else None
    except Exception:
        return None


def refresh_catalog(base: str, compute: bool = True,
                    stats: Optional[dict] = None) -> tuple:
    """Rescan the disk, reusing metrics from the existing CSV for unchanged studies,
    computing only new ones, then rewrite the CSV. Returns ``(df, path|None)`` —
    ``None`` means the write failed and whatever is on the share is now stale, which
    is what lets the scheduled task exit non-zero instead of reporting success."""
    df = build_catalog(base, previous=read_catalog(base), compute=compute, stats=stats)
    return df, write_catalog(df, base)


def load_or_build_catalog(base: str, rebuild: bool = False) -> pd.DataFrame:
    """Return the study catalog for the dashboard.

    ``rebuild=False`` (default): read the persisted CSV if it exists (fast). If it is
    missing, build **structure only** (just a directory scan — instant, even over a
    network share) so the app opens immediately; the headline metrics are filled by
    the explicit refresh (run_dashboard.bat / Rebuild button / build_catalog.py),
    which shows progress instead of a silent spinner. ``rebuild=True``: full
    incremental refresh (computes metrics for new studies).
    """
    if not rebuild:
        cached = read_catalog(base)
        if cached is not None:
            return cached
        return refresh_catalog(base, compute=False)[0]   # structure only → no page-load hang
    return refresh_catalog(base, compute=True)[0]


def study_from_row(row) -> Study:
    """Reconstruct a ``Study`` from a catalog row (no disk rescan)."""
    d = str(row.get("install_date") or "")
    install = None
    if d:
        try:
            install = date.fromisoformat(d)
        except ValueError:
            install = None
    return Study(
        path=str(row["path"]),
        year=int(row["year"]),
        location=str(row["location"]),
        install_date=install,
        source_name=str(row.get("source_name") or "radar"),
        status=str(row.get("status") or "normal"),
    )
