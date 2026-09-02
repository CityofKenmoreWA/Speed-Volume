"""Study catalog WITH per-study coordinates.

Same table as ``catalog.py`` (location, year, dates, cached metrics), plus **lat**
and **lon** columns read from each installation photo's GPS (WGS84 / EPSG:4326).
Photos without GPS (or with a 0/0 fix) get empty lat/lon.

Like ``catalog.py`` this is **incremental**: metrics and lat/lon for studies
already in the existing ``study_catalog_latlon.csv`` are reused; only new studies
are processed. No projection library required — lat/lon come straight from EXIF.
"""
from __future__ import annotations

import math
import os
from typing import Optional

import pandas as pd

from . import catalog
from .discovery import is_usable_gps, photo_gps  # noqa: F401  (photo_gps kept for API compat)

CATALOG_XY_NAME = "study_catalog_latlon.csv"
LATLON_COLUMNS = ["lat", "lon"]
CATALOG_XY_COLUMNS = catalog.CATALOG_COLUMNS + LATLON_COLUMNS


def _valid_gps(gps) -> bool:
    """True only for a real fix. Defers to ``discovery.is_usable_gps`` so the
    lat/lon catalog and the dashboard agree on what counts as a location."""
    if not gps:
        return False
    return is_usable_gps(gps[0], gps[1])


def latlon_from_gps(gps) -> tuple:
    """(lat, lon) from a photo GPS fix, rounded to 6 dp (~0.1 m). None/None if no fix."""
    if not _valid_gps(gps):
        return (None, None)
    lat, lon = gps
    return (round(float(lat), 6), round(float(lon), 6))


def _present(v) -> bool:
    return v is not None and v != "" and not (isinstance(v, float) and math.isnan(v))


def build_catalog_xy(base: str, previous: Optional[pd.DataFrame] = None,
                     compute: bool = True, stats: Optional[dict] = None) -> pd.DataFrame:
    """Build the catalog + lat/lon. ``previous`` (an existing lat/lon catalog) supplies
    both the reusable metrics and reusable lat/lon for unchanged studies; only new
    studies are processed. ``stats`` is populated with counts."""
    df = catalog.build_catalog(base, previous=previous, compute=compute, stats=stats)

    prev_ll: dict[str, tuple] = {}
    if previous is not None and not previous.empty and {"path", "lat", "lon"} <= set(previous.columns):
        for r in previous.to_dict("records"):
            prev_ll[str(r.get("path"))] = (r.get("lat"), r.get("lon"))

    lats, lons = [], []
    reused = computed = missing = 0
    for row in df.to_dict("records"):
        path = str(row.get("path"))
        cached = prev_ll.get(path)
        if cached is not None and _present(cached[0]) and _present(cached[1]):
            lat, lon = cached
            reused += 1
        else:
            gps = catalog.study_from_row(row).loc_gps
            lat, lon = latlon_from_gps(gps)
            computed += 1
            if lat is None:
                missing += 1
        lats.append(lat)
        lons.append(lon)
    df["lat"] = lats
    df["lon"] = lons

    if stats is not None:
        stats.update({"latlon_reused": reused, "latlon_computed": computed,
                      "latlon_missing": missing,
                      "latlon_present": int(df["lat"].notna().sum())})
    return df[CATALOG_XY_COLUMNS]


def catalog_xy_path(base: str) -> str:
    return os.path.join(base, CATALOG_XY_NAME)


def write_catalog_xy(df: pd.DataFrame, path: str) -> Optional[str]:
    return catalog.atomic_to_csv(df, path)


def read_catalog_xy(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"install_date": str})
        if df.empty:
            return None
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
        for c in catalog.METRIC_COLUMNS + LATLON_COLUMNS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception:
        return None


def refresh_catalog_xy(base: str, out: Optional[str] = None, compute: bool = True,
                       stats: Optional[dict] = None) -> tuple:
    """Incrementally (re)build the lat/lon catalog and write it. Returns (df, out_path|None).

    Reuse source, in order: the existing lat/lon catalog (metrics + lat/lon), else the
    plain ``study_catalog.csv`` (metrics only) so a first build doesn't recompute
    metrics already stored there — it only needs to read the photo GPS.
    """
    out = out or catalog_xy_path(base)
    previous = read_catalog_xy(out)
    if previous is None:
        previous = catalog.read_catalog(base)
    df = build_catalog_xy(base, previous=previous, compute=compute, stats=stats)
    written = write_catalog_xy(df, out)
    return df, written
