"""Source adapters: read a raw file and normalize it to the canonical schema.

The rest of the package only ever sees the canonical columns defined in
``config`` (timestamp, speed, vehicle_class, direction). Supporting a new data
source = add a ``SourceSpec`` in config + (if its layout is unusual) a subclass
of ``SourceAdapter`` here. Radar works out of the box via ``SourceAdapter``.
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from .config import CANONICAL_COLUMNS, CLASS, DIRECTION, SPEED, TS, SourceSpec


class RawLoadError(Exception):
    """Raised when a raw file cannot be located or parsed into the schema."""


def find_raw_file(study_dir: str, spec: SourceSpec) -> str:
    """Return the raw data file in ``study_dir`` belonging to this study.

    Folders occasionally contain a stray raw file from a different study, so we
    must pick the one that matches THIS folder rather than the alphabetically
    first match:
      1. exact ``<folder>_Raw.csv`` (glob pattern with '*' -> folder name);
      2. a file whose name starts with the folder's location token;
      3. any file with 'raw' in its name; else the first match.
    """
    matches = sorted(glob.glob(os.path.join(study_dir, spec.raw_glob)))
    if not matches:
        raise RawLoadError(f"No file matching {spec.raw_glob!r} in {study_dir}")
    folder = os.path.basename(study_dir.rstrip("/\\"))

    exact_name = spec.raw_glob.replace("*", folder).lower()
    for m in matches:
        if os.path.basename(m).lower() == exact_name:
            return m

    loc_token = folder.split("_")[0].lower()
    cands = [m for m in matches if "raw" in os.path.basename(m).lower()] or matches
    pref = [m for m in cands if os.path.basename(m).lower().startswith(loc_token)]
    return (pref or cands)[0]


def list_raw_files(study_dir: str, spec: SourceSpec) -> list[str]:
    """All raw-glob matches in a folder (used to flag stray/extra files)."""
    return sorted(glob.glob(os.path.join(study_dir, spec.raw_glob)))


def _resolve_column(df: pd.DataFrame, candidates) -> str | None:
    """Find the actual df column for a canonical field given candidate names."""
    if isinstance(candidates, str):
        candidates = [candidates]
    norm = {c.strip().lower().replace(" ", ""): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower().replace(" ", "")
        if key in norm:
            return norm[key]
    return None


def _parse_datetime(series: pd.Series, formats) -> pd.Series:
    """Parse a datetime column, trying each configured format then inference."""
    best = None
    best_ok = -1
    for fmt in formats:
        try:
            parsed = pd.to_datetime(series, format=fmt, errors="coerce")
        except (ValueError, TypeError):
            continue
        ok = parsed.notna().sum()
        if ok > best_ok:
            best, best_ok = parsed, ok
        if ok == len(series):  # perfect parse, stop early
            return parsed
    if best is None:
        raise RawLoadError("Could not parse datetime column with any known format")
    return best


class SourceAdapter:
    """Default adapter: column-map + datetime-format + categorical normalization."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec

    def load(self, study_dir: str) -> pd.DataFrame:
        path = find_raw_file(study_dir, self.spec)
        return self.load_file(path)

    def load_file(self, path: str) -> pd.DataFrame:
        spec = self.spec
        df = pd.read_csv(path, encoding=spec.encoding, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        out = pd.DataFrame()

        ts_col = _resolve_column(df, spec.column_map[TS])
        if ts_col is None:
            raise RawLoadError(f"No timestamp column in {os.path.basename(path)} "
                               f"(looked for {spec.column_map[TS]})")
        out[TS] = _parse_datetime(df[ts_col], spec.datetime_formats)

        sp_col = _resolve_column(df, spec.column_map[SPEED])
        if sp_col is None:
            raise RawLoadError(f"No speed column in {os.path.basename(path)}")
        out[SPEED] = pd.to_numeric(df[sp_col], errors="coerce") * spec.speed_to_mph

        cl_col = _resolve_column(df, spec.column_map.get(CLASS, []))
        out[CLASS] = self._normalize(df[cl_col], spec.class_map) if cl_col else np.nan

        dr_col = _resolve_column(df, spec.column_map.get(DIRECTION, []))
        out[DIRECTION] = self._normalize(df[dr_col], spec.direction_map) if dr_col else np.nan

        before = len(out)
        out = out.dropna(subset=[TS, SPEED]).reset_index(drop=True)
        out.attrs["dropped_rows"] = before - len(out)
        out.attrs["source"] = spec.name
        out.attrs["raw_path"] = path
        return out[CANONICAL_COLUMNS]

    @staticmethod
    def _normalize(series: pd.Series, mapping: dict[str, str]) -> pd.Series:
        if not mapping:
            return series.astype("string").str.strip()
        s = series.astype("string").str.strip()
        lower = s.str.lower()
        return lower.map(mapping).fillna(s)


def get_adapter(spec: SourceSpec) -> SourceAdapter:
    """Factory hook — return a specialized adapter per source if/when needed."""
    return SourceAdapter(spec)
