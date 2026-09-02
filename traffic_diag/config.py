"""Configuration: canonical schema, per-source parsing rules, and analysis knobs.

Everything that varies between data sources (column names, datetime formats,
timestamp structure) or between agencies (speed bins, peak windows, diagnostic
thresholds) lives here so the rest of the package stays source-agnostic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# Paths. Everything the app produces is anchored to the repo root so the project
# is portable; only the DATA lives outside it. Both are overridable from the bat
# file (APP_ROOT / TRAFFIC_DATA_BASE) — see run_dashboard.bat.
# --------------------------------------------------------------------------- #

# Repo root: this file is <root>/traffic_diag/config.py, so the root is two levels
# up. Override with APP_ROOT to relocate the app without editing code.
REPO_ROOT = os.environ.get(
    "APP_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

# Where generated reports go. Relative to the repo root by default; overridable.
REPORTS_DIR = os.environ.get("TRAFFIC_REPORTS_DIR", os.path.join(REPO_ROOT, "reports"))

# --------------------------------------------------------------------------- #
# Root of the DATA tree (the <year> folders) - the one directory outside the app.
#
# Resolved per machine, first hit wins:
#   1. the TRAFFIC_DATA_BASE environment variable - what run_dashboard.bat and the
#      server's KenmoreTrafficDashboard.bat set;
#   2. a ``data_base.txt`` beside the app holding the path on one line. It is
#      gitignored, so it stays on the machine it describes and never reaches the
#      repo or the handoff bundle. Copy ``data_base.txt.example`` to create it.
#   3. nothing - an empty string, which callers report via NO_DATA_BASE_MSG.
#
# There is deliberately NO built-in path. A hardcoded default meant that whenever
# the environment variable went missing the app silently pointed at one
# developer's home directory, named that person in the error, and shipped their
# username inside a deliverable handed to the city's IT department.
# --------------------------------------------------------------------------- #
DATA_BASE_FILE = os.path.join(REPO_ROOT, "data_base.txt")


def read_data_base_file(path: Optional[str] = None) -> str:
    """The path held in ``data_base.txt``, or "" if it is absent/blank/unreadable.

    First non-blank, non-comment line wins. Surrounding quotes are stripped and
    ``%VARS%`` / ``~`` are expanded, so the file can be written the way a path is
    normally pasted out of Explorer.
    """
    try:
        with open(path or DATA_BASE_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip().strip('"').strip()
                if line and not line.startswith("#"):
                    return os.path.expandvars(os.path.expanduser(line))
    except OSError:
        pass
    return ""


def resolve_data_base() -> str:
    """The configured study-data root, or "" when nothing is configured."""
    env = (os.environ.get("TRAFFIC_DATA_BASE") or "").strip().strip('"').strip()
    return env or read_data_base_file()


DEFAULT_BASE = resolve_data_base()

# Shown when no data root is configured at all, so the failure names the fix
# rather than a path nobody recognises.
NO_DATA_BASE_MSG = (
    "No study-data folder is configured. Set the TRAFFIC_DATA_BASE environment "
    "variable, or put the path on one line in data_base.txt next to the app "
    "(copy data_base.txt.example), or pass --base on the command line."
)

# --------------------------------------------------------------------------- #
# Figure raster resolution. Every PNG the app writes — standalone files, the
# base64 images embedded in the HTML report, the images placed into the PDF, and
# the Streamlit renders — goes out at this DPI, so print output stays sharp.
# Figures are sized in inches (figsize), so DPI changes pixel count only: layout,
# font sizes and proportions are identical at any value. Overridable for a quick
# low-res preview run (e.g. TRAFFIC_FIGURE_DPI=100).
# --------------------------------------------------------------------------- #
FIGURE_DPI = int(os.environ.get("TRAFFIC_FIGURE_DPI", "250"))

# --------------------------------------------------------------------------- #
# City of Kenmore brand (from the Communications style guide). Used for report
# letterheads and dashboard chrome — NOT for the data color scales, which stay
# meaningful (speed green->red, volume white->blue).
# --------------------------------------------------------------------------- #
KENMORE_NAVY = "#0E1E37"      # primary dark (headings, rules)
KENMORE_AMBER = "#FFB300"     # primary accent (the gold line)
KENMORE_LIGHT = "#E4E6EA"     # primary light (table header fill)
KENMORE_TEAL = "#016666"      # accent
KENMORE_OFFWHITE = "#F7F7F8"  # accent background
KENMORE_OLIVE = "#CCC737"     # accent (logo green family)

# City logo (full-color, transparent) shipped with the app under assets/.
LOGO_PATH = os.path.join(REPO_ROOT, "assets", "kenmore_logo.png")
LOGO_WHITE_PATH = os.path.join(REPO_ROOT, "assets", "kenmore_logo_white.png")

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
    # Latest hour (24h) at which a 60-minute peak window may START.
    #
    # The AM search used to run to an 11:45 AM start, which reported windows like
    # "11:45 AM - 12:45 PM" as the morning peak - three quarters of it after noon.
    # Capping the start at 11:00 AM keeps an AM peak inside the morning.
    am_peak_latest_start_hour: int = 11
    # PM windows start no earlier than noon and no later than 11:00 PM, so a peak
    # never straddles midnight into the next day.
    pm_peak_earliest_start_hour: int = 12
    pm_peak_latest_start_hour: int = 23
    # Upper edge of the speed histogram bins (1-mph bins from 0).
    max_speed_bin: int = 100
    # Weekday set (Mon=0 .. Sun=6) used for "weekday" averages.
    weekday_indices: tuple[int, ...] = (0, 1, 2, 3, 4)
    # Weekend set, used for the "Weekend" summary columns.
    weekend_indices: tuple[int, ...] = (5, 6)
    # Day set behind the hourly tables' "Weekday" summary columns. None = use
    # weekday_indices (Mon-Fri, correct). The legacy Excel used Mon-Thu only, which
    # silently dropped Friday from every hourly weekday average; LEGACY_ANALYSIS
    # restores that so the old workbooks can still be reproduced on demand.
    hourly_weekday_indices: Optional[tuple[int, ...]] = None
    # Which days feed the speed/percentile distribution:
    #   "weekday" — true Mon-Fri days in the window (corrected; default)
    #   "first"   — the legacy Excel behavior: the first (study_days - 2) calendar
    #               days from the window start, regardless of weekday.
    # The legacy Excel mislabeled "first 5 days" as "weekday"; for studies that do
    # not start on Monday that wrongly pulls weekend days into the weekday 85th.
    percentile_window: str = "weekday"

    @property
    def hourly_weekdays(self) -> tuple:
        """Days behind the hourly "Weekday" columns (Mon-Fri unless overridden)."""
        if self.hourly_weekday_indices is None:
            return self.weekday_indices
        return self.hourly_weekday_indices


DEFAULT_ANALYSIS = AnalysisConfig()
# Legacy reproduction: matches the original Excel exactly, quirks included - the
# first-5-calendar-days "weekday" percentile window AND the Mon-Thu hourly weekday
# averages. For comparing against archived workbooks, not for new reporting.
LEGACY_ANALYSIS = AnalysisConfig(percentile_window="first",
                                 hourly_weekday_indices=(0, 1, 2, 3))


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
