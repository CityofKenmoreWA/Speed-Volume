"""Standardized report export: self-contained HTML, formatted Excel, and PDF.

Every export covers the **Merged** view plus the **Directional** breakdown
(Incoming / Outgoing), mirroring the legacy ``_ReportMerged`` and
``_ReportDirectional`` outputs. All consume a ``pipeline.StudyResult`` so the
radar/SFS/GridSmart sources flow through identical reporting once their adapters
produce canonical data.
"""
from __future__ import annotations

import base64
import io
import os
from datetime import datetime

from jinja2 import Template

from . import styling
from .config import DIRECTION, AnalysisConfig, DEFAULT_ANALYSIS
from .figures import build_figures, fig_dfactor, save_figures
from .metrics import HOUR_LABELS

# Summary split into a Speed box and a Volume box (per supervisor).
# (label, attr, fmt, kind) — kind: "speed" colored vs limit, "count" white->blue,
# "plain" no color, "peak" the hour label.
SPEED_BOX = [("85th Percentile Speed", "design_speed", "{:.2f}", "speed"),
             ("Average Speed", "avg_speed", "{:.2f}", "speed"),
             ("Median Speed", "median_speed", "{:.2f}", "speed"),
             ("Maximum Speed", "max_speed", "{:.0f}", "speed"),
             ("% Over Limit", "over_limit_pct", "{:.1f}", "plain")]
VOLUME_BOX = [("Total Vehicles", "total", "{:.0f}", "count"),
              ("Average Daily Traffic", "adt", "{:.1f}", "count"),
              ("Average Weekday Traffic", "avg_weekday_traffic", "{:.1f}", "count"),
              ("AM Peak Hour", "am_peak", None, "peak"),
              ("PM Peak Hour", "pm_peak", None, "peak")]


def _summary_boxes(result, limit):
    """Build (dirs, speed_rows, volume_rows) where each row = (name, [(text, bg_hex)])."""
    dirs = list(result.metrics)
    counts = [getattr(result.metrics[d], a) for _, a, _, k in VOLUME_BOX if k == "count"
              for d in dirs if isinstance(getattr(result.metrics[d], a), (int, float))]
    vmin, vmax = (min(counts), max(counts)) if counts else (0.0, 1.0)

    def build(spec):
        out = []
        for name, attr, fmt, kind in spec:
            cells = []
            for d in dirs:
                v = getattr(result.metrics[d], attr)
                if kind == "peak":
                    cells.append((v[0] if v else "—", None))
                    continue
                ok = isinstance(v, (int, float)) and v == v
                txt = fmt.format(v) if ok else "—"
                bg = (styling.speed_hex(v, limit) if kind == "speed" else
                      styling.count_hex(v, vmin, vmax) if kind == "count" else None) if ok else None
                cells.append((txt, bg))
            out.append((name, cells))
        return out

    return dirs, build(SPEED_BOX), build(VOLUME_BOX)


def _pct_rows_colored(m, limit):
    """Percentile rows with the Speed cell background colored on the speed scale."""
    rows = []
    for p, s, e in m.pct_table:
        rows.append((p, (f"{s:.2f}" if s is not None else "—",
                         styling.speed_hex(s, limit) if s is not None else None),
                     f"{e:.2f}" if e is not None else "—"))
    return rows

_SEV_COLOR = {"error": "#d9534f", "warning": "#f0ad4e", "info": "#5bc0de", "ok": "#5cb85c"}
_RISK_COLOR = {"high": "#d9534f", "moderate": "#f0ad4e", "low": "#5cb85c"}
_SL_LABEL = {"input": "manual input", "excel": "existing Excel report",
             "notes": "Notes file", "default": "default"}
# Order figures appear in reports (matches the legacy report layout).
_FIG_ORDER = ("percentile_speed", "weekday_p85", "time_distribution",
              "daily_distribution", "speed_distribution")


def direction_window(window, label: str):
    """Slice the study window to one direction (Merged = everything)."""
    if label == "Merged":
        return window
    return window[window[DIRECTION] == label]


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _class_rows(m):
    return [(k, m.class_counts.get(k, 0), m.class_pct.get(k, 0.0))
            for k in ("Small", "Medium", "Large") if k in m.class_counts]


