"""Study-quality diagnostics: flag likely errors or impacts that compromise a study.

Each check yields zero or more ``Finding``s with a severity. ``run_diagnostics``
aggregates them and assigns an overall risk level. Thresholds are configurable
(``config.DiagnosticThresholds``). Validated qualitatively against the agency's
``_Compromised Studies`` folders (snow days, parking-induced imbalance, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

from .config import (CLASS, DIRECTION, SPEED, TS, DEFAULT_ANALYSIS,
                     DiagnosticThresholds, DEFAULT_THRESHOLDS, SOURCES)
from .sources import list_raw_files

if TYPE_CHECKING:
    from .study import StudyData

SEVERITY_ORDER = {"ok": 0, "info": 1, "warning": 2, "error": 3}


@dataclass
class Finding:
    category: str
    severity: str          # info | warning | error
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class DiagnosticReport:
    findings: list = field(default_factory=list)

    @property
    def risk(self) -> str:
        if any(f.severity == "error" for f in self.findings):
            return "high"
        if any(f.severity == "warning" for f in self.findings):
            return "moderate"
        return "low"

    @property
    def n_errors(self) -> int:
        return sum(f.severity == "error" for f in self.findings)

    @property
    def n_warnings(self) -> int:
        return sum(f.severity == "warning" for f in self.findings)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([{"category": f.category, "severity": f.severity,
                              "message": f.message, **f.detail} for f in self.findings])


# --------------------------------------------------------------------------- #
# Individual checks. Each takes (StudyData, thresholds) -> list[Finding].
# --------------------------------------------------------------------------- #
# Day-of-week (Mon=0) set for the standard 3-day mid-week count.
_STANDARD_TWT = [1, 2, 3]   # Tuesday, Wednesday, Thursday


def _check_completeness(sd, th):
    out = []
    n_days = len(sd.day_coverage)
    complete = [c for c in sd.day_coverage if c.coverage >= th.min_hourly_coverage]
    win = sd.window
    win_dates = sorted(win[TS].dt.normalize().unique()) if not win.empty else []
    win_days = len(win_dates)
    win_dows = sorted({int(pd.Timestamp(d).dayofweek) for d in win_dates})

    if win_days == 3 and win_dows == _STANDARD_TWT:
        # A Tuesday–Thursday count is the standard representative short study — not a
        # risk. (Length is fine; other checks still judge data quality separately.)
        out.append(Finding("standard_short_count", "info",
                           "Standard 3-day count (Tuesday–Thursday).",
                           {"window_days": 3}))
    elif win_days == 3:
        # 3 days that are NOT Tue–Thu are not a valid short count -> high risk.
        names = ", ".join(pd.Timestamp(d).day_name() for d in win_dates)
        out.append(Finding("insufficient_days", "error",
                           f"3-day window is not a standard Tuesday–Thursday count "
                           f"(covers {names}).",
                           {"window_days": 3, "days": names}))
    else:
        if sd.selection_note and "Only" in sd.selection_note:
            out.append(Finding("insufficient_days", "error", sd.selection_note,
                                {"complete_days": len(complete), "available_days": n_days}))
        if win_days < 7:
            out.append(Finding("insufficient_days", "warning",
                                f"Window has {win_days} day(s); a standard study needs 7.",
                                {"window_days": win_days}))
    for c in sd.day_coverage:
        if c.coverage < th.min_hourly_coverage:
            out.append(Finding("incomplete_day", "info",
                               f"{c.day}: only {c.hours_present}/24 hours have data.",
                               {"day": str(c.day), "hours_present": c.hours_present}))
    return out


def _check_gaps(sd, th):
    """Flag gaps during normally-active hours (quiet nighttime gaps are expected)."""
    out = []
    if sd.window.empty:
        return out
    ts = sd.window[TS].sort_values().reset_index(drop=True)
    gaps = ts.diff().dropna()
    if gaps.empty:
        return out
    thresh = pd.Timedelta(hours=th.max_gap_hours)
    concerning = []
    for i in range(1, len(ts)):
        gap = ts[i] - ts[i - 1]
        if gap <= thresh:
            continue
        # Daytime outage if the gap starts in active hours or is long enough to span them.
        start_h = ts[i - 1].hour
        if 5 <= start_h <= 21 or gap >= pd.Timedelta(hours=6):
            concerning.append((ts[i - 1], gap))
    if concerning:
        worst = max(concerning, key=lambda g: g[1])
        out.append(Finding("data_gap", "warning",
                           f"{len(concerning)} daytime gap(s) > {th.max_gap_hours}h; "
                           f"largest {worst[1].total_seconds()/3600:.1f}h after {worst[0]}.",
                           {"n_gaps": len(concerning),
                            "max_gap_hours": round(worst[1].total_seconds()/3600, 2)}))
    return out


def _check_volume(sd, th):
    out = []
    if sd.window.empty:
        out.append(Finding("no_data", "error", "No vehicles in the selected window.", {}))
        return out
    per_day = sd.window.groupby(sd.window[TS].dt.normalize()).size()
    adt = per_day.mean()
    if adt < th.low_volume_per_day:
        out.append(Finding("low_volume", "warning",
                           f"Low average daily volume ({adt:.0f} veh/day).",
                           {"adt": round(float(adt), 1)}))
    if len(per_day) >= 2 and per_day.min() > 0:
        ratio = per_day.max() / per_day.min()
        if ratio > th.max_daily_volume_ratio:
            out.append(Finding("erratic_volume", "warning",
                               f"Daily volume swings {ratio:.1f}x "
                               f"({int(per_day.min())}-{int(per_day.max())}).",
                               {"ratio": round(float(ratio), 2),
                                "min_day": int(per_day.min()), "max_day": int(per_day.max())}))
    return out


def _check_adt_vs_awdt(sd, th):
    """Flag when ADT exceeds Average Weekday Traffic.

    Weekday volume is normally >= the all-days average, so AWDT >= ADT. ADT > AWDT
    means weekends are busier than weekdays — verify before trusting the weekday
    AWDT used by the D-factor (recreational route, or a data/direction problem).
    """
    out = []
    w = sd.window
    if w.empty:
        return out
    dates = w[TS].dt.normalize()
    per_date = w.groupby(dates).size()
    adt = float(per_date.mean()) if len(per_date) else 0.0
    wk = w[w[TS].dt.dayofweek.isin(DEFAULT_ANALYSIS.weekday_indices)]
    if wk.empty:
        return out
    awdt = float(wk.groupby(wk[TS].dt.normalize()).size().mean())
    if adt > awdt:
        out.append(Finding("adt_exceeds_awdt", "warning",
                           f"ADT ({adt:.0f}) exceeds Average Weekday Traffic ({awdt:.0f}) — "
                           f"weekend volume is higher than weekday; verify the data and the "
                           f"direction split before relying on the weekday D-factor.",
                           {"adt": round(adt, 1), "awdt": round(awdt, 1)}))
    return out


def _check_direction_balance(sd, th):
    out = []
    if sd.window.empty or sd.window[DIRECTION].notna().sum() == 0:
        return out
    counts = sd.window[DIRECTION].value_counts()
    if len(counts) < 2:
        out.append(Finding("single_direction", "info",
                           "Only one travel direction recorded.",
                           {"direction": str(counts.index[0])}))
        return out
    share = counts.max() / counts.sum()
    if share > th.max_direction_share:
        out.append(Finding("direction_imbalance", "warning",
                           f"Directional imbalance: {counts.idxmax()} is "
                           f"{share*100:.0f}% of traffic.",
                           {"share": round(float(share), 3),
                            "counts": counts.to_dict()}))
    return out


def _check_speed_outliers(sd, th):
    """Implausible speeds, escalated when they are outside the counter's spec.

    Two questions, deliberately answered by ONE finding. How much of the traffic
    sits at unusual speeds ([speed_min, speed_max], default 5-90 mph) decides
    info vs warning by share. Whether any reading is outside the instrument's
    RATED range ([device_speed_min, device_speed_max], 1.3-100 mph for the
    Armadillo Tracker) forces a warning regardless of share, because such a value
    cannot be a measured vehicle at all.

    These were briefly two separate checks, which double-reported: the rated range
    strictly contains the outlier range, so anything beyond 100 mph is necessarily
    also beyond 90 and every device-range violation appeared twice on the report.
    """
    out = []
    if sd.window.empty:
        return out
    sp = sd.window[SPEED].dropna()
    if sp.empty:
        return out
    lo = int((sp < th.speed_min).sum())
    hi = int((sp > th.speed_max).sum())
    if not (lo or hi):
        return out

    # Out of the instrument's specification - artifacts, not slow/fast vehicles.
    dev_lo = int((sp < th.device_speed_min).sum())
    dev_hi = int((sp > th.device_speed_max).sum())
    n_dev = dev_lo + dev_hi

    share = (lo + hi) / len(sp)
    severity = "warning" if (n_dev or share >= 0.01) else "info"

    msg = f"{lo} speeds < {th.speed_min:g} mph, {hi} > {th.speed_max:g} mph."
    if n_dev:
        parts = []
        if dev_hi:
            parts.append(f"{dev_hi} above the rated {th.device_speed_max:g} mph "
                         f"maximum (highest {sp.max():.0f})")
        if dev_lo:
            parts.append(f"{dev_lo} below the rated {th.device_speed_min:g} mph "
                         f"minimum (lowest {sp.min():.0f})")
        msg += (f" {'; '.join(parts).capitalize()} - outside the counter's rated "
                f"measurement range, so those are instrument artifacts rather than "
                f"measured vehicles.")

    out.append(Finding("speed_outliers", severity, msg,
                       {"below": lo, "above": hi,
                        "pct": round(share * 100, 2),
                        "out_of_device_range": n_dev,
                        "above_device_max": dev_hi, "below_device_min": dev_lo,
                        "max_speed": float(sp.max())}))
    return out


def _check_classes(sd, th):
    out = []
    if sd.window.empty or sd.window[CLASS].notna().sum() == 0:
        return out
    vc = sd.window[CLASS].value_counts(normalize=True)
    large = float(vc.get("Large", 0.0))
    if large > 0.15:
        out.append(Finding("class_distribution", "warning",
                           f"Unusually high heavy-vehicle share ({large*100:.0f}% Large).",
                           {"large_pct": round(large * 100, 1)}))
    if len(vc) == 1:
        out.append(Finding("class_distribution", "info",
                           f"All vehicles classified as {vc.index[0]}.", {}))
    return out


def _check_timestamps(sd, th):
    out = []
    if sd.raw.empty:
        return out
    ts = sd.raw[TS]
    # Minute-resolution timestamps make same-minute distinct vehicles look identical,
    # so only flag when an implausibly large share are exact repeats (possible doubling).
    dups = int(sd.raw.duplicated(subset=[TS, SPEED, CLASS, DIRECTION]).sum())
    frac = dups / len(sd.raw) if len(sd.raw) else 0.0
    if frac > 0.40:
        out.append(Finding("duplicate_records", "warning",
                           f"{dups} ({frac*100:.0f}%) fully-identical rows — possible "
                           f"data doubling.", {"duplicates": dups, "fraction": round(frac, 3)}))
    if not ts.is_monotonic_increasing:
        out.append(Finding("unordered_timestamps", "info",
                           "Raw timestamps are not in chronological order.", {}))
    return out


def _check_files(sd, th):
    out = []
    spec = SOURCES.get(sd.study.source_name)
    if spec is None:
        return out
    raws = list_raw_files(sd.study.path, spec)
    if len(raws) > 1:
        import os
        names = [os.path.basename(r) for r in raws]
        out.append(Finding("stray_raw_file", "warning",
                           f"{len(raws)} raw files in folder; expected 1.",
                           {"files": names}))
    return out


def _check_notes(sd, th):
    """Surface technician quality flags written in _Notes.txt."""
    out = []
    keywords = ("snow", "ice", "imbalance", "inbalance", "construction", "closed",
                "closure", "rain", "flood", "incomplete", "error", "parking", "check")
    for flag in sd.notes.get("flags", []):
        if any(k in flag.lower() for k in keywords):
            out.append(Finding("technician_note", "warning",
                               f"Technician note: {flag}", {"note": flag}))
    return out


_CHECKS = [_check_completeness, _check_gaps, _check_volume, _check_adt_vs_awdt,
           _check_direction_balance, _check_speed_outliers, _check_classes,
           _check_timestamps, _check_files, _check_notes]


def run_diagnostics(sd: "StudyData",
                    thresholds: DiagnosticThresholds = DEFAULT_THRESHOLDS) -> DiagnosticReport:
    """Run all checks against a loaded study and return an aggregated report."""
    findings: list = []
    for check in _CHECKS:
        try:
            findings.extend(check(sd, thresholds))
        except Exception as e:   # a broken check must not sink the whole report
            findings.append(Finding("diagnostic_error", "info",
                                     f"Check {check.__name__} failed: {e}", {}))
    findings.sort(key=lambda f: -SEVERITY_ORDER[f.severity])
    return DiagnosticReport(findings=findings)
