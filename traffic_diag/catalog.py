"""Study catalog: a single index of every study across all year folders.

Instead of walking one year's folders on demand, the whole tree is scanned once
into a table (location, year, install date, path, ...) plus a few headline
metrics (avg speed, 85th %ile, ADT, AWDT), and persisted to a CSV. The dashboard
reads that table to drive a **Location -> Year** picker without re-walking the
disk on every interaction.

Metrics are computed **incrementally**: a refresh keeps the metrics already stored
for studies whose folder path is unchanged, and only runs the (relatively costly)
per-study processing for NEW studies. So the first refresh computes everything
once; later refreshes only touch newly-added folders.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

import pandas as pd

from .discovery import Study, find_studies
from .pipeline import process_study

CATALOG_NAME = "study_catalog.csv"
STRUCT_COLUMNS = ["location", "year", "install_date", "study_id",
                  "status", "source_name", "study_type", "path"]
# Headline metrics (Merged direction), cached per study.
METRIC_COLUMNS = ["avg_speed", "p85_speed", "adt", "awdt"]
CATALOG_COLUMNS = STRUCT_COLUMNS + METRIC_COLUMNS


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

    ``previous`` (an existing catalog DataFrame): rows whose ``path`` already
    appears there — and already have metrics — reuse those metrics unchanged;
    only NEW studies are processed. ``compute=False`` skips metric computation
    for new studies (structure only). ``stats`` is populated with counts.
    """
    prev: dict[str, dict] = {}
    if previous is not None and not previous.empty and "path" in previous.columns:
        for r in previous.to_dict("records"):
            prev[str(r.get("path"))] = r

    n_reused = n_computed = 0
    rows = []
    for s in find_studies(base, source_name=source_name):
        row = _struct_row(s)
        cached = prev.get(s.path)
        if cached is not None and _has_metrics(cached):
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
                    stats: Optional[dict] = None) -> pd.DataFrame:
    """Rescan the disk, reusing metrics from the existing CSV for unchanged studies,
    computing only new ones, then rewrite the CSV."""
    df = build_catalog(base, previous=read_catalog(base), compute=compute, stats=stats)
    write_catalog(df, base)
    return df


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
        return refresh_catalog(base, compute=False)   # structure only → no page-load hang
    return refresh_catalog(base, compute=True)


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
