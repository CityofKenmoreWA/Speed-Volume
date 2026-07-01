"""Streamlit dashboard built on the traffic_diag backbone.

Run:  streamlit run app/streamlit_app.py

Pick a year (auto-discovered), then a location (auto-populated), run the report,
and view statistics, tables, figures, and diagnostics — with HTML/Excel download.
New years/locations on disk appear automatically.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.config import DEFAULT_BASE
from traffic_diag.discovery import find_studies, find_years
from traffic_diag.figures import build_figures, fig_dfactor
from traffic_diag.metrics import HOUR_LABELS
from traffic_diag.pipeline import process_study
from traffic_diag.report import (build_html_report, direction_window,
                                  hourly_report_table, write_excel_report, write_pdf_report)
from traffic_diag.styling import style_counts, style_hourly_table, style_speed

st.set_page_config(page_title="Traffic Study Diagnostics", layout="wide")
_RISK_COLOR = {"high": "#d9534f", "moderate": "#f0ad4e", "low": "#5cb85c"}

def show_footer():
    st.divider()
    st.markdown(
        """
        <div style='text-align: center; color: gray; font-size: 0.9em;'>
            Developed by Mohammad Mehdi Oshanreh <a href='https://oshanreh.com' target='_blank' style='text-decoration: none;'>oshanreh.com</a>
        </div>
        """, 
        unsafe_allow_html=True
    )

@st.cache_data(show_spinner=False)
def _years(base):
    return find_years(base)


@st.cache_data(show_spinner=False)
def _studies(base, year):
    return [(s.study_id, s.location, str(s.install_date), s.status, s.path, s.report_xlsx or "")
            for s in find_studies(base, year=year)]


@st.cache_data(show_spinner=False)
def _default_speed_limit(path, year):
    """Resolve the default speed limit for a study: Notes 'Limit:' -> Excel -> 25."""
    from traffic_diag.discovery import Study
    from traffic_diag.study import resolve_speed_limit
    val, src = resolve_speed_limit(Study(path=path, year=year, location="", install_date=None))
    return float(val), src


_SL_SRC_LABEL = {"excel": "existing Excel report", "notes": "Notes file (Limit:)",
                 "default": "default 25 mph", "input": "manual input"}


st.title("🚦 Traffic Study Diagnostics & Report")

with st.sidebar:
    st.header("Select study")
    base = st.text_input("Data folder", value=DEFAULT_BASE)
    if not os.path.isdir(base):
        st.error("Folder not found."); st.stop()
    years = _years(base)
    if not years:
        st.error("No year folders found."); st.stop()
    year = st.selectbox("Year", years, index=len(years) - 1)
    studies = _studies(base, year)
    if not studies:
        st.warning("No studies for this year."); st.stop()
    labels = [f"{loc}  ({d}){'  ⚠'+status if status!='normal' else ''}"
              for (_id, loc, d, status, _p, _x) in studies]
    idx = st.selectbox("Location", range(len(studies)), format_func=lambda i: labels[i])
    # Default the speed limit to the value in THIS study's existing Excel report.
    # Default speed limit resolved per study: existing Excel -> Notes 'Limit:' -> 25.
    default_sl, sl_src = _default_speed_limit(studies[idx][4], year)
    speed_limit = st.number_input("Speed limit (mph)", 5, 70, int(default_sl), 1)
    st.caption(f"Default {default_sl:g} mph from **{_SL_SRC_LABEL.get(sl_src, sl_src)}** — "
               f"override above if needed.")
    run = st.button("Run report", type="primary", width="stretch")

if not run and "result" not in st.session_state:
    st.info("Choose a study and click **Run report**.")
    show_footer()
    st.stop()

if run:
    sid, loc, d, status, path, _xlsx = studies[idx]
    sel = next(s for s in find_studies(base, year=year) if s.path == path)
    # Only override if the user changed the value; otherwise auto-resolve (keeps the
    # source label as Excel / Notes / default).
    explicit = None if abs(float(speed_limit) - default_sl) < 1e-9 else float(speed_limit)
    with st.spinner(f"Processing {sid}…"):
        st.session_state.result = process_study(sel, speed_limit=explicit)

result = st.session_state.result
sd, m = result.data, result.merged
diag = result.diagnostics

st.subheader(sd.study.location)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Vehicles", f"{m.total:,}")
c2.metric("ADT", f"{m.adt:,.0f}")
c3.metric("85th %ile Speed", f"{m.design_speed:.2f} mph" if m.design_speed else "—")
c4.metric("Avg Speed", f"{m.avg_speed:.2f} mph")
st.caption(f"Window: {sd.window_start:%Y-%m-%d} – {sd.window_end:%Y-%m-%d} ({m.n_days} days) · "
           f"Speed limit: {sd.speed_limit:g} mph ({_SL_SRC_LABEL.get(sd.speed_limit_source, sd.speed_limit_source)}) · "
           f"Directions: {sd.notes.get('incoming')} in / {sd.notes.get('outgoing')} out · "
           f"Source: {sd.study.source_name}")

# Diagnostics
color = _RISK_COLOR.get(diag.risk, "#777")
st.markdown(f"### Diagnostics &nbsp; <span style='background:{color};color:#fff;"
            f"padding:2px 10px;border-radius:10px'>{diag.risk.upper()} RISK</span>",
            unsafe_allow_html=True)
if diag.findings:
    st.dataframe(diag.to_frame()[["severity", "category", "message"]],
                 width="stretch", hide_index=True)
else:
    st.success("No issues detected.")
if sd.notes.get("raw"):
    with st.expander("Technician notes"):
        st.text(sd.notes["raw"])

# Merged vs Directional view selector (drives the Figures & Tables tabs).
view = st.radio("Direction (figures & tables)", list(result.metrics), horizontal=True)
mv = result.metrics[view]
wv = direction_window(sd.window, view)

tab_sum, tab_fig, tab_tab = st.tabs(["📊 Summary", "📈 Figures", "🗂 Tables"])

with tab_sum:
    dirs = list(result.metrics)
    def _row(attr, fmt):
        return {d: (fmt.format(getattr(result.metrics[d], attr))
                    if isinstance(getattr(result.metrics[d], attr), (int, float))
                    and getattr(result.metrics[d], attr) == getattr(result.metrics[d], attr)
                    else None) for d in dirs}
    # Two boxes side by side: Speed | Volume
    box_speed, box_vol = st.columns(2)
    with box_speed:
        st.markdown("#### 🏁 Speed")
        speed_tbl = pd.DataFrame({
            "85th Percentile": _row("design_speed", "{:.2f}"),
            "Average": _row("avg_speed", "{:.2f}"),
            "Median": _row("median_speed", "{:.2f}"),
            "Maximum": _row("max_speed", "{:.0f}"),
            "% Over Limit": _row("over_limit_pct", "{:.1f}"),
        }).T
        speed_num = speed_tbl.drop(index="% Over Limit", errors="ignore").apply(pd.to_numeric, errors="coerce")
        st.dataframe(style_speed(speed_num, sd.speed_limit).format(precision=2),
                     width="stretch")
        st.caption(f"Colored vs speed limit {sd.speed_limit:g} mph · "
                   f"% over limit: {', '.join(f'{d} {result.metrics[d].over_limit_pct:.1f}%' for d in dirs)}")
    with box_vol:
        st.markdown("#### 🚗 Volume")
        vol_tbl = pd.DataFrame({
            "Total Vehicles": _row("total", "{:.0f}"),
            "ADT": _row("adt", "{:.0f}"),
            "Avg Weekday Traffic": _row("avg_weekday_traffic", "{:.0f}"),
        }).T.apply(pd.to_numeric, errors="coerce")
        st.dataframe(style_counts(vol_tbl), width="stretch")
        peaks = pd.DataFrame({
            "AM Peak": {d: (result.metrics[d].am_peak[0] if result.metrics[d].am_peak else "—") for d in dirs},
            "PM Peak": {d: (result.metrics[d].pm_peak[0] if result.metrics[d].pm_peak else "—") for d in dirs},
        }).T
        st.dataframe(peaks, width="stretch")

    st.markdown(f"**Speed percentiles ({view})**")
    pct = pd.DataFrame(mv.pct_table, columns=["Percentile", "Speed", "Excess over limit"])
    st.dataframe(style_speed(pct.round({"Speed": 2, "Excess over limit": 2}),
                             sd.speed_limit, subset=["Speed"]).format(precision=2),
                 width="stretch", hide_index=True)

with tab_fig:
    st.caption(f"Showing **{view}** figures.")
    figs = build_figures(wv, mv)
    cols = st.columns(2)
    for i, (name, fig) in enumerate(figs.items()):
        cols[i % 2].pyplot(fig, width="stretch")
    # D-Factor uses both directions — show once.
    st.markdown("**Directional split — D-Factor**")
    dir_names = {"Incoming": sd.notes.get("incoming"), "Outgoing": sd.notes.get("outgoing")}
    st.pyplot(fig_dfactor(result.metrics, dir_names=dir_names), width="stretch")

with tab_tab:
    hrt = hourly_report_table(mv)
    if hrt is not None:
        st.markdown(f"**Hourly table — {view}** (speed scale on Weekday 85th %ile; counts white→blue)")
        hv = hrt.copy(); hv.index = [HOUR_LABELS[h] for h in hv.index]
        st.dataframe(style_hourly_table(hv, sd.speed_limit), width="stretch")
    if mv.hourly_speed is not None:
        st.markdown(f"**Hourly average speed — {view} (24h × day)**")
        hs = mv.hourly_speed.copy(); hs.index = [HOUR_LABELS[h] for h in hs.index]
        st.dataframe(style_speed(hs, sd.speed_limit).format(precision=2), width="stretch")
    if mv.class_counts:
        st.markdown(f"**Vehicle classification — {view}**")
        st.dataframe(pd.DataFrame({"Count": mv.class_counts,
                                   "%": {k: round(v, 1) for k, v in mv.class_pct.items()}}),
                     width="stretch")

# Downloads
st.markdown("### Export")
html = build_html_report(result)
d1, d2, d3 = st.columns(3)
d1.download_button("⬇ HTML report", html,
                   file_name=f"{sd.study.study_id}_report.html", mime="text/html",
                   width="stretch")
tmp_xlsx = os.path.join(tempfile.gettempdir(), f"{sd.study.study_id}_report.xlsx")
write_excel_report(result, tmp_xlsx)
with open(tmp_xlsx, "rb") as fh:
    d2.download_button("⬇ Excel report", fh.read(),
                       file_name=f"{sd.study.study_id}_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")
tmp_pdf = os.path.join(tempfile.gettempdir(), f"{sd.study.study_id}_report.pdf")
write_pdf_report(result, tmp_pdf)
with open(tmp_pdf, "rb") as fh:
    d3.download_button("⬇ PDF report", fh.read(),
                       file_name=f"{sd.study.study_id}_report.pdf", mime="application/pdf",
                       width="stretch")
    
show_footer()