def hourly_report_table(m):
    """Hourly volume matrix with the per-hour Weekday 85th %ile prepended,
    matching the legacy Report sheet's hourly block."""
    if m.hourly_volume is None:
        return None
    t = m.hourly_volume.copy()
    if m.hourly_weekday_p85 is not None:
        t.insert(0, "Weekday 85th %ile", m.hourly_weekday_p85)
    return t


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
_HTML = Template("""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{{ title }}</title>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;}
 h1{font-size:20px;margin-bottom:2px} h2{font-size:16px;border-bottom:2px solid #5b62d6;padding-bottom:3px;margin-top:26px}
 h3{font-size:14px;color:#3b41a8;margin:14px 0 4px}
 .meta{color:#555;font-size:13px;margin-bottom:8px}
 table{border-collapse:collapse;margin:8px 0;font-size:13px}
 th,td{border:1px solid #ccc;padding:4px 8px;text-align:right}
 th{background:#eef0fb;text-align:center} td.l,th.l{text-align:left}
 .risk{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-weight:bold}
 .findings li{margin:3px 0} .sev{font-weight:bold}
 .figs img{max-width:520px;border:1px solid #ddd;margin:6px;vertical-align:top}
 .note{background:#fafafa;border-left:3px solid #5b62d6;padding:6px 10px;white-space:pre-wrap;font-size:13px}
 .recap{font-size:13px;color:#333;margin:2px 0 6px}
 .tabs{display:flex;flex-wrap:wrap;gap:20px}
</style></head><body>
<h1>{{ title }}</h1>
<div class="meta">
 Location: <b>{{ location }}</b> &nbsp;|&nbsp; Directions: {{ inc }} (in) / {{ out }} (out)
 &nbsp;|&nbsp; Speed limit: {{ speed_limit }} mph ({{ sl_source }})<br>
 Study window: {{ start }} &ndash; {{ end }} ({{ n_days }} days) &nbsp;|&nbsp; Source: {{ source }}
 &nbsp;|&nbsp; Generated: {{ generated }}
</div>
{% if notes %}<div class="note">{{ notes }}</div>{% endif %}

<h2>Data Quality Diagnostics
  &nbsp;<span class="risk" style="background:{{ risk_color }}">{{ risk|upper }} RISK</span></h2>
{% if findings %}<ul class="findings">
{% for f in findings %}<li><span class="sev" style="color:{{ f.color }}">[{{ f.severity|upper }}]</span>
 {{ f.category }}: {{ f.message }}</li>{% endfor %}
</ul>{% else %}<p>No issues detected.</p>{% endif %}

<h2>Summary Statistics</h2>
<div class="tabs">
<div><h3>🏁 Speed</h3>
<table><tr><th class="l">Metric</th>{% for d in dirs %}<th>{{ d }}</th>{% endfor %}</tr>
{% for name, cells in speed_rows %}<tr><td class="l">{{ name }}</td>
{% for txt, bg in cells %}<td{% if bg %} style="background:{{ bg }}"{% endif %}>{{ txt }}</td>{% endfor %}</tr>{% endfor %}
</table></div>
<div><h3>🚗 Volume</h3>
<table><tr><th class="l">Metric</th>{% for d in dirs %}<th>{{ d }}</th>{% endfor %}</tr>
{% for name, cells in volume_rows %}<tr><td class="l">{{ name }}</td>
{% for txt, bg in cells %}<td{% if bg %} style="background:{{ bg }}"{% endif %}>{{ txt }}</td>{% endfor %}</tr>{% endfor %}
</table></div>
</div>
<div class="recap">Speed cells colored vs the {{ speed_limit }} mph limit (green=slow → white=limit → yellow=+5 → red); volume cells white→blue.</div>

<h2>Directional Split — D-Factor</h2>
<div class="figs"><img src="data:image/png;base64,{{ dfactor_img }}"></div>

{% for sec in sections %}
<h2>{{ sec.label }} Report</h2>
<div class="recap">{{ sec.recap }}</div>
<div class="tabs">
<div><h3>Speed Percentiles</h3>
<table><tr><th>Pct</th><th>Speed</th><th>Excess</th></tr>
{% for p, scell, e in sec.pct_rows %}<tr><td>{{ p }}</td>
<td{% if scell.1 %} style="background:{{ scell.1 }}"{% endif %}>{{ scell.0 }}</td>
<td>{{ e }}</td></tr>{% endfor %}
</table></div>
<div><h3>Vehicle Classification</h3>
<table><tr><th class="l">Class</th><th>Count</th><th>%</th></tr>
{% for k,c,p in sec.class_rows %}<tr><td class="l">{{ k }}</td><td>{{ c }}</td>
<td>{{ '%.1f'|format(p) }}</td></tr>{% endfor %}
</table></div>
</div>
<div class="figs">{% for img in sec.figures %}<img src="data:image/png;base64,{{ img }}">{% endfor %}</div>
{% endfor %}
</body></html>""")


