"""Statistics & tables for a speed/volume study.

These follow standard practice rather than reproducing the legacy Excel workbook.
Where the workbook was simply wrong, this module is deliberately different; set
``config.LEGACY_ANALYSIS`` to reproduce the old numbers for comparison.

Methodology:
  * Average / Median / Max speed   -> all vehicles in the window.
  * Percentile speeds              -> the design-window distribution (true Mon-Fri
                                      by default), cumulative P(speed < s) over a
                                      grid covering every observed speed, linear
                                      interpolation between integer speeds.
  * ADT = total / days in window; Average Weekday Traffic = mean of Mon-Fri totals.
  * AM/PM/overall peak hour -> busiest 60-min (15-min sliding) window of average
    Mon-Fri volume; each peak carries its average weekday hourly volume.
  * Hourly tables: volume columns average the per-day counts; SPEED columns pool
    the underlying speeds rather than averaging per-day statistics. "Weekday"
    means Mon-Fri (the workbook used Mon-Thu and dropped Friday).
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


def _speed_grid(counts_by_speed: dict[int, int], max_speed_bin: int):
    """Integer speed grid covering EVERY observed speed, with its counts.

    The grid runs 0..max(max_speed_bin, highest observed speed), so no record is
    left out of the denominator. The old fixed 1..max_speed_bin-1 grid silently
    dropped speeds of 0 and >= 100 - eight real studies record 100-104 mph, and
    those vehicles were missing from every percentile while still being counted in
    max_speed, so the two statistics described different populations.
    """
    hi = max(int(max_speed_bin) - 1, max((int(s) for s in counts_by_speed), default=0))
    speeds = np.arange(0, hi + 1)
    cnt = np.array([counts_by_speed.get(int(s), 0) for s in speeds], dtype=float)
    return speeds, cnt


def _cumulative_pct(cnt: np.ndarray) -> np.ndarray:
    """G[i] = 100 * P(speed < speeds[i]), the cumulative used for interpolation."""
    n = cnt.sum()
    if n == 0:
        return np.zeros_like(cnt)
    return np.concatenate(([0.0], np.cumsum(cnt)[:-1])) / n * 100.0


def pct_below(counts_by_speed: dict[int, int], speed: int,
              max_speed_bin: int = 100) -> Optional[float]:
    """Percent of vehicles travelling STRICTLY BELOW ``speed`` (None if no data)."""
    speeds, cnt = _speed_grid(counts_by_speed, max_speed_bin)
    if cnt.sum() == 0:
        return None
    g = _cumulative_pct(cnt)
    idx = int(np.searchsorted(speeds, speed))
    if idx >= len(speeds):
        return 100.0
    return float(g[idx])


def percentile_speed(counts_by_speed: dict[int, int], target_pct: float,
                     max_speed_bin: int = 100) -> Optional[float]:
    """Speed at ``target_pct`` by cumulative interpolation over integer speeds.

    Builds F[s] = count(speed < s) on the integer grid, G[s] = F[s]/N*100, then for
    percentile P interpolates between the bracketing integer speeds:
    ``s + (P - G[s]) / (G[s+1] - G[s])``. Raw speeds are whole mph, so the integer
    grid is exact rather than a rounding approximation.
    """
    speeds, cnt = _speed_grid(counts_by_speed, max_speed_bin)
    n = cnt.sum()
    if n == 0:
        return None
    g = _cumulative_pct(cnt)
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
    """The ``width``-mph window holding the most vehicles (e.g. the 10-MPH pace).

    Covers exactly ``width`` consecutive integer speeds, reported inclusively as
    ``low``..``high`` (so a 10-mph pace reads e.g. 26-35, ten values). It previously
    convolved ``width + 1`` bins, making the "10-MPH pace" eleven speeds wide and
    overstating its share of traffic.
    """
    speeds, cnt = _speed_grid(counts_by_speed, max_speed_bin)
    total = cnt.sum()
    if total == 0 or width < 1:
        return None
    window = np.convolve(cnt, np.ones(width, dtype=float), mode="valid")
    lo = int(np.argmax(window))
    return {"low": int(speeds[lo]), "high": int(speeds[lo]) + width - 1,
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
    design_dates: list = field(default_factory=list)      # days feeding the percentile distribution
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
    hourly_weekday_p85: Optional[pd.Series] = None        # per-hour 85th over the design dates
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
    It defaults to ``design_dates_for(dates, cfg)`` — the true Mon-Fri days in the
    window, or the legacy "first ``study_days - 2`` calendar days" when
    ``cfg.percentile_window == "first"``. Callers pass it explicitly so every
    direction of one study shares the same day set.
    """
    m = DirectionMetrics(label=label, speed_limit=speed_limit, n_days=cfg.study_days,
                         design_pct=cfg.design_percentile)
    if df.empty:   # e.g. an absent direction in a single-direction study
        m.total = 0
        m.adt = 0.0
        m.max_speed = 0.0
        # Report the real window length, not the configured default, so an empty
        # direction does not claim a 7-day study the data never covered.
        if n_days is not None:
            m.n_days = n_days
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
    # Normalised once and kept on the metrics: the percentile curve is drawn from
    # this same list, so the curve and the design_speed marker on it cannot drift
    # apart the way they did while the figure re-derived its own day set.
    m.design_dates = list(pd.to_datetime(list(design_dates)))
    design_df = df[df["date"].isin(m.design_dates)]
    cbs = _counts_by_speed(design_df[SPEED]) if not design_df.empty else {}
    pcts = [90, 85, 80, 70, 60, 50, 40, 30, 20, 10]
    m.pct_table = []
    for p in pcts:
        sp = percentile_speed(cbs, p, cfg.max_speed_bin)
        excess = max(0.0, sp - speed_limit) if sp is not None else None
        m.pct_table.append((p, sp, excess))
    m.design_speed = percentile_speed(cbs, cfg.design_percentile * 100, cfg.max_speed_bin)

    # Speed -> percentile (inverse table, for the percentile curve). Uses
    # pct_below so the speed is looked up by VALUE; indexing the cumulative array
    # positionally used to return the figure for the next integer speed up.
    for sp in [50, 45, 40, 35, 30, 25, 20, 15, 10, 5]:
        below = pct_below(cbs, sp, cfg.max_speed_bin)
        if below is not None:
            m.speed_pct_table.append((sp, below))

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
    # Speed matrices pool the underlying speeds for their summary columns; volume
    # averages the per-day counts. Averaging per-day mean speeds would weight a day
    # with one vehicle the same as a day with two hundred.
    m.hourly_speed = _hourly_stat_matrix(
        df, ordered_dates, cfg, _mean_speed,
        summary_labels=("Average", "Weekday Avg", "Weekend Avg"))
    m.hourly_p85 = _hourly_stat_matrix(df, ordered_dates, cfg, _p85_speed(cfg))

    # Per-hour design-percentile speed over ``design_dates`` — the Excel "Weekday 85th
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


