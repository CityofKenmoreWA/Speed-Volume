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
    df = df[x.notna()]
    x = x[x.notna()].sort_values()
    df = df.loc[x.index]
    adt = pd.to_numeric(df["ADT"], errors="coerce").to_numpy()
    awdt = pd.to_numeric(df["AWDT"], errors="coerce").to_numpy()
    p85 = pd.to_numeric(df["85th Speed"], errors="coerce").to_numpy()
    mean = pd.to_numeric(df["Mean Speed"], errors="coerce").to_numpy()

    # One evenly-spaced slot per study, rather than placing each study at its true
    # date on a time axis.
    #
    # Studies are not spread evenly: a location will have two counts a week apart
    # inside a history spanning years. On a real time axis the bar width has to be
    # small enough that the closest pair does not overlap, which then applies to
    # every bar on the chart — at 80thAv_no_186thSt a 7-day gap across a 1530-day
    # span left each bar 0.18% of the axis wide, a hairline you could not read a
    # value off. 26 of the 172 multi-study locations have a gap under a month, so
    # no single width fixes it: whatever is wide enough to read overlaps the close
    # pair, and whatever avoids the overlap is invisible.
    #
    # Equal slots sidestep the trade-off entirely. The dates are still shown, as
    # the axis labels, so nothing is lost except the horizontal distortion — and
    # with at most nine studies at any location they all stay legible.
    pos = np.arange(len(df), dtype=float)
    bw = 0.38                        # each bar; the pair spans 0.76 of a slot

    fig, (axv, axs) = plt.subplots(2, 1, figsize=(8, 6.2), sharex=True)

    # Top panel: volume as grouped columns.
    axv.bar(pos - bw / 2, adt, width=bw, color="#4f81bd", label="ADT")
    axv.bar(pos + bw / 2, awdt, width=bw, color="#2ca25f", label="AWDT")
    axv.set_ylabel("Volume (veh/day)")
    # Headroom above the tallest column so the legend has somewhere to sit. With
    # only two or three studies the columns are wide enough to fill the panel, and
    # "best" placement then put the legend on top of one of them.
    vmax = np.nanmax(np.concatenate([adt, awdt])) if len(pos) else 0.0
    axv.set_ylim(0, vmax * 1.18 if np.isfinite(vmax) and vmax > 0 else 1)
    axv.legend(fontsize=8, loc="upper right", ncol=2, framealpha=0.92)
    axv.grid(True, axis="y", alpha=0.3)
    title = f"{location} — metrics over time"
    if direction and direction != "Merged":
        title += f" ({direction})"
    axv.set_title(title)

    # Bottom panel: speed as lines with markers. Headroom of +5 mph above the
    # highest speed point so the top line isn't pinned to the frame.
    axs.plot(pos, p85, "-o", color="#e08214", label="85th %ile speed")
    axs.plot(pos, mean, "-^", color="#d9534f", label="Mean speed")
    both = np.concatenate([p85, mean]) if len(pos) else np.array([np.nan])
    smax = np.nanmax(both) if np.isfinite(both).any() else 0.0
    axs.set_ylabel("Speed (mph)"); axs.set_ylim(0, smax + 5 if smax > 0 else 5)
    axs.set_xlabel("Study date")
    axs.legend(fontsize=8, loc="best", ncol=2); axs.grid(True, alpha=0.3)

    # Each slot labelled with its own study date. The full date, not just the
    # month: two counts at one location can fall in the same month, and "Oct 2021"
    # twice over would look like a mistake.
    labels = [d.strftime("%Y-%m-%d") for d in x]
    axs.set_xticks(pos)
    axs.set_xticklabels(labels)
    if len(pos):
        axs.set_xlim(-0.6, len(pos) - 0.4)
    # Show x-tick labels on BOTH panels (sharex hides them on the top axis by
    # default, and fig.autofmt_xdate() would too — so rotate manually per axis).
    for a in (axv, axs):
        a.tick_params(axis="x", labelbottom=True)
        for lbl in a.get_xticklabels():
            lbl.set_rotation(30)
            lbl.set_horizontalalignment("right")
            lbl.set_fontsize(8)
    fig.tight_layout()
    return fig