def _recap(m) -> str:
    p85 = f"{m.design_speed:.2f}" if m.design_speed else "-"
    return (f"Total {m.total:,} veh · ADT {m.adt:,.1f} · 85th %ile {p85} mph · "
            f"Avg {m.avg_speed:.2f} mph · Max {m.max_speed:.0f} mph · "
            f"% over limit {m.over_limit_pct:.1f}")


def build_html_report(result, cfg: AnalysisConfig = DEFAULT_ANALYSIS, directions=None) -> str:
    sd = result.data
    directions = directions or list(result.metrics)
    import matplotlib.pyplot as plt

    sections = []
    for label in directions:
        m = result.metrics[label]
        sub = direction_window(sd.window, label)
        figs = build_figures(sub, m, cfg)
        imgs = [_fig_to_b64(f) for f in figs.values()]
        for f in figs.values():
            plt.close(f)
        sections.append({"label": label, "recap": _recap(m),
                         "pct_rows": _pct_rows_colored(m, sd.speed_limit),
                         "class_rows": _class_rows(m), "figures": imgs})

    dir_names = {"Incoming": sd.notes.get("incoming"), "Outgoing": sd.notes.get("outgoing")}
    dfig = fig_dfactor(result.metrics, cfg, dir_names)
    dfactor_img = _fig_to_b64(dfig); plt.close(dfig)

    dirs, speed_rows, volume_rows = _summary_boxes(result, sd.speed_limit)
    diag = result.diagnostics
    findings = [{"category": f.category, "severity": f.severity, "message": f.message,
                 "color": _SEV_COLOR.get(f.severity, "#777")}
                for f in (diag.findings if diag else [])]
    return _HTML.render(
        title=f"Speed & Volume Study — {sd.study.location}",
        location=sd.study.location, inc=sd.notes.get("incoming") or "?",
        out=sd.notes.get("outgoing") or "?", speed_limit=f"{sd.speed_limit:g}",
        start=str(sd.window_start), end=str(sd.window_end),
        n_days=result.merged.n_days, source=sd.study.source_name,
        sl_source=_SL_LABEL.get(sd.speed_limit_source, sd.speed_limit_source),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        notes=sd.notes.get("raw", ""),
        risk=(diag.risk if diag else "unknown"),
        risk_color=_RISK_COLOR.get(diag.risk if diag else "", "#777"),
        findings=findings, dirs=dirs, speed_rows=speed_rows, volume_rows=volume_rows,
        dfactor_img=dfactor_img, sections=sections,
    )


def write_html_report(result, path: str, cfg: AnalysisConfig = DEFAULT_ANALYSIS) -> str:
    html = build_html_report(result, cfg)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


