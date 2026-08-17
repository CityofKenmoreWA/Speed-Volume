"""Statistics & tables matching the legacy Excel report (verified to 1e-6).

Methodology (see project memory for the full audit):
  * Average / Median / Max speed   -> all vehicles in the 7-day window.
  * Percentile speeds              -> WEEKDAY (Mon-Fri) distribution only,
                                      cumulative P(speed < s), linear interp.
  * ADT = total / study_days; Average Weekday Traffic = mean of Mon-Fri totals.
  * AM/PM/overall peak hour -> busiest 60-min (15-min sliding) window of average
    WEEKDAY (Mon-Fri) volume; each peak carries its average weekday hourly volume.
  * Hourly table "Weekday Avg" column = mean of Mon-Thu only (legacy Excel quirk).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import CLASS, DIRECTION, SPEED, TS, AnalysisConfig, DEFAULT_ANALYSIS

DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOUR_LABELS = [
    f"{(h % 12) or 12}:00 {'AM' if h < 12 else 'PM'} -"
    f"{((h + 1) % 12) or 12}:00 {'AM' if (h + 1) % 24 < 12 else 'PM'}"
    for h in range(24)
]


def percentile_speed(counts_by_speed: dict[int, int], target_pct: float,
                     max_speed_bin: int = 100) -> Optional[float]:
    """Speed at ``target_pct`` using the Excel cumulative-interpolation method.

    Excel builds F[s] = count(speed < s) on an integer grid s=1..max-1, then
    G[s] = F[s]/N*100, and for percentile P interpolates between the bracketing
    integer speeds: ``s + (P - G[s]) / (G[s+1] - G[s])``.
    """
    speeds = np.arange(1, max_speed_bin)
    cnt = np.array([counts_by_speed.get(int(s), 0) for s in speeds], dtype=float)
    n = cnt.sum()
    if n == 0:
        return None
    cum_lt = np.concatenate(([0.0], np.cumsum(cnt)[:-1]))   # count(speed < s)
    g = cum_lt / n * 100.0
    # Largest speed s with G[s] STRICTLY < P, then interpolate to the next speed.
    # Strict '<' matches Excel when the cumulative is flat exactly at P (a zero-count
    # speed straddling the percentile); it equals '<=' in every non-tied case.
    below = np.where(g < target_pct)[0]
    if len(below) == 0:
        return float(speeds[0])
    i = int(below[-1])
    if i + 1 >= len(speeds) or g[i + 1] == g[i]:
        return float(speeds[i])
    return float(speeds[i] + (target_pct - g[i]) / (g[i + 1] - g[i]) * (speeds[i + 1] - speeds[i]))


def _counts_by_speed(speeds: pd.Series) -> dict[int, int]:
    return speeds.round().astype(int).value_counts().to_dict()


def _fmt_clock(minutes: int) -> str:
    minutes %= 24 * 60
    h, mm = divmod(minutes, 60)
    return f"{(h % 12) or 12}:{mm:02d} {'AM' if h < 12 else 'PM'}"


def peak_hours_15min(df, cfg: AnalysisConfig = DEFAULT_ANALYSIS):
    """AM / PM / overall peak hour via a 15-minute sliding 60-minute window.

    Uses average weekday (Mon-Fri) volume per 15-min bin, then the busiest
    4-bin (60-min) window. Returns (am, pm, overall), each ``(label, volume)`` or
    None, where ``label`` is the window's start-end time and ``volume`` is the
    average weekday vehicles in that hour.
    """
    wd = df[df[TS].dt.dayofweek.isin(cfg.weekday_indices)]
    if wd.empty:
        return None, None, None
    n_days = wd[TS].dt.normalize().nunique() or 1
    bins = wd[TS].dt.hour * 4 + wd[TS].dt.minute // 15
    counts = bins.value_counts()
    avg = np.array([counts.get(b, 0) / n_days for b in range(96)], dtype=float)
    win = np.convolve(avg, np.ones(4), mode="valid")   # win[b] = 60-min window starting at 15-min bin b (0..92)

    def peak(lo, hi):
        seg = win[lo:hi]
        if len(seg) == 0 or seg.max() == 0:
            return None
        b = lo + int(np.argmax(seg))
        return (f"{_fmt_clock(b * 15)} - {_fmt_clock(b * 15 + 60)}", float(win[b]))

    return peak(0, 48), peak(48, 93), peak(0, 93)


def design_dates_for(dates, cfg: AnalysisConfig = DEFAULT_ANALYSIS) -> list:
    """Dates that feed the speed/percentile distribution.

    Default ("weekday"): the true Mon-Fri days in the window. Legacy ("first"):
    the first ``study_days - 2`` calendar days from the window start (what the Excel
    actually did, which mislabeled weekend days as weekday for non-Monday starts).
    """
    ordered = sorted(pd.Timestamp(d) for d in dates)
    if getattr(cfg, "percentile_window", "weekday") == "first":
        return ordered[: max(1, cfg.study_days - 2)]
    weekdays = [d for d in ordered if d.dayofweek in cfg.weekday_indices]
    return weekdays or ordered   # fall back to all days if the window has no weekdays


def pace_interval(counts_by_speed: dict[int, int], width: int, max_speed_bin: int = 100):
    """The ``width``-mph window holding the most vehicles (e.g. 10-MPH pace)."""
    speeds = np.arange(0, max_speed_bin)
    cnt = np.array([counts_by_speed.get(int(s), 0) for s in speeds], dtype=float)
    if cnt.sum() == 0:
        return None
    window = np.convolve(cnt, np.ones(width + 1, dtype=float), mode="valid")
    lo = int(np.argmax(window))
    total = cnt.sum()
    return {"low": lo, "high": lo + width,
            "count": int(window[lo]), "pct": float(window[lo] / total * 100.0)}


@dataclass
class DirectionMetrics:
    """All statistics/tables for one direction set (Merged / Incoming / Outgoing)."""

    label: str
    speed_limit: float
    n_days: int
    total: int = 0
    avg_speed: float = float("nan")
    median_speed: float = float("nan")
    max_speed: float = float("nan")
    max_speed_when: Optional[pd.Timestamp] = None
    adt: float = float("nan")
    avg_weekday_traffic: float = float("nan")
    design_pct: float = 0.85
    design_speed: float = float("nan")
    pct_table: list = field(default_factory=list)        # (pct, speed, excess)
    speed_pct_table: list = field(default_factory=list)   # (speed, percentile)
    pace: Optional[dict] = None
    over_limit_count: int = 0
    over_limit_pct: float = float("nan")
    daily_totals: dict = field(default_factory=dict)      # weekday name -> count
    daily_sequence: list = field(default_factory=list)    # [(weekday name, count)] chronological (Day1..Day7)
    hourly_volume: Optional[pd.DataFrame] = None          # 24 x (dates + aggregates)
    hourly_speed: Optional[pd.DataFrame] = None           # mean speed 24 x (dates + Average aggregates)
    hourly_p85: Optional[pd.DataFrame] = None             # 85th %ile 24 x (dates + Overall aggregates)
    hourly_weekday_p85: Optional[pd.Series] = None        # per-hour weekday (first-5-day) 85th
    am_peak: Optional[tuple] = None                       # (hour_label, avg weekday veh in the 60-min window)
    pm_peak: Optional[tuple] = None
    peak_hour: Optional[tuple] = None                     # overall busiest 60-min window (for trend table)
    class_counts: dict = field(default_factory=dict)
    class_pct: dict = field(default_factory=dict)
    direction_counts: dict = field(default_factory=dict)

    def summary(self) -> dict:
        """Flat scalar summary (used for validation tables & report headers)."""
        return {
            "label": self.label, "total_vehicles": self.total,
            "average_speed": self.avg_speed, "median_speed": self.median_speed,
            "max_speed": self.max_speed, "adt": self.adt,
            "average_weekday_traffic": self.avg_weekday_traffic,
            f"p{int(self.design_pct * 100)}_speed": self.design_speed,
            "over_limit_pct": self.over_limit_pct,
            "am_peak": self.am_peak[0] if self.am_peak else None,
            "pm_peak": self.pm_peak[0] if self.pm_peak else None,
        }


def compute_direction_metrics(df: pd.DataFrame, label: str, speed_limit: float,
                              cfg: AnalysisConfig = DEFAULT_ANALYSIS,
                              n_days: Optional[int] = None,
                              design_dates: Optional[list] = None) -> DirectionMetrics:
    """Compute the full statistics block for a (possibly direction-filtered) window.

    ``n_days`` is the divisor for ADT (Excel's "Number of Days"). When omitted it
    defaults to the number of distinct calendar days in ``df``.

    ``design_dates`` is the set of dates used for the speed/percentile distribution.
    The legacy report uses the FIRST ``study_days - 2`` calendar days of the window
    (Day1..Day5 = the "weekday" portion, position-based, NOT Mon-Fri by name). Pass
    the shared window dates so per-direction percentiles match the Excel exactly.
    """
    m = DirectionMetrics(label=label, speed_limit=speed_limit, n_days=cfg.study_days,
                         design_pct=cfg.design_percentile)
    if df.empty:   # e.g. an absent direction in a single-direction study
        m.total = 0
        m.adt = 0.0
        m.max_speed = 0.0
        return m

    df = df.copy()
    df["date"] = df[TS].dt.normalize()
    df["dow"] = df[TS].dt.dayofweek
    df["hour"] = df[TS].dt.hour

    actual_days = n_days if n_days is not None else int(df["date"].nunique())
    m.n_days = actual_days
    m.total = int(len(df))
    m.avg_speed = float(df[SPEED].mean())
    m.median_speed = float(df[SPEED].median())
    m.max_speed = float(df[SPEED].max())
    m.max_speed_when = df.loc[df[SPEED].idxmax(), TS]
    m.adt = m.total / actual_days if actual_days else float("nan")

    # Daily totals keyed by weekday name (one date per weekday in a clean week).
    per_date = df.groupby("date").size()
    dow_of_date = {d: d.dayofweek for d in per_date.index}
    daily_by_dow: dict[int, list] = {}
    for d, c in per_date.items():
        daily_by_dow.setdefault(dow_of_date[d], []).append(int(c))
    m.daily_totals = {DOW_NAMES[dw]: sum(v) for dw, v in sorted(daily_by_dow.items())}
    # Day sequence ALWAYS ordered Monday..Sunday (not by collection start day), so
    # every study reads Mon -> Sun in tables, figures, and summaries.
    m.daily_sequence = [(DOW_NAMES[dw], int(sum(v))) for dw, v in sorted(daily_by_dow.items())]

    # Average Weekday Traffic is NAME-based (Mon-Fri daily totals); this matches Excel.
    weekday_totals = [c for dw, lst in daily_by_dow.items() if dw in cfg.weekday_indices for c in lst]
    m.avg_weekday_traffic = float(np.mean(weekday_totals)) if weekday_totals else float("nan")

    # Speed/percentile distribution days. Default = true Mon-Fri weekdays in the
    # window (corrected); legacy "first N calendar days" available via cfg. Callers
    # may pass design_dates explicitly so all directions share the same set.
    if design_dates is None:
        design_dates = design_dates_for(sorted(df["date"].unique()), cfg)
    design_df = df[df["date"].isin(pd.to_datetime(list(design_dates)))]
    cbs = _counts_by_speed(design_df[SPEED]) if not design_df.empty else {}
    pcts = [90, 85, 80, 70, 60, 50, 40, 30, 20, 10]
    m.pct_table = []
    for p in pcts:
        sp = percentile_speed(cbs, p, cfg.max_speed_bin)
        excess = max(0.0, sp - speed_limit) if sp is not None else None
        m.pct_table.append((p, sp, excess))
    m.design_speed = percentile_speed(cbs, cfg.design_percentile * 100, cfg.max_speed_bin)

    # Speed -> percentile (inverse table, for the percentile curve).
    n_wd = sum(cbs.values()) or 1
    speeds_grid = np.arange(1, cfg.max_speed_bin)
    cnt = np.array([cbs.get(int(s), 0) for s in speeds_grid], dtype=float)
    cum_lt = np.concatenate(([0.0], np.cumsum(cnt)[:-1]))
    g = cum_lt / n_wd * 100.0
    for sp in [50, 45, 40, 35, 30, 25, 20, 15, 10, 5]:
        if sp < len(speeds_grid):
            m.speed_pct_table.append((sp, float(g[sp])))

    m.pace = pace_interval(_counts_by_speed(df[SPEED]), cfg.pace_width, cfg.max_speed_bin)
    m.over_limit_count = int((df[SPEED] > speed_limit).sum())
    m.over_limit_pct = m.over_limit_count / m.total * 100.0 if m.total else float("nan")

    # Hourly x day matrices (volume + average speed), with aggregate columns.
    ordered_dates = sorted(per_date.index)
    vol = (df.pivot_table(index="hour", columns="date", values=SPEED, aggfunc="size")
             .reindex(index=range(24), columns=ordered_dates).fillna(0).astype(int))
    spd = (df.pivot_table(index="hour", columns="date", values=SPEED, aggfunc="mean")
             .reindex(index=range(24), columns=ordered_dates))
    m.hourly_volume = _add_aggregates(vol, ordered_dates, cfg)
    m.hourly_speed = _add_aggregates(spd, ordered_dates, cfg, mean=True)
    m.hourly_p85 = _hourly_p85_matrix(df, ordered_dates, cfg)

    # Per-hour weekday (first-5-day) design-percentile speed — Excel "Weekday 85th
    # Percentile" column: same cumulative-interpolation method, per hour, None if empty.
    p85_by_hour = {}
    for h in range(24):
        sp_h = design_df.loc[design_df["hour"] == h, SPEED] if not design_df.empty else design_df[SPEED]
        cbs_h = _counts_by_speed(sp_h) if len(sp_h) else {}
        p85_by_hour[h] = percentile_speed(cbs_h, cfg.design_percentile * 100, cfg.max_speed_bin)
    m.hourly_weekday_p85 = pd.Series(p85_by_hour, name="Weekday 85th %ile")

    # AM / PM / overall peak hour via a 15-minute sliding 60-minute window
    # (average weekday volume). Replaces the whole-clock-hour weekday-average method.
    m.am_peak, m.pm_peak, m.peak_hour = peak_hours_15min(df, cfg)

    if df[CLASS].notna().any():
        vc = df[CLASS].value_counts()
        m.class_counts = {k: int(v) for k, v in vc.items()}
        m.class_pct = {k: float(v / m.total * 100.0) for k, v in vc.items()}
    if df[DIRECTION].notna().any():
        m.direction_counts = {k: int(v) for k, v in df[DIRECTION].value_counts().items()}
    return m


def _add_aggregates(mat: pd.DataFrame, dates, cfg: AnalysisConfig, mean: bool = False) -> pd.DataFrame:
    """Append Average / Weekday Avg (Mon-Thu) / Weekend Avg, and order day columns Mon->Sun.

    Aggregates are computed from the raw date columns first; the day columns are then
    relabeled to weekday names and reordered Monday..Sunday so every study reads
    Mon -> Sun regardless of which weekday data collection started on.
    """
    out = mat.copy()
    dows = {d: d.dayofweek for d in dates}
    weekday_cols = [d for d in dates if dows[d] in (0, 1, 2, 3)]   # Mon-Thu (Excel quirk)
    weekend_cols = [d for d in dates if dows[d] in (5, 6)]

    avg = out[list(dates)].mean(axis=1) if len(dates) else np.nan
    wd = out[weekday_cols].mean(axis=1) if weekday_cols else np.nan
    we = out[weekend_cols].mean(axis=1) if weekend_cols else np.nan

    out = out.rename(columns={d: DOW_NAMES[dows[d]] for d in dates})
    day_order = [DOW_NAMES[i] for i in range(7) if DOW_NAMES[i] in out.columns]
    out = out[day_order]
    out["Average"] = avg
    out["Weekday Avg"] = wd
    out["Weekend Avg"] = we
    return out


def _hourly_p85_matrix(df: pd.DataFrame, dates, cfg: AnalysisConfig) -> pd.DataFrame:
    """Per hour x day design-percentile (85th) speed, plus pooled Overall /
    Weekday Overall / Weekend Overall columns.

    A percentile is itself an aggregating statistic, so the summary columns are the
    percentile of the POOLED speeds for that hour (over all days / Mon-Thu / Sat-Sun) —
    NOT the mean of the per-day percentiles, which would be meaningless. Day groupings
    match the mean-speed table (Weekday = Mon-Thu, Weekend = Sat-Sun).
    """
    pct = cfg.design_percentile * 100.0

    def p85(speeds: pd.Series):
        if len(speeds) == 0:
            return np.nan
        r = percentile_speed(_counts_by_speed(speeds), pct, cfg.max_speed_bin)
        return np.nan if r is None else r

    # Per (hour, day) cell.
    cell = (df.groupby(["hour", "date"])[SPEED].apply(p85)
              .unstack("date").reindex(index=range(24), columns=list(dates)))
    dows = {d: pd.Timestamp(d).dayofweek for d in dates}
    out = cell.rename(columns={d: DOW_NAMES[dows[d]] for d in dates})
    day_order = [DOW_NAMES[i] for i in range(7) if DOW_NAMES[i] in out.columns]
    out = out[day_order]

    # Pooled per-hour percentile over each day set.
    def pooled(mask) -> pd.Series:
        sub = df[mask]
        if sub.empty:
            return pd.Series(np.nan, index=range(24))
        return sub.groupby("hour")[SPEED].apply(p85).reindex(range(24))

    out["Overall"] = pooled(df["dow"].notna())
    out["Weekday Overall"] = pooled(df["dow"].isin((0, 1, 2, 3)))
    out["Weekend Overall"] = pooled(df["dow"].isin((5, 6)))
    return out
