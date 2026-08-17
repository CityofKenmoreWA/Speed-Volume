"""Matplotlib figures reproducing the legacy Excel report charts (headless / Agg).

The five charts and their data sources match the workbook exactly (verified from
the chart definitions in the ``* Report`` sheets):

  * "Percentile Speed"                         scatter: weekday speed vs P(speed<s)*100,
                                               with vertical line at the 85th-pct speed
                                               and a horizontal line at 85%.
  * "Weekday 85th Percentile Speed Distribution" bar: per-hour weekday 85th percentile.
  * "Time Distribution"                        bar: average hourly volume (Σ/days).
  * "Daily Distribution"                       bar: daily totals Day1..Day7 (weekday labels).
  * "Speed Distribution"                       bar: all-window counts in 5-mph bins [lo,hi).

Bar charts carry value labels above the bars, matching the report.
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")  # safe for servers / Streamlit / batch
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

from .config import SPEED, TS, AnalysisConfig, DEFAULT_ANALYSIS
from .metrics import DirectionMetrics, DOW_NAMES, HOUR_LABELS

_BAR = "#4f81bd"      # Excel default blue
_LINE = "#4f81bd"
_WEEKEND = "#e08214"  # weekend bars (orange)


def _weekday_speed_counts(window, cfg):
    """Per-integer-speed counts for the weekday (first study_days-2 days) distribution."""
    d = window.copy()
    d["date"] = d[TS].dt.normalize()
    design = sorted(d["date"].unique())[: max(1, cfg.study_days - 2)]
    sp = d.loc[d["date"].isin(design), SPEED].round().astype(int)
    speeds = np.arange(1, cfg.max_speed_bin)
    cnt = np.array([(sp == s).sum() for s in speeds], dtype=float)
    return speeds, cnt


def _bar_labels(ax, bars, fmt):
    try:
        ax.bar_label(bars, fmt=fmt, fontsize=6, padding=1)
    except Exception:  # very old matplotlib
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt % b.get_height(),
                    ha="center", va="bottom", fontsize=6)


def fig_percentile_speed(window, m: DirectionMetrics, cfg=DEFAULT_ANALYSIS):
    speeds, cnt = _weekday_speed_counts(window, cfg)
    n = cnt.sum()
    fig, ax = plt.subplots(figsize=(8, 4))
    if n > 0:
        cum_lt = np.concatenate(([0.0], np.cumsum(cnt)[:-1])) / n * 100.0  # P(speed < s)
        ax.plot(speeds, cum_lt, color=_LINE, lw=1.6)
        if m.design_speed:
            ax.axhline(85, color="black", lw=1)
            ax.axvline(m.design_speed, color="red", lw=1.5)
            ax.plot([m.design_speed], [85], "o", color="red", ms=5,
                    label=f"85th pct = {m.design_speed:.1f} mph")
            ax.legend(fontsize=8, loc="lower right")
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    ax.set_xlabel("Speed (mph)"); ax.set_ylabel("Percentile (%)")
    ax.set_title("Percentile Speed")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def fig_weekday_p85(m: DirectionMetrics, cfg=DEFAULT_ANALYSIS):
    """Per-hour weekday 85th-percentile speed as a LINE chart (per supervisor)."""
    fig, ax = plt.subplots(figsize=(9, 4))
    vals = [None if (m.hourly_weekday_p85 is None or m.hourly_weekday_p85.get(h) is None
                     or np.isnan(m.hourly_weekday_p85.get(h))) else float(m.hourly_weekday_p85.get(h))
            for h in range(24)]
    y = np.array([np.nan if v is None else v for v in vals], dtype=float)
    ax.plot(range(24), y, "-o", color=_LINE, ms=4, lw=1.6)
    if m.speed_limit:
        ax.axhline(m.speed_limit, color="#444", ls="--", lw=1, label=f"Limit = {m.speed_limit:g}")
        ax.legend(fontsize=8)
    for h, v in enumerate(vals):
        if v is not None:
            ax.annotate(f"{v:.1f}", (h, v), textcoords="offset points", xytext=(0, 4),
                        ha="center", fontsize=6)
    ax.set_xticks(range(24)); ax.set_xticklabels(HOUR_LABELS, rotation=90, fontsize=6)
    ax.set_ylabel("Percentile Speed (MPH)"); ax.set_xlabel("Time")
    # Robust y-range: 0 baseline + ~15% headroom above the tallest point/limit so the
    # value labels are never clipped.
    finite = [v for v in vals if v is not None]
    top = (max(finite + [m.speed_limit]) if finite else m.speed_limit) * 1.15
    ax.set_ylim(0, top)
    ax.set_title("Weekday 85th Percentile Speed Distribution")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    return fig


def hourly_weekday_awdt(m: DirectionMetrics):
    """Per-hour average weekday (Mon-Fri) volume for one direction — the hourly AWDT."""
    hv = m.hourly_volume
    if hv is None:
        return np.zeros(24)
    cols = [d for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday") if d in hv.columns]
    if not cols:
        return np.zeros(24)
    s = hv[cols].mean(axis=1)
    return np.array([float(s.get(h, 0.0)) for h in range(24)])


def fig_dfactor(metrics: dict, cfg=DEFAULT_ANALYSIS, dir_names: dict | None = None):
    """Back-to-back hourly AWDT by direction + hourly D-factor.

    The direction with the larger total weekday volume is the primary (plotted
    upward); the other is plotted downward. D-factor(hour) = primary / (primary +
    secondary), 0..1, using the primary as the major-direction reference.
    """
    dir_names = dir_names or {}
    inc = hourly_weekday_awdt(metrics["Incoming"]) if "Incoming" in metrics else np.zeros(24)
    out = hourly_weekday_awdt(metrics["Outgoing"]) if "Outgoing" in metrics else np.zeros(24)
    if inc.sum() >= out.sum():
        pname, sname, pv, sv = "Incoming", "Outgoing", inc, out
    else:
        pname, sname, pv, sv = "Outgoing", "Incoming", out, inc
    pn = f"{dir_names.get(pname) or pname}"
    sn = f"{dir_names.get(sname) or sname}"

    # Drop hours with no traffic in either direction so empty overnight slots don't
    # pad the axis (purely cosmetic; if every hour has data nothing is dropped).
    keep = np.where((pv + sv) > 0)[0]
    if keep.size == 0:
        keep = np.arange(24)
    pvk, svk = pv[keep], sv[keep]
    pos = np.arange(len(keep))

    fig, ax = plt.subplots(figsize=(9, 5.4))
    ax.bar(pos, pvk, color="#4f81bd", label=f"{pn} (primary, ▲)")
    ax.bar(pos, -svk, color="#e08214", label=f"{sn} (▼)")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(pos); ax.set_xticklabels([HOUR_LABELS[h] for h in keep], rotation=90, fontsize=6)
    ax.set_ylabel(f"Avg weekday volume (veh/hr)\n▲ {pn} (above)   ▼ {sn} (below)")
    ax.set_xlabel("Time")
    ymax = max(pvk.max(initial=0), svk.max(initial=0), 1.0)
    ax.set_ylim(-ymax * 1.15, ymax * 1.15)
    # Show both halves as positive volumes (the sign only encodes direction).
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{abs(v):.0f}"))

    totk = pvk + svk
    with np.errstate(invalid="ignore", divide="ignore"):
        d = np.where(totk > 0, pvk / totk, np.nan)
    ax2 = ax.twinx()
    ax2.plot(pos, d, "o-", color="#222", ms=3, lw=1.2, label="D-factor")
    # Center 0.5 on the volume zero-line: use the SAME ±15% headroom as the left axis
    # (symmetric about 0.5) so 0.5 sits at the vertical middle and 0.4/0.6 are
    # equidistant from it. (0,1.05) put the midpoint at 0.525 and skewed the ticks.
    ax2.set_ylim(0.5 - 0.575, 0.5 + 0.575)   # = (-0.075, 1.075)
    ax2.set_yticks(np.arange(0.0, 1.01, 0.2))
    ax2.set_ylabel("D-Factor (0–1)")

    overall = pv.sum() / (pv.sum() + sv.sum()) if (pv.sum() + sv.sum()) > 0 else float("nan")
    # Legend BELOW the plot so it never overlaps the bars or the D-factor line.
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, ncol=3, frameon=False,
              loc="upper center", bbox_to_anchor=(0.5, -0.42))
    ax.set_title(f"D-Factor by Time of Day  (primary {pn}, overall D = {overall:.2f})")
    fig.subplots_adjust(bottom=0.40, top=0.92, left=0.09, right=0.91)
    return fig


def fig_time_distribution(m: DirectionMetrics, cfg=DEFAULT_ANALYSIS):
    fig, ax = plt.subplots(figsize=(9, 4))
    if m.hourly_volume is not None:
        vals = [float(m.hourly_volume["Average"].get(h, 0.0)) for h in range(24)]
    else:
        vals = [0.0] * 24
    bars = ax.bar(range(24), vals, color=_BAR)
    _bar_labels(ax, bars, "%.0f")
    ax.set_xticks(range(24)); ax.set_xticklabels(HOUR_LABELS, rotation=90, fontsize=6)
    ax.set_ylabel("Average Volume"); ax.set_xlabel("Time")
    ax.set_ylim(0, (max(vals) if vals else 1) * 1.18 or 1)   # headroom so bar labels aren't clipped
    ax.set_title("Time Distribution")
    fig.tight_layout()
    return fig


def fig_daily_distribution(m: DirectionMetrics, cfg=DEFAULT_ANALYSIS):
    fig, ax = plt.subplots(figsize=(8, 4))
    seq = m.daily_sequence or [(d, m.daily_totals.get(d, 0)) for d in DOW_NAMES if d in m.daily_totals]
    labels = [d for d, _ in seq]
    vals = [c for _, c in seq]
    # Weekend bars (Saturday/Sunday) get a distinct color.
    colors = [_WEEKEND if d in ("Saturday", "Sunday") else _BAR for d in labels]
    bars = ax.bar(range(len(vals)), vals, color=colors)
    _bar_labels(ax, bars, "%.0f")
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("Vehicle Counts"); ax.set_xlabel("Days")
    ax.set_ylim(0, (max(vals) if vals else 1) * 1.12 or 1)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=_BAR, label="Weekday"), Patch(color=_WEEKEND, label="Weekend")],
              fontsize=8, loc="upper right")
    ax.set_title("Daily Distribution")
    fig.tight_layout()
    return fig


def fig_speed_distribution(window, m: DirectionMetrics, cfg=DEFAULT_ANALYSIS):
    """All-window vehicle counts in 5-mph bins [lo, hi), 0..100."""
    fig, ax = plt.subplots(figsize=(9, 4))
    edges = np.arange(0, cfg.max_speed_bin + 5, 5)   # 0,5,...,100
    sp = window[SPEED].dropna().to_numpy()
    counts, _ = np.histogram(sp, bins=edges)
    labels = [f"{int(edges[i])}-{int(edges[i+1])}" for i in range(len(edges) - 1)]
    bars = ax.bar(range(len(counts)), counts, color=_BAR)
    _bar_labels(ax, bars, "%.0f")
    # Red vertical line at the posted speed limit. Bar i spans speeds [5i, 5i+5); the
    # limit value maps to x = limit/5 - 0.5 (a bin boundary sits at the bar's left edge).
    if m.speed_limit:
        ax.axvline(m.speed_limit / 5.0 - 0.5, color="red", lw=1.6,
                   label=f"Speed limit = {m.speed_limit:g}")
        ax.legend(fontsize=8)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, rotation=90, fontsize=6)
    ax.set_ylabel("Vehicle Counts"); ax.set_xlabel("Speed (mph)")
    ax.set_title("Speed Distribution")
    fig.tight_layout()
    return fig


def build_figures(window, m: DirectionMetrics, cfg: AnalysisConfig = DEFAULT_ANALYSIS) -> dict:
    """The five report figures for one direction set, keyed by short name."""
    return {
        "percentile_speed": fig_percentile_speed(window, m, cfg),
        "weekday_p85": fig_weekday_p85(m, cfg),
        "time_distribution": fig_time_distribution(m, cfg),
        "daily_distribution": fig_daily_distribution(m, cfg),
        "speed_distribution": fig_speed_distribution(window, m, cfg),
    }


def save_figures(figs: dict, outdir: str, prefix: str = "") -> dict:
    """Save figures to PNG; returns {name: path}. Does not close the figures."""
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    for name, fig in figs.items():
        path = os.path.join(outdir, f"{prefix}{name}.png")
        fig.savefig(path, dpi=120, bbox_inches="tight")
        paths[name] = path
    return paths
