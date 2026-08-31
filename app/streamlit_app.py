"""Streamlit dashboard built on the traffic_diag backbone.

Run:  streamlit run app/streamlit_app.py

Pick a location (from the study catalog), then a year available for it, run the
report, and view statistics, tables, figures, and diagnostics — with HTML / Excel /
PDF download. New years/locations appear once the catalog has been refreshed.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from traffic_diag.catalog import (catalog_path, load_or_build_catalog,
                                  study_from_row)
from traffic_diag.config import (DEFAULT_BASE, KENMORE_AMBER, KENMORE_NAVY,
                                 LOGO_PATH, LOGO_WHITE_PATH)
from traffic_diag.discovery import maps_url
from traffic_diag.figures import build_figures, fig_dfactor
from traffic_diag.metrics import HOUR_LABELS
from traffic_diag.pipeline import process_study
from traffic_diag.report import (build_html_report, direction_display, direction_window,
                                  hourly_report_table, write_excel_report, write_pdf_report)
from traffic_diag.styling import (add_col_dividers, style_counts,
                                   style_hourly_table, style_speed)
from traffic_diag.trends import over_time_table, fig_trend


@st.cache_data(show_spinner=False)
def _over_time(base, location, direction):
    return over_time_table(base, location, direction=direction)


# --------------------------------------------------------------------------- #
# Export artifacts.
#
# Streamlit reruns this whole script on every widget interaction — including the
# direction radio — and none of the three exports depends on that selection (each
# one always covers every direction). Uncached, a single WB/EB toggle spent ~8s
# rebuilding byte-identical reports.
#
# The `_result` parameter is underscore-prefixed so Streamlit skips hashing it
# (StudyResult is not hashable) and keys the cache on study_id + speed_limit —
# the only two inputs that change what a report contains. Re-running the same
# study with the same limit therefore reuses the cached bytes.
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Building HTML report…")
def _export_html(_result, study_id: str, speed_limit: float) -> str:
    return build_html_report(_result)


@st.cache_data(show_spinner="Building Excel report…")
def _export_xlsx(_result, study_id: str, speed_limit: float) -> bytes:
    path = os.path.join(tempfile.gettempdir(), f"{study_id}_report.xlsx")
    write_excel_report(_result, path)
    with open(path, "rb") as fh:
        return fh.read()


@st.cache_data(show_spinner="Building PDF report…")
def _export_pdf(_result, study_id: str, speed_limit: float) -> bytes:
    path = os.path.join(tempfile.gettempdir(), f"{study_id}_report.pdf")
    write_pdf_report(_result, path)
    with open(path, "rb") as fh:
        return fh.read()


st.set_page_config(page_title="Kenmore Traffic Study Diagnostics", layout="wide")
_RISK_COLOR = {"high": "#d9534f", "moderate": "#f0ad4e", "low": "#5cb85c"}

# Persistent City of Kenmore logo (top-left + sidebar) when supported.
try:
    if os.path.exists(LOGO_PATH):
        st.logo(LOGO_PATH)
except Exception:
    pass


@st.cache_data(show_spinner=False)
def _logo_uri(path):
    try:
        with open(path, "rb") as fh:
            return "data:image/png;base64," + base64.b64encode(fh.read()).decode()
    except Exception:
        return ""


def show_header():
    """City of Kenmore letterhead as a self-contained navy banner so the text stays
    legible in both light and dark Streamlit themes (the banner supplies its own
    background rather than relying on the page background)."""
    uri = _logo_uri(LOGO_WHITE_PATH if os.path.exists(LOGO_WHITE_PATH) else LOGO_PATH)
    img = f"<img src='{uri}' style='height:68px;width:auto'>" if uri else ""
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:18px;background:{KENMORE_NAVY};"
        f"padding:14px 22px;border-radius:8px;border-bottom:4px solid {KENMORE_AMBER};"
        f"margin-bottom:14px'>{img}"
        f"<div><div style='font-size:13px;letter-spacing:1.5px;color:{KENMORE_AMBER};"
        f"text-transform:uppercase;font-weight:700'>City of Kenmore</div>"
        f"<div style='font-size:28px;font-weight:700;color:#ffffff;line-height:1.15'>"
        f"🚦 Traffic Study Diagnostics &amp; Report</div></div></div>",
        unsafe_allow_html=True)

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

def _catalog_stamp(base) -> float:
    """Mtime of the catalog CSV, or 0.0 if it is not there yet.

    This is the cache key below. The catalog is refreshed out of band by the
    scheduled task (scripts/build_catalog.py), so keying on the file's mtime is
    what lets a dashboard that is already open pick up a refresh on its next
    rerun — no Rebuild button, no restart.
    """
    try:
        return os.path.getmtime(catalog_path(base))
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _catalog(base, stamp: float):
    """The study catalog (all locations x years), read from the CSV on the share.

    ``stamp`` is not used in the body — it is the cache key. A new mtime means a
    new entry, so the refreshed catalog is read instead of the stale one.
    """
    return load_or_build_catalog(base, rebuild=False)


@st.cache_data(show_spinner=False)
def _default_speed_limit(path, year):
    """Resolve the default speed limit for a study: Notes 'Limit:' -> Excel -> 25."""
    from traffic_diag.discovery import Study
    from traffic_diag.study import resolve_speed_limit
    val, src = resolve_speed_limit(Study(path=path, year=year, location="", install_date=None))
    return float(val), src


_SL_SRC_LABEL = {"excel": "existing Excel report", "notes": "Notes file (Limit:)",
                 "default": "default 25 mph", "input": "manual input"}

# Grid + sticky-header CSS for the hourly tables. st.dataframe drops a Styler's
# border CSS (its grid renderer ignores it), so hourly tables are rendered as the
# Styler's own HTML inside a scroll box — that keeps the coloring AND the dividers.
# Header/label cells get explicit backgrounds AND text colors so they stay legible
# regardless of the Streamlit theme (otherwise dark mode paints white text on these
# light cells). Data cells carry their own bg+contrast color from the styler.
_HOURLY_GRID = [
    {"selector": "", "props": [("border-collapse", "collapse"), ("font-size", "12px")]},
    {"selector": "th, td", "props": [("border", "1px solid #ccc"), ("padding", "3px 7px"),
                                     ("text-align", "right"), ("white-space", "nowrap")]},
    {"selector": "thead th", "props": [("position", "sticky"), ("top", "0"),
                                       ("background", KENMORE_NAVY), ("color", "#ffffff"),
                                       ("z-index", "2")]},
    {"selector": "th.row_heading", "props": [("position", "sticky"), ("left", "0"),
                                             ("background", "#E4E6EA"), ("color", "#111111"),
                                             ("text-align", "left"), ("z-index", "1")]},
]


def _render_hourly(styler, height=680):
    html = styler.set_table_styles(_HOURLY_GRID, overwrite=False).to_html()
    st.markdown(f'<div style="max-height:{height}px;overflow:auto;border:1px solid #ddd;'
                f'border-radius:4px">{html}</div>', unsafe_allow_html=True)


show_header()

with st.sidebar:
    st.header("Select study")

    # The data folder is fixed by deployment (TRAFFIC_DATA_BASE) — not user input.
    # The catalog behind it is refreshed out of band by the scheduled task, so the
    # dashboard is read-only with respect to the share.
    base = DEFAULT_BASE
    if not os.path.isdir(base):
        st.error(f"Study folder is not reachable:  \n`{base}`  \n\n"
                 "Check that the server can see the share, then restart the app.")
        show_footer(); st.stop()

    cat = _catalog(base, _catalog_stamp(base))
    if cat is None or cat.empty:
        st.error("No studies found in the study folder.")
        show_footer(); st.stop()

    # Pick a LOCATION first, then the YEAR available for that location.
    locations = sorted(cat["location"].unique())
    loc = st.selectbox("Location", locations)
    loc_rows = cat[cat["location"] == loc]

    years = sorted(int(y) for y in loc_rows["year"].dropna().unique())

    def _year_label(y):
        rr = loc_rows[loc_rows["year"] == y]
        return f"{y}" + ("  ⚠" if (rr["status"] != "normal").any() else "")

    year = st.selectbox("Year", years, index=len(years) - 1, format_func=_year_label)
    yr_rows = loc_rows[loc_rows["year"] == year].reset_index(drop=True)

    # A (location, year) can hold more than one deployment (different install dates).
    if len(yr_rows) > 1:
        def _study_label(i):
            r = yr_rows.iloc[i]
            tag = "  ⚠" if r["status"] != "normal" else ""
            return f"{r['install_date'] or r['study_id']}{tag}"
        si = st.selectbox("Study (date)", range(len(yr_rows)), format_func=_study_label)
    else:
        si = 0
    row = yr_rows.iloc[si]

    # Default speed limit resolved per study: Notes 'Limit:' -> existing Excel -> 25.
    default_sl, sl_src = _default_speed_limit(row["path"], int(year))
    speed_limit = st.number_input("Speed limit (mph)", 5, 70, int(default_sl), 1)
    st.caption(f"Default {default_sl:g} mph from **{_SL_SRC_LABEL.get(sl_src, sl_src)}** — "
               f"override above if needed.")
    run = st.button("Run report", type="primary", width="stretch")

if not run and "result" not in st.session_state:
    st.info("Choose a location and year, then click **Run report**.")
    show_footer()
    st.stop()

if run:
    sel = study_from_row(row)
    # Only override if the user changed the value; otherwise auto-resolve (keeps the
    # source label as Excel / Notes / default).
    explicit = None if abs(float(speed_limit) - default_sl) < 1e-9 else float(speed_limit)
    with st.spinner(f"Processing {row['study_id']}…"):
        st.session_state.result = process_study(sel, speed_limit=explicit)

result = st.session_state.result
sd, m = result.data, result.merged
diag = result.diagnostics
# Canonical direction keys -> display labels (compass heading from _Notes.txt when
# available, else the generic word). Merged stays "Merged".
dmap = direction_display(sd.notes)

st.subheader(sd.study.location)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("AWDT", f"{m.avg_weekday_traffic:,.0f}"
          if m.avg_weekday_traffic == m.avg_weekday_traffic else "—")
c2.metric("ADT", f"{m.adt:,.0f}")
c3.metric("85th %ile Speed", f"{m.design_speed:.2f} mph" if m.design_speed else "—")
c4.metric("Avg Speed", f"{m.avg_speed:.2f} mph")
c5.metric("Speed Limit", f"{sd.speed_limit:g} mph")
st.caption(f"Window: {sd.window_start:%Y-%m-%d} – {sd.window_end:%Y-%m-%d} ({m.n_days} days) · "
           f"Speed limit: {sd.speed_limit:g} mph ({_SL_SRC_LABEL.get(sd.speed_limit_source, sd.speed_limit_source)}) · "
           f"Directions: {sd.notes.get('incoming')} in / {sd.notes.get('outgoing')} out · "
           f"Source: {sd.study.source_name}")

# Installation site photo(s) + location map. Some sites have more than one
# installation photo (e.g. *_Loc1 / *_Loc2); show every one, not just the first.
_loc_photos, _map_img = sd.study.loc_photos, sd.study.map_image
if _loc_photos or _map_img:
    with st.expander("📍 Installation site", expanded=True):
        pc = st.columns(2)
        for i, _photo in enumerate(_loc_photos):
            cap = (f"Installation site — {sd.study.location} "
                   f"(installed {sd.study.install_date})")
            if len(_loc_photos) > 1:
                cap += f" · photo {i + 1} of {len(_loc_photos)}"
            pc[0].image(_photo, caption=cap, width="stretch")
        if _loc_photos:
            gps = sd.study.loc_gps
            if gps:
                pc[0].markdown(f"📍 [Open in Google Maps]({maps_url(*gps)}) "
                               f"· {gps[0]:.5f}, {gps[1]:.5f}")
        if _map_img:
            pc[1].image(_map_img, caption="Location map", width="stretch")

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
# Options stay canonical keys; the compass heading is shown via format_func.
view = st.radio("Direction (figures & tables)", list(result.metrics),
                format_func=lambda k: dmap.get(k, k), horizontal=True)
view_label = dmap.get(view, view)
mv = result.metrics[view]
wv = direction_window(sd.window, view)

tab_sum, tab_fig, tab_tab, tab_trend = st.tabs(
    ["📊 Summary", "📈 Figures", "🗂 Tables", "📅 Over time"])

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
        }).T.rename(columns=dmap)
        speed_num = speed_tbl.drop(index="% Over Limit", errors="ignore").apply(pd.to_numeric, errors="coerce")
        st.dataframe(style_speed(speed_num, sd.speed_limit).format(precision=2),
                     width="stretch")
        st.caption(f"Colored vs speed limit {sd.speed_limit:g} mph · "
                   f"% over limit: {', '.join(f'{dmap.get(d, d)} {result.metrics[d].over_limit_pct:.1f}%' for d in dirs)}")
    with box_vol:
        st.markdown("#### 🚗 Volume")
        vol_tbl = pd.DataFrame({
            "Avg Weekday Traffic": _row("avg_weekday_traffic", "{:.0f}"),
            "ADT": _row("adt", "{:.0f}"),
        }).T.rename(columns=dmap).apply(pd.to_numeric, errors="coerce")
        st.dataframe(style_counts(vol_tbl), width="stretch")
        peaks = pd.DataFrame({
            "AM Peak": {d: (result.metrics[d].am_peak[0] if result.metrics[d].am_peak else "—") for d in dirs},
            "AM Peak Vol": {d: (f"{result.metrics[d].am_peak[1]:.0f}" if result.metrics[d].am_peak else "—") for d in dirs},
            "PM Peak": {d: (result.metrics[d].pm_peak[0] if result.metrics[d].pm_peak else "—") for d in dirs},
            "PM Peak Vol": {d: (f"{result.metrics[d].pm_peak[1]:.0f}" if result.metrics[d].pm_peak else "—") for d in dirs},
        }).T.rename(columns=dmap)
        st.dataframe(peaks, width="stretch")
        st.caption("Peak Vol = average weekday vehicles in the peak 60-min window.")

    st.markdown(f"**Speed percentiles ({view_label})**")
    pct = pd.DataFrame(mv.pct_table, columns=["Percentile", "Speed", "Excess over limit"])
    st.dataframe(style_speed(pct.round({"Speed": 2, "Excess over limit": 2}),
                             sd.speed_limit, subset=["Speed"]).format(precision=2),
                 width="stretch", hide_index=True)

with tab_fig:
    st.caption(f"Showing **{view_label}** figures.")
    figs = build_figures(wv, mv)
    cols = st.columns(2)
    for i, (name, fig) in enumerate(figs.items()):
        cols[i % 2].pyplot(fig, width="stretch")
    # D-Factor uses both directions — show once.
    st.markdown("**Directional split — D-Factor**")
    dir_names = {"Incoming": sd.notes.get("incoming"), "Outgoing": sd.notes.get("outgoing")}
    st.pyplot(fig_dfactor(result.metrics, dir_names=dir_names), width="stretch")

with tab_tab:
    # Tall enough to show ~19 hours at once (midnight–evening) so the AM and PM
    # peak hours are both visible without scrolling. A thick rule separates the
    # per-day columns from the Average/Overall summary columns.
    hrt = hourly_report_table(mv)
    if hrt is not None:
        st.markdown(f"**Hourly volume — {view_label}** (counts white→blue)")
        hv = hrt.copy(); hv.index = [HOUR_LABELS[h] for h in hv.index]
        _render_hourly(add_col_dividers(style_hourly_table(hv, sd.speed_limit), ["Average"]))
    if mv.hourly_p85 is not None:
        st.markdown(f"**Hourly 85th percentile speed — {view_label} (24h × day)**")
        hp = mv.hourly_p85.copy(); hp.index = [HOUR_LABELS[h] for h in hp.index]
        _render_hourly(add_col_dividers(style_speed(hp, sd.speed_limit).format(precision=2), ["Overall"]))
    if mv.hourly_speed is not None:
        st.markdown(f"**Hourly average speed — {view_label} (24h × day)**")
        hs = mv.hourly_speed.copy(); hs.index = [HOUR_LABELS[h] for h in hs.index]
        _render_hourly(add_col_dividers(style_speed(hs, sd.speed_limit).format(precision=2), ["Average"]))
    if mv.class_counts:
        st.markdown(f"**Vehicle classification — {view_label}**")
        st.dataframe(pd.DataFrame({"Count": mv.class_counts,
                                   "%": {k: round(v, 1) for k, v in mv.class_pct.items()}}),
                     width="stretch")

with tab_trend:
    st.markdown(f"**{sd.study.location} — statistics over time** (all studies for this "
                f"location, across years). Volume/speed columns follow the **{view_label}** "
                f"direction selected above; D-Factor is always the overall split.")
    if st.button("Build over-time table", type="primary"):
        with st.spinner(f"Processing every study for this location ({view_label})…"):
            table = _over_time(base, sd.study.location, view)
        st.session_state.trend_table = table
        st.session_state.trend_key = (sd.study.location, view)
    if st.session_state.get("trend_key") == (sd.study.location, view) and "trend_table" in st.session_state:
        table = st.session_state.trend_table
        st.dataframe(table, width="stretch", hide_index=True)
        fname = f"{sd.study.location}_{view_label}_trend.csv" if view != "Merged" else f"{sd.study.location}_trend.csv"
        st.download_button("⬇ Download CSV", table.to_csv(index=False),
                           file_name=fname, mime="text/csv")
        if len(table) >= 2:
            st.pyplot(fig_trend(table, sd.study.location, view_label), width="stretch")
            dvals = pd.to_numeric(table["D-Factor"], errors="coerce").dropna()
            if len(dvals) >= 2:
                st.caption(f"D-factor across years: {dvals.min():.2f}–{dvals.max():.2f} "
                           f"(range {dvals.max()-dvals.min():.2f}). Large swings may indicate "
                           f"a data/placement issue.")

# Downloads. Built once per study (see the cached _export_* helpers) so changing
# the direction selection above does not regenerate them.
st.markdown("### Export")
sid, slim = sd.study.study_id, float(sd.speed_limit)
d1, d2, d3 = st.columns(3)
d1.download_button("⬇ HTML report", _export_html(result, sid, slim),
                   file_name=f"{sid}_report.html", mime="text/html",
                   width="stretch")
d2.download_button("⬇ Excel report", _export_xlsx(result, sid, slim),
                   file_name=f"{sid}_report.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   width="stretch")
d3.download_button("⬇ PDF report", _export_pdf(result, sid, slim),
                   file_name=f"{sid}_report.pdf", mime="application/pdf",
                   width="stretch")
    
show_footer()