"""Per-location statistics over time (across years) + a trend figure.

Groups every study of one location (all years) chronologically and reports the
headline metrics per study: ADT, AWDT, 85th & mean speed, overall D-factor, an
AADT placeholder, and the peak hour + its volume. Also answers "check the
D-factor across years" by trending it over the location's studies.
"""
from __future__ import annotations

from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import FIGURE_DPI, AnalysisConfig, DEFAULT_ANALYSIS
from .discovery import find_studies
from .pipeline import process_study

# Independent matplotlib entry point (this module does not import .figures), so
# it sets the shared raster resolution itself.
matplotlib.rcParams["figure.dpi"] = FIGURE_DPI
matplotlib.rcParams["savefig.dpi"] = FIGURE_DPI

COLUMNS = ["Study", "Date", "Status", "ADT", "AWDT", "85th Speed", "Mean Speed",
           "D-Factor", "AADT", "Peak Hour", "Peak Hour Vol"]


def _overall_dfactor(result) -> float:
    """Share of weekday traffic in the busier direction, or NaN if undefined.

    A D-factor only means something when BOTH directions were measured. Treating a
    missing direction as zero used to yield exactly 1.00 for one-way studies, which
    reads as a real 100/0 split rather than "not applicable" - and then fed the
    across-years D-factor comparison as if it were a measurement.
    """
    inc = result.metrics.get("Incoming")
    out = result.metrics.get("Outgoing")
    iw = inc.avg_weekday_traffic if inc else float("nan")
    ow = out.avg_weekday_traffic if out else float("nan")
    if iw != iw or ow != ow:          # either direction missing / NaN
        return float("nan")
    tot = iw + ow
    if tot <= 0:
        return float("nan")
    return max(iw, ow) / tot


def over_time_row(study, result, direction: str = "Merged") -> dict:
    # Per-direction metrics for the chosen view; falls back to Merged. D-Factor stays
    # the study-level overall split (it is inherently a between-directions metric).
    m = result.metrics.get(direction) or result.merged
    return {
        "Study": study.study_id,
        "Date": study.install_date.isoformat() if study.install_date else "",
        "Status": study.status,
        "ADT": round(m.adt, 1),
        "AWDT": round(m.avg_weekday_traffic, 1) if m.avg_weekday_traffic == m.avg_weekday_traffic else None,
        "85th Speed": round(m.design_speed, 2) if m.design_speed else None,
        "Mean Speed": round(m.avg_speed, 2) if m.avg_speed == m.avg_speed else None,
        "D-Factor": (lambda d: round(d, 3) if d == d else None)(_overall_dfactor(result)),
        "AADT": "",                       # placeholder — needs seasonal factors we don't have yet
        "Peak Hour": m.peak_hour[0] if m.peak_hour else None,
        "Peak Hour Vol": round(m.peak_hour[1]) if m.peak_hour else None,
    }


def over_time_table(base: str, location: str,
                    cfg: AnalysisConfig = DEFAULT_ANALYSIS,
                    direction: str = "Merged") -> pd.DataFrame:
    """One row per study of ``location`` (all years), sorted by install date.

    ``direction`` selects which set the volume/speed columns describe
    ("Merged" | "Incoming" | "Outgoing"); D-Factor stays the overall split.
    """
    studies = [s for s in find_studies(base) if s.location == location]
    studies.sort(key=lambda s: (s.install_date or date.min))
    rows = []
    for s in studies:
        try:
            result = process_study(s, cfg=cfg, run_diag=False)
            rows.append(over_time_row(s, result, direction))
        except Exception as e:
            rows.append({"Study": s.study_id,
                         "Date": s.install_date.isoformat() if s.install_date else "",
                         "Status": s.status, "AADT": "", "Peak Hour": f"ERROR: {str(e)[:40]}"})
    return pd.DataFrame(rows, columns=COLUMNS)


def fig_trend(table: pd.DataFrame, location: str, direction: str | None = None):
    """Two stacked panels sharing one time axis: Volume (ADT + AWDT columns) on top,
    Speed (85th %ile + mean lines) below. One tick per year.

    Separate panels (rather than one dual-axis plot) keep each metric on its own
    natural scale, so volume and speed can't be visually confused.

    ``direction`` (a display label such as "EB") is appended to the title when the
    table describes a single direction rather than the Merged view.
    """
    df = table.copy()
    df = df[df["Date"] != ""]
    x = pd.to_datetime(df["Date"], errors="coerce")
    xnum = mdates.date2num(x)
    adt = pd.to_numeric(df["ADT"], errors="coerce").to_numpy()
    awdt = pd.to_numeric(df["AWDT"], errors="coerce").to_numpy()
    p85 = pd.to_numeric(df["85th Speed"], errors="coerce").to_numpy()
    mean = pd.to_numeric(df["Mean Speed"], errors="coerce").to_numpy()

    # Bar-group width from the tightest spacing between studies so grouped columns
    # don't overlap when two studies fall close together.
    if len(xnum) >= 2:
        gaps = np.diff(np.sort(xnum)); gaps = gaps[gaps > 0]
        span = float(gaps.min()) if gaps.size else 180.0
    else:
        span = 180.0
    bw = min(span * 0.8, 220.0) / 2   # width of each of the two bars, in days

    fig, (axv, axs) = plt.subplots(2, 1, figsize=(8, 6.2), sharex=True)

    # Top panel: volume as grouped columns.
    axv.bar(xnum - bw / 2, adt, width=bw, color="#4f81bd", label="ADT")
    axv.bar(xnum + bw / 2, awdt, width=bw, color="#2ca25f", label="AWDT")
    axv.set_ylabel("Volume (veh/day)"); axv.set_ylim(bottom=0)
    axv.legend(fontsize=8, loc="best", ncol=2); axv.grid(True, axis="y", alpha=0.3)
    title = f"{location} — metrics over time"
    if direction and direction != "Merged":
        title += f" ({direction})"
    axv.set_title(title)

    # Bottom panel: speed as lines with markers. Headroom of +5 mph above the
    # highest speed point so the top line isn't pinned to the frame.
    axs.plot(xnum, p85, "-o", color="#e08214", label="85th %ile speed")
    axs.plot(xnum, mean, "-^", color="#d9534f", label="Mean speed")
    both = np.concatenate([p85, mean]) if len(xnum) else np.array([np.nan])
    smax = np.nanmax(both) if np.isfinite(both).any() else 0.0
    axs.set_ylabel("Speed (mph)"); axs.set_ylim(0, smax + 5 if smax > 0 else 5)
    axs.set_xlabel("Study date")
    axs.legend(fontsize=8, loc="best", ncol=2); axs.grid(True, alpha=0.3)

    # One tick per year for multi-year histories; but when the studies span < ~2
    # years, year boundaries are too sparse (a single study pair can leave just one
    # tick between the bars), so label each study's own date instead.
    axs.xaxis_date()
    span_days = float(xnum.max() - xnum.min()) if len(xnum) else 0.0
    if len(xnum) >= 2 and span_days >= 730:
        axs.xaxis.set_major_locator(mdates.YearLocator())
        axs.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    else:
        axs.set_xticks(sorted(xnum))
        axs.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    # Show x-tick labels on BOTH panels (sharex hides them on the top axis by
    # default, and fig.autofmt_xdate() would too — so rotate manually per axis).
    for a in (axv, axs):
        a.tick_params(axis="x", labelbottom=True)
        for lbl in a.get_xticklabels():
            lbl.set_rotation(30)
            lbl.set_horizontalalignment("right")
    fig.tight_layout()
    return fig