def _day_labels(dates) -> dict:
    """Map each date to its column label, keeping labels unique.

    Normally one date per weekday, so the label is just "Monday". A window longer
    than seven days repeats a weekday; those get the date appended ("Monday 01-12")
    instead of colliding into duplicate columns that break the table.
    """
    dows = {d: pd.Timestamp(d).dayofweek for d in dates}
    seen: dict[int, int] = {}
    for d in dates:
        seen[dows[d]] = seen.get(dows[d], 0) + 1
    return {d: (DOW_NAMES[dows[d]] if seen[dows[d]] == 1
                else f"{DOW_NAMES[dows[d]]} {pd.Timestamp(d):%m-%d}")
            for d in dates}


def _order_day_columns(out: pd.DataFrame, dates, labels: dict) -> pd.DataFrame:
    """Reorder the day columns Monday..Sunday (chronologically within a weekday)."""
    ordered = sorted(dates, key=lambda d: (pd.Timestamp(d).dayofweek, pd.Timestamp(d)))
    return out[[labels[d] for d in ordered]]


def _add_aggregates(mat: pd.DataFrame, dates, cfg: AnalysisConfig) -> pd.DataFrame:
    """Append Average / Weekday Avg / Weekend Avg to a per-day COUNT matrix.

    Averaging per-day counts is the right aggregate for volume. "Weekday" is
    ``cfg.hourly_weekdays`` - Mon-Fri by default; the legacy Excel used Mon-Thu,
    which dropped Friday from every hourly weekday average while the study-level
    AWDT on the same report used Mon-Fri, so the two disagreed.
    """
    out = mat.copy()
    dows = {d: pd.Timestamp(d).dayofweek for d in dates}
    weekday_cols = [d for d in dates if dows[d] in cfg.hourly_weekdays]
    weekend_cols = [d for d in dates if dows[d] in cfg.weekend_indices]

    avg = out[list(dates)].mean(axis=1) if len(dates) else np.nan
    wd = out[weekday_cols].mean(axis=1) if weekday_cols else np.nan
    we = out[weekend_cols].mean(axis=1) if weekend_cols else np.nan

    labels = _day_labels(dates)
    out = out.rename(columns=labels)
    out = _order_day_columns(out, dates, labels)
    out["Average"] = avg
    out["Weekday Avg"] = wd
    out["Weekend Avg"] = we
    return out


def _mean_speed(speeds: pd.Series) -> float:
    return float(speeds.mean()) if len(speeds) else np.nan


def _p85_speed(cfg: AnalysisConfig):
    """A per-hour design-percentile function bound to this config."""
    pct = cfg.design_percentile * 100.0

    def _fn(speeds: pd.Series) -> float:
        if len(speeds) == 0:
            return np.nan
        r = percentile_speed(_counts_by_speed(speeds), pct, cfg.max_speed_bin)
        return np.nan if r is None else r

    return _fn


def _hourly_stat_matrix(df: pd.DataFrame, dates, cfg: AnalysisConfig, statfn,
                        summary_labels=("Overall", "Weekday Overall", "Weekend Overall"),
                        ) -> pd.DataFrame:
    """Per hour x day SPEED statistic, plus pooled Overall / Weekday / Weekend columns.

    The summary columns apply ``statfn`` to the POOLED speeds for that hour, never to
    the per-day results. That is required for a percentile (the mean of per-day 85ths
    is not an 85th of anything) and equally required for the mean, where a day with
    one vehicle would otherwise weigh the same as a day with two hundred.

    "Weekday" is ``cfg.hourly_weekdays`` - Mon-Fri by default, Mon-Thu under
    LEGACY_ANALYSIS. ``summary_labels`` names the three summary columns; each table
    keeps the names its consumers already look up to place the group divider.
    """
    cell = (df.groupby(["hour", "date"])[SPEED].apply(statfn)
              .unstack("date").reindex(index=range(24), columns=list(dates)))
    labels = _day_labels(dates)
    out = _order_day_columns(cell.rename(columns=labels), dates, labels)

    def pooled(mask) -> pd.Series:
        sub = df[mask]
        if sub.empty:
            return pd.Series(np.nan, index=range(24))
        return sub.groupby("hour")[SPEED].apply(statfn).reindex(range(24))

    all_lbl, wd_lbl, we_lbl = summary_labels
    out[all_lbl] = pooled(df["dow"].notna())
    out[wd_lbl] = pooled(df["dow"].isin(cfg.hourly_weekdays))
    out[we_lbl] = pooled(df["dow"].isin(cfg.weekend_indices))
    return out
