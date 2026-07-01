# Traffic Study Diagnostics & Report Automation

A modular Python backbone that reproduces the City's legacy radar **Speed & Volume**
Excel report, runs **data-quality diagnostics** to flag compromised studies, and
exports a standardized report — plus a **Streamlit dashboard** on the same code.
The data layer is source-agnostic, so SFS and GridSmart can be added later without
touching the statistics, diagnostics, or reporting code.

Every report (HTML / Excel / PDF) and the dashboard cover the **Merged** view *and*
the **Directional** breakdown (Incoming / Outgoing) — figures, percentile tables,
class splits, and 24h×day matrices for each — mirroring the legacy `_ReportMerged`
and `_ReportDirectional` outputs.

The Python output has been **validated to within 1e-6 against the legacy Excel
reports across 243 studies** (Merged / Incoming / Outgoing): total vehicles,
average / median / max speed, ADT, average weekday traffic, and 85th-percentile speed
all match exactly.

## Data layout it expects

```
<base>/<year>/<Location>_<YYYYMMDD>/<...>_Raw.csv      # canonical input
<base>/<year>/_Compromised Studies/<study>/            # human-labeled bad studies
<base>/<year>/_Incomplete/<study>/
```

Raw CSV columns: `Date&Time, Speed, Class, Direction`. The CSV is the analysis week
(already trimmed). Years and locations are discovered automatically — new folders
appear with no code changes.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
```

Set `TRAFFIC_DATA_BASE` to point at your data root, or pass `--base` / type it in the
dashboard. Default is the `Speed and Volume Studies` folder on this machine.

## Standalone CLI

```bash
# discover
python scripts/generate_report.py --list
python scripts/generate_report.py --year 2025 --list

# one study (substring match) -> HTML + Excel in ./reports/<study>/
python scripts/generate_report.py --year 2025 --location 56thAv_so_190thSt

# whole year
python scripts/generate_report.py --year 2025 --all

# validate Python vs the legacy Excel reports
python scripts/generate_report.py --year 2025 --validate
python scripts/generate_report.py --validate            # all years
```

Useful flags: `--format html|excel|pdf|both|all`, `--speed-limit 30`,
`--include-compromised`, `--out <dir>`. (`both` = HTML+Excel; `all` adds PDF.)

**Speed limit** is resolved per study with this precedence (and the chosen source
is shown on the report/dashboard): explicit `--speed-limit` input → a `Limit: <n>`
line in `_Notes.txt` → the value in the existing `_Report.xlsx` (fallback while
migrating off Excel) → default **25 mph**.

## Streamlit dashboard

```bash
streamlit run app/streamlit_app.py
```

Pick a year → location (auto-populated, with ⚠ on compromised), set the speed limit,
**Run report**, then browse summary metrics, figures, hourly/class tables, and
diagnostics, and download the HTML or Excel report.

## Package layout

| Module | Responsibility |
|--------|----------------|
| `config.py` | Canonical schema, per-source parsing specs (datetime formats, column maps), analysis & diagnostic thresholds |
| `sources.py` | Read a raw file → canonical `[timestamp, speed, vehicle_class, direction]` (radar today; add a `SourceSpec` for SFS/GridSmart) |
| `discovery.py` | Enumerate years / locations / study folders; parse folder names; read `_Notes.txt` |
| `study.py` | Load a study, select the best complete 7-day window, hold metadata |
| `metrics.py` | All statistics & tables, matching the Excel exactly |
| `diagnostics.py` | Data-quality checks → findings + overall risk |
| `figures.py` | Matplotlib charts (histogram, percentile curve, hourly/daily profiles) |
| `report.py` | HTML (embedded figures) + formatted Excel export |
| `pipeline.py` | `process_study` / `process_all` — the shared backbone |
| `validate.py` | Compare Python output against the legacy Excel cached values |

## Methodology notes (matching the legacy Excel)

- **Average / Median / Max speed** use all vehicles in the window.
- **Percentile speeds (85th, …)** use the **true Mon–Fri weekday** days in the window
  by default (`AnalysisConfig.percentile_window="weekday"`), with a cumulative
  `P(speed < s)` distribution and linear interpolation between integer speeds. This
  intentionally **corrects a legacy-Excel flaw**: the workbook used the *first 5
  calendar days* from the window start (cell `W2 = start + 4 days`) and mislabeled it
  "weekday", so a non-Monday-start study pulled weekend days into the weekday 85th.
  Set `percentile_window="first"` (or use `config.LEGACY_ANALYSIS`) to reproduce the
  old numbers exactly — that's what `--validate` uses to confirm an exact Excel match.
- **ADT** = total ÷ number of days; **Average Weekday Traffic** = mean of Mon–Fri
  daily totals; **AM/PM peak hour** uses the Mon–Thu hourly average.
- See `tools/` for the reverse-engineering / verification scripts.

## Extending to SFS / GridSmart

Add a `SourceSpec` to `config.py` (its raw glob, column map, datetime formats, and
class/direction normalization). If a source needs special parsing, subclass
`SourceAdapter` in `sources.py`. Everything downstream is unchanged.
