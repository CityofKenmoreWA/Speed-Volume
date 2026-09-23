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

import fnmatch
import glob
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

import pandas as pd

from .discovery import Study, find_studies, relocate_study
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

    Metadata only — no file contents are read. Returns "" if the folder cannot be
    read, which is treated as "changed" and simply recomputes.

    One ``scandir`` rather than a glob per pattern. Every refresh fingerprints
    every study, so this runs ~770 times over a network share and the cost is
    round trips, not work: three globs plus a ``stat`` per hit was about seven
    round trips per folder where one listing will do. Windows returns size and
    mtime as part of the listing itself, so ``DirEntry.stat()`` here is free.
    Matching then happens in memory. Measured on the share: 5.8s -> 1.7s, with
    byte-identical fingerprints for all 776 studies.
    """
    newest = 0.0
    total = 0
    count = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not any(fnmatch.fnmatch(entry.name, p) for p in FINGERPRINT_GLOBS):
                    continue
                if not entry.is_file():
                    continue
                st = entry.stat()
                newest = max(newest, st.st_mtime)
                total += st.st_size
                count += 1
    except OSError:
        return ""
    return f"{newest:.0f}:{total}:{count}" if count else ""


# Fingerprinting is latency-bound on a network share, so the listings are issued
# concurrently. Eight keeps the share busy without flooding it: measured 1.7s
# serial -> 0.24s, and going to sixteen only buys another 0.08s.
_FINGERPRINT_WORKERS = 8


def fingerprint_all(paths) -> dict:
    """``{path: fingerprint}`` for many studies at once, in parallel."""
    paths = list(paths)
    if not paths:
        return {}
    with ThreadPoolExecutor(max_workers=_FINGERPRINT_WORKERS) as pool:
        return dict(zip(paths, pool.map(study_fingerprint, paths)))


def study_types_for(studies) -> dict:
    """``{path: study_type}``, reading the notes files concurrently.

    ``Study.study_type`` parses ``_Notes.txt``, so asking for it one study at a
    time means one file read per study over the share. Only the studies whose
    notes actually changed reach this — the rest keep the value already stored in
    the catalog — but a first build has to read all of them.
    """
    studies = list(studies)
    if not studies:
        return {}

    def _one(s):
        try:
            return s.study_type
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=_FINGERPRINT_WORKERS) as pool:
        return dict(zip((s.path for s in studies), pool.map(_one, studies)))


def _cached_study_type(cached: Optional[dict]) -> Optional[str]:
    """The stored study_type, or None if the row has none to reuse.

    An empty string is a real value here (plenty of studies classify as nothing in
    particular), so only a genuinely absent or NaN cell counts as "not stored".
    """
    if not cached or "study_type" not in cached:
        return None
    v = cached.get("study_type")
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v)


def _struct_row(s: Study, fingerprint: Optional[str] = None,
                study_type: Optional[str] = None) -> dict:
    """One catalog row's structural fields.

    ``fingerprint`` and ``study_type`` are passed in when the caller already has
    them — both cost a trip to the share per study, and a bulk refresh resolves
    them for the whole tree at once rather than one at a time in a loop.
    """
    return {
        "location": s.location,
        "year": s.year,
        "install_date": s.install_date.isoformat() if s.install_date else "",
        "study_id": s.study_id,
        "status": s.status,
        "source_name": s.source_name,
        "study_type": s.study_type if study_type is None else study_type,
        "path": s.path,
        "fingerprint": study_fingerprint(s.path) if fingerprint is None else fingerprint,
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
    found = find_studies(base, source_name=source_name)

    # Fingerprint every study up front and in parallel. One at a time inside the
    # loop this was one of the two dominant costs of a refresh, and it is paid on
    # every run whether or not anything changed.
    prints = fingerprint_all(s.path for s in found)

    # study_type parses the study's _Notes.txt, and that file is part of the
    # fingerprint — so a study whose fingerprint still matches cannot have a
    # different study_type, and the stored value stands. Only the studies that
    # really changed are read, and those are read concurrently. Reading all of
    # them every refresh was the other dominant cost.
    reusable_type = {}
    needs_type = []
    for s in found:
        cached = prev.get(_cache_key(s.path))
        fp = prints.get(s.path, "")
        fp_match = (cached is not None and fp != ""
                    and str(cached.get("fingerprint") or "") == fp)
        stored = _cached_study_type(cached) if fp_match else None
        if stored is None:
            needs_type.append(s)
        else:
            reusable_type[s.path] = stored
    reusable_type.update(study_types_for(needs_type))

    for s in found:
        row = _struct_row(s, prints.get(s.path), reusable_type.get(s.path))
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


def resolve_study(base: str, row) -> tuple:
    """``(study, moved_from)`` for a catalog row, following the folder if it moved.

    The catalog stores each study's path, so a row goes wrong the moment someone
    reclassifies a study by dragging its folder between ``_Incomplete``,
    ``_Compromised Studies`` and the year folder. Until the next refresh the row
    points at a path that no longer exists, and the only symptom was a
    ``RawLoadError`` naming a glob — no hint that the study had simply moved.

    So the path is checked before use. If it is gone the folder is looked up by
    name (a few stat calls, not a tree walk) and the caller gets the study at its
    real location plus the stale path in ``moved_from``, which is its cue to
    mention the move and get the catalog rebuilt. ``moved_from`` is None in the
    normal case. A study that cannot be found anywhere raises ``LookupError``,
    since there is nothing sensible to process.
    """
    study = study_from_row(row)
    if os.path.isdir(study.path):
        return study, None

    found = relocate_study(base, study.folder_name, year=study.year,
                           source_name=study.source_name)
    if found is None:
        raise LookupError(
            f"Study folder '{study.folder_name}' is not at its recorded location "
            f"({study.path}) and was not found anywhere under {base}. It may have "
            f"been renamed or removed; refresh the study list.")
    return found, study.path
