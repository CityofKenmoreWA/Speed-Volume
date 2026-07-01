"""Configuration: canonical schema, per-source parsing rules, and analysis knobs.

Everything that varies between data sources (column names, datetime formats,
timestamp structure) or between agencies (speed bins, peak windows, diagnostic
thresholds) lives here so the rest of the package stays source-agnostic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# Default root of the data tree (overridable via env var or CLI/dashboard input).
DEFAULT_BASE = os.environ.get(
    "TRAFFIC_DATA_BASE",
    r"C:\Users\moshanreh\Desktop\Mohammad\Speed and Volume Studies",
)

# --------------------------------------------------------------------------- #
# Canonical column names used everywhere downstream of the source adapters.
# --------------------------------------------------------------------------- #
TS = "timestamp"          # tz-naive datetime of the observation
SPEED = "speed"           # speed in mph (float)
CLASS = "vehicle_class"   # canonical class: "Small" | "Medium" | "Large"
DIRECTION = "direction"   # canonical direction: "Incoming" | "Outgoing"

CANONICAL_COLUMNS = [TS, SPEED, CLASS, DIRECTION]


@dataclass(frozen=True)
class SourceSpec:
    """How to read and normalize one data-source family.

    Adapters (see ``sources.py``) consume this. Adding SFS/GridSmart later means
    adding a SourceSpec + an adapter, with no changes to metrics/diagnostics.
    """

    name: str
    raw_glob: str                       # glob (within a study folder) for the raw file
    # Mapping from canonical name -> source column name (or list of candidates).
    column_map: dict[str, object]
    # Candidate datetime formats tried in order; None => let pandas infer.
    datetime_formats: tuple[Optional[str], ...] = (None,)
    # Normalization maps for categorical fields (source value -> canonical).
    class_map: dict[str, str] = field(default_factory=dict)
    direction_map: dict[str, str] = field(default_factory=dict)
    # If the source stores speed in km/h, set to 0.621371 to convert to mph.
    speed_to_mph: float = 1.0
    encoding: str = "utf-8-sig"


# --- Radar (current scope) -------------------------------------------------- #
# Raw CSV columns: "Date&Time, Speed, Class, Direction"
#   Date&Time   e.g. "10/6/2025 0:10"  (M/D/YYYY H:MM, 24h, no seconds)
#   Class       Small | Medium | Large
#   Direction   Incoming | Outgoing
RADAR = SourceSpec(
    name="radar",
    raw_glob="*_Raw.csv",
    column_map={
        TS: ["Date&Time", "Date & Time", "DateTime", "Date&Time "],
        SPEED: ["Speed"],
        CLASS: ["Class"],
        DIRECTION: ["Direction"],
    },
    datetime_formats=(
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        None,  # final fallback: pandas inference
    ),
    class_map={
        "small": "Small", "medium": "Medium", "large": "Large",
        "veh_sm": "Small", "veh_med": "Medium", "veh_lg": "Large",
        "motorcycle": "Small", "sedan": "Medium", "truck": "Large",
    },
    direction_map={
        "incoming": "Incoming", "outgoing": "Outgoing",
        "in": "Incoming", "out": "Outgoing",
    },
)

# --- Placeholders for future sources --------------------------------------- #
# Fill column_map / datetime_formats once real SFS / GridSmart samples exist.
SFS = SourceSpec(
    name="sfs",
    raw_glob="*_Raw.csv",
    column_map={TS: ["DateTime"], SPEED: ["Speed"], CLASS: ["Class"], DIRECTION: ["Direction"]},
    datetime_formats=(None,),
)
GRIDSMART = SourceSpec(
    name="gridsmart",
    raw_glob="*.csv",
    column_map={TS: ["Timestamp"], SPEED: ["Speed"], CLASS: ["Class"], DIRECTION: ["Direction"]},
    datetime_formats=(None,),
)

SOURCES: dict[str, SourceSpec] = {s.name: s for s in (RADAR, SFS, GRIDSMART)}


@dataclass(frozen=True)
class AnalysisConfig:
    """Agency/analysis knobs that shape statistics and the study window."""

    # Default posted speed limit (mph) when not supplied per-study.
    default_speed_limit: int = 25
    # Study window length in whole days.
    study_days: int = 7
    # Percentile reported as the design speed (fraction).
    design_percentile: float = 0.85
    # Width (mph) of the pace interval.
    pace_width: int = 10
    # Upper edge of the speed histogram bins (1-mph bins from 0).
    max_speed_bin: int = 100
    # Weekday set (Mon=0 .. Sun=6) used for "weekday" averages.
    weekday_indices: tuple[int, ...] = (0, 1, 2, 3, 4)
    # Which days feed the speed/percentile distribution:
    #   "weekday" — true Mon-Fri days in the window (corrected; default)
    #   "first"   — the legacy Excel behavior: the first (study_days - 2) calendar
    #               days from the window start, regardless of weekday.
    # The legacy Excel mislabeled "first 5 days" as "weekday"; for studies that do
    # not start on Monday that wrongly pulls weekend days into the weekday 85th.
    percentile_window: str = "weekday"


DEFAULT_ANALYSIS = AnalysisConfig()
# Legacy reproduction (matches the original Excel exactly, incl. its quirk).
LEGACY_ANALYSIS = AnalysisConfig(percentile_window="first")


@dataclass(frozen=True)
class DiagnosticThresholds:
    """Tunable thresholds for the data-quality / compromised-study checks."""

    # Fraction of expected hours that must contain data for a day to be "complete".
    min_hourly_coverage: float = 0.75
    # Min vehicles/day below which volume is flagged unusually low.
    low_volume_per_day: int = 50
    # Directional imbalance flagged when the busier direction exceeds this share.
    max_direction_share: float = 0.70
    # Speeds outside [min, max] mph are flagged as outliers.
    speed_min: float = 5.0
    speed_max: float = 90.0
    # A gap (no observations) longer than this many hours is flagged.
    max_gap_hours: float = 3.0
    # Day-to-day volume swing (max/min) above this is flagged as erratic.
    max_daily_volume_ratio: float = 3.0


DEFAULT_THRESHOLDS = DiagnosticThresholds()