# --------------------------------------------------------------------------- #
# PDF (reportlab)
# --------------------------------------------------------------------------- #
def write_pdf_report(result, path: str, cfg: AnalysisConfig = DEFAULT_ANALYSIS,
                     directions=None) -> str:
    """Print-ready PDF with a Merged section and a Directional section per direction."""
    import shutil
    import tempfile

    import matplotlib.pyplot as plt
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    sd = result.data
    directions = directions or list(result.metrics)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    styles = getSampleStyleSheet()
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#3b41a8"))
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)

    doc = SimpleDocTemplate(path, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            title=f"Speed & Volume Study — {sd.study.location}")
    avail_w = doc.width
    m0 = result.merged
    flow = [Paragraph(f"Speed &amp; Volume Study — {sd.study.location}", styles["Title"])]
    meta = (f"Location: <b>{sd.study.location}</b> | Directions: {sd.notes.get('incoming')} (in) / "
            f"{sd.notes.get('outgoing')} (out) | Speed limit: {sd.speed_limit:g} mph "
            f"({_SL_LABEL.get(sd.speed_limit_source, sd.speed_limit_source)})<br/>"
            f"Window: {sd.window_start.date()} – {sd.window_end.date()} ({m0.n_days} days) | "
            f"Source: {sd.study.source_name} | Generated: {datetime.now():%Y-%m-%d %H:%M}")
    flow += [Paragraph(meta, small), Spacer(1, 8)]
    if sd.notes.get("raw"):
        flow += [Paragraph("<i>" + sd.notes["raw"].replace("\n", "<br/>") + "</i>", small),
                 Spacer(1, 8)]

    diag = result.diagnostics
    risk = diag.risk if diag else "unknown"
    rc = _RISK_COLOR.get(risk, "#777")
    flow += [Paragraph(f"Data Quality Diagnostics — "
                       f"<font color='{rc}'><b>{risk.upper()} RISK</b></font>", h2)]
    if diag and diag.findings:
        rows = [["Severity", "Category", "Message"]]
        for f in diag.findings:
            rows.append([f.severity.upper(), f.category, Paragraph(f.message, small)])
        t = Table(rows, colWidths=[0.9 * inch, 1.6 * inch, avail_w - 2.5 * inch])
        t.setStyle(_grid([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0fb"))]))
        flow += [t]
    else:
        flow += [Paragraph("No issues detected.", small)]
    flow += [Spacer(1, 10)]

    dirs, speed_rows, volume_rows = _summary_boxes(result, sd.speed_limit)
    boxes = Table([[Paragraph("Speed", h2), Paragraph("Volume", h2)],
                   [_box_table(speed_rows, dirs, avail_w / 2 - 4),
                    _box_table(volume_rows, dirs, avail_w / 2 - 4)]],
                  colWidths=[avail_w / 2, avail_w / 2])
    boxes.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow += [Paragraph("Summary Statistics", h2), boxes]

    tmpdir = tempfile.mkdtemp(prefix="tdpdf_")
    try:
        # D-Factor (study-level) once, right after the summary.
        dnames = {"Incoming": sd.notes.get("incoming"), "Outgoing": sd.notes.get("outgoing")}
        dfig = fig_dfactor(result.metrics, cfg, dnames)
        dpath = os.path.join(tmpdir, "dfactor.png")
        dfig.savefig(dpath, dpi=120, bbox_inches="tight"); plt.close(dfig)
        flow += [Spacer(1, 8), Paragraph("Directional Split — D-Factor", h2),
                 Image(dpath, width=avail_w, height=avail_w * 0.5)]
        for label in directions:
            m = result.metrics[label]
            sub = direction_window(sd.window, label)
            flow += [PageBreak(), Paragraph(f"{label} Report", h2),
                     Paragraph(_recap(m), small), Spacer(1, 4)]
            flow += [_pct_class_combo(m, avail_w, h2, small, sd.speed_limit)]
            figs = build_figures(sub, m, cfg)
            paths = save_figures(figs, tmpdir, prefix=f"{label}_")
            for f in figs.values():
                plt.close(f)
            for name in _FIG_ORDER:
                if name in paths:
                    flow += [Image(paths[name], width=avail_w, height=avail_w * 0.5), Spacer(1, 6)]
        doc.build(flow)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return path


def _box_table(rows, dirs, total_w):
    """A summary sub-table (Speed or Volume) with per-cell background colors."""
    from reportlab.lib import colors
    from reportlab.platypus import Table
    data = [["Metric"] + list(dirs)] + [[name] + [txt for txt, _ in cells] for name, cells in rows]
    ncol = len(dirs)
    metric_w = total_w * 0.46
    cw = [metric_w] + [(total_w - metric_w) / ncol] * ncol
    extra = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0fb")),
             ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("ALIGN", (0, 0), (0, -1), "LEFT")]
    for ri, (_name, cells) in enumerate(rows, start=1):
        for ci, (_txt, bg) in enumerate(cells, start=1):
            if bg:
                extra.append(("BACKGROUND", (ci, ri), (ci, ri), colors.HexColor(bg)))
    t = Table(data, colWidths=cw)
    t.setStyle(_grid(extra))
    return t


def _pct_class_combo(m, avail_w, h2, small, limit):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle
    pct = [["Pct", "Speed", "Excess"]] + [
        [str(p), f"{s:.2f}" if s is not None else "-", f"{e:.2f}" if e is not None else "-"]
        for p, s, e in m.pct_table]
    pct_style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0fb")),
                 ("ALIGN", (0, 0), (-1, -1), "RIGHT")]
    for ri, (_p, s, _e) in enumerate(m.pct_table, start=1):   # color the Speed cell
        bg = styling.speed_hex(s, limit) if s is not None else None
        if bg:
            pct_style.append(("BACKGROUND", (1, ri), (1, ri), colors.HexColor(bg)))
    pct_t = Table(pct, colWidths=[0.7 * 72, 0.9 * 72, 0.9 * 72])
    pct_t.setStyle(_grid(pct_style))
    cls = [["Class", "Count", "%"]] + [
        [k, str(m.class_counts.get(k, 0)), f"{m.class_pct.get(k, 0.0):.1f}"]
        for k in ("Small", "Medium", "Large") if k in m.class_counts]
    cls_t = Table(cls or [["Class", "Count", "%"]],
                  colWidths=[1.1 * 72, 0.9 * 72, 0.7 * 72])
    cls_t.setStyle(_grid([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0fb")),
                          ("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    combo = Table([[Paragraph("Speed Percentiles", h2), Paragraph("Vehicle Classification", h2)],
                   [pct_t, cls_t]], colWidths=[avail_w / 2, avail_w / 2])
    combo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return combo


def _grid(extra):
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle
    base = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    return TableStyle(base + list(extra))


# --------------------------------------------------------------------------- #
# Excel (xlsxwriter)
# --------------------------------------------------------------------------- #
def write_excel_report(result, path: str, cfg: AnalysisConfig = DEFAULT_ANALYSIS) -> str:
    """Formatted workbook: a two-box (Speed | Volume) color-coded Summary,
    per-direction percentiles, color-coded per-direction Hourly Volume / Speed
    matrices, and Diagnostics."""
    import xlsxwriter

    sd = result.data
    limit = sd.speed_limit
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb = xlsxwriter.Workbook(path, {"nan_inf_to_errors": True})
    h = wb.add_format({"bold": True, "bg_color": "#eef0fb", "border": 1})
    b = wb.add_format({"border": 1})
    title = wb.add_format({"bold": True, "font_size": 14})

    _cache: dict = {}

    def fmt(bg=None, num=None, bold=False):
        key = (bg, num, bold)
        if key not in _cache:
            d = {"border": 1}
            if bg:
                d["bg_color"] = bg
                d["font_color"] = styling.text_on_hex(bg)
            if num:
                d["num_format"] = num
            if bold:
                d["bold"] = True
            _cache[key] = wb.add_format(d)
        return _cache[key]

    ws = wb.add_worksheet("Summary")
    ws.set_column(0, 0, 24); ws.set_column(1, 8, 13)
    ws.write(0, 0, f"Speed & Volume Study — {sd.study.location}", title)
    ws.write(1, 0, f"Window: {sd.window_start.date()} .. {sd.window_end.date()} "
                   f"({result.merged.n_days} days)   Speed limit: {limit:g} mph "
                   f"({_SL_LABEL.get(sd.speed_limit_source, sd.speed_limit_source)})")
    ws.write(2, 0, f"Directions: {sd.notes.get('incoming')} (in) / {sd.notes.get('outgoing')} (out)")
    dirs, speed_rows, volume_rows = _summary_boxes(result, limit)
    ndir = len(dirs)

    def write_box(r0, c0, heading, rows):
        ws.write(r0, c0, heading, h)
        ws.write(r0 + 1, c0, "Metric", h)
        for j, d in enumerate(dirs):
            ws.write(r0 + 1, c0 + 1 + j, d, h)
        for i, (name, cells) in enumerate(rows, start=2):
            ws.write(r0 + i, c0, name, b)
            for j, (txt, bg) in enumerate(cells):
                ws.write(r0 + i, c0 + 1 + j, txt, fmt(bg=bg))

    write_box(4, 0, "Speed (colored vs limit)", speed_rows)
    write_box(4, ndir + 3, "Volume (white→blue)", volume_rows)

    # Per-direction percentile tables, stacked; Speed cell colored on the speed scale.
    r = 4 + max(len(speed_rows), len(volume_rows)) + 4
    for label in dirs:
        m = result.metrics[label]
        ws.write(r, 0, f"{label} — Speed Percentiles", h)
        ws.write(r, 1, "Speed", h); ws.write(r, 2, "Excess", h)
        for p, s, e in m.pct_table:
            r += 1
            ws.write(r, 0, p, b)
            ws.write(r, 1, s if s is not None else "",
                     fmt(bg=styling.speed_hex(s, limit) if s is not None else None, num="0.00"))
            ws.write(r, 2, e if e is not None else "", fmt(num="0.00"))
        r += 2

    # Per-direction hourly matrices.
    for label in dirs:
        m = result.metrics[label]
        _write_matrix(wb.add_worksheet(f"{label} Volume"[:31]), hourly_report_table(m), h, fmt,
                      limit, speed_col="Weekday 85th %ile")
        _write_matrix(wb.add_worksheet(f"{label} Speed"[:31]), m.hourly_speed, h, fmt,
                      limit, all_speed=True)

    wd = wb.add_worksheet("Diagnostics")
    wd.set_column(0, 0, 20); wd.set_column(1, 2, 60)
    wd.write(0, 0, "Risk", h)
    wd.write(0, 1, (result.diagnostics.risk if result.diagnostics else "?"), b)
    wd.write(2, 0, "Severity", h); wd.write(2, 1, "Category", h); wd.write(2, 2, "Message", h)
    for i, f in enumerate(result.diagnostics.findings if result.diagnostics else [], start=3):
        wd.write(i, 0, f.severity, b); wd.write(i, 1, f.category, b); wd.write(i, 2, f.message, b)

    wb.close()
    return path


def _write_matrix(ws, mat, h, fmt, limit, speed_col=None, all_speed=False):
    """Write an hour×column matrix with cell coloring.

    all_speed=True colors every numeric cell on the speed scale (hourly speed).
    Otherwise ``speed_col`` (if given) is speed-colored and the remaining numeric
    columns are white→blue, normalized over those count columns.
    """
    import math
    if mat is None:
        ws.write(0, 0, "No data", h)
        return
    cols = list(mat.columns)
    count_cols = [c for c in cols if c != speed_col]
    nums = [float(mat.loc[i, c]) for c in count_cols for i in mat.index
            if not (isinstance(mat.loc[i, c], float) and math.isnan(mat.loc[i, c]))]
    vmin, vmax = (min(nums), max(nums)) if nums and not all_speed else (0.0, 1.0)

    ws.write(0, 0, "Hour", h)
    for j, c in enumerate(cols):
        ws.write(0, j + 1, str(c), h)
    for i, (hour, row) in enumerate(mat.iterrows(), start=1):
        ws.write(i, 0, HOUR_LABELS[hour] if hour < len(HOUR_LABELS) else str(hour), h)
        for j, c in enumerate(cols):
            v = row[c]
            if isinstance(v, float) and math.isnan(v):
                ws.write(i, j + 1, "", fmt())
                continue
            if all_speed or c == speed_col:
                bg = styling.speed_hex(v, limit)
                ws.write(i, j + 1, float(v), fmt(bg=bg, num="0.0"))
            else:
                bg = styling.count_hex(v, vmin, vmax)
                ws.write(i, j + 1, float(v), fmt(bg=bg, num="0.0"))
