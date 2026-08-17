# Traffic Study Diagnostics & Report Automation

A modular Python backbone that reproduces the City of Kenmore's legacy radar
**Speed & Volume** Excel report, runs **data-quality diagnostics** to flag
compromised studies, and exports a standardized, **City-branded** report — plus a
**Streamlit dashboard** on the same code. The data layer is source-agnostic, so SFS
and GridSmart can be added later without touching the statistics, diagnostics, or
reporting code.

Every report (HTML / Excel / PDF) and the dashboard cover the **Merged** view *and*
the **Directional** breakdown — figures, percentile tables, class splits, and 24h×day
matrices for each — mirroring the legacy `_ReportMerged` / `_ReportDirectional`
outputs. Directions are labeled with the **compass heading** recorded in `_Notes.txt`
(e.g. `NB` / `SB` / `EB` / `WB`), falling back to *Incoming* / *Outgoing* when a study
doesn't specify one.

The Python output has been **validated to within 1e-6 against the legacy Excel
reports across 243 studies** (Merged / Incoming / Outgoing): total vehicles,
average / median / max speed, ADT, average weekday traffic, and 85th-percentile speed
all match exactly.

## Quick start (Windows)

Double-click **`run_dashboard.bat`**. It:
1. sets the two configurable paths (see [Configuration](#configuration)),
2. stops any stale Streamlit server on port 8501,
3. builds/refreshes the **study catalog** (all locations × years), and
4. launches the dashboard at `http://<your-ip>:8501`.

Everything the app produces lives **inside the repo**; the only external directory is
the study **data** folder.

## Data layout it expects

```
<base>/<year>/<Location>_<YYYYMMDD>/<...>_Raw.csv      # canonical input
<base>/<year>/_Compromised Studies/<study>/            # human-labeled bad studies
<base>/<year>/_Incomplete/<study>/
<base>/study_catalog.csv                               # generated index (see below)
```

Raw CSV columns: `Date&Time, Speed, Class, Direction`. The CSV is the analysis week
(already trimmed). Years and locations are discovered automatically — new folders
appear with no code changes. Each study folder may also hold `*_Notes.txt`
(direction mapping, `Limit: <n>`), `*_Loc.*` (installation photo), and `*_Map.*`
(location map).

## Install

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
```

## Configuration

Two directories, both overridable from **`run_dashboard.bat`** (edit the
`CONFIGURATION` block at the top) or via environment variables:

| Variable | Meaning | Default |
|----------|---------|---------|
| `TRAFFIC_DATA_BASE` | the **data** root holding the `<year>` folders (the only path outside the repo) | `…\Mohammad\Speed and Volume Studies` |
| `APP_ROOT` | the app/repo root; anchors `reports/`, `assets/`, the catalog logic | the repo folder |
| `TRAFFIC_REPORTS_DIR` | where generated reports are written | `<APP_ROOT>\reports` |

Every CLI script also accepts `--base` to override the data folder inline.

**Speed limit** is resolved per study (the chosen source is shown on the
report/dashboard): explicit input → a `Limit: <n>` line in `_Notes.txt` → the value in
the existing `_Report.xlsx` (fallback while migrating off Excel) → default **25 mph**.

## Study catalog

`study_catalog.csv` (written next to the data) is a one-row-per-study index of the
whole tree — `location, year, install_date, study_id, status, source_name, path` plus
cached headline metrics `avg_speed, p85_speed, adt, awdt`. It lets the dashboard drive
a **Location → Year** picker without re-walking the disk.

It is built/refreshed **incrementally**: a refresh keeps the metrics already stored for
unchanged studies and only processes **new** folders (the first build computes all
~750 studies once, ~2–3 min; later refreshes are sub-second). Refresh it via
`run_dashboard.bat` (automatic at launch), the dashboard's **🔄 Rebuild catalog**
button, or `python scripts/build_catalog.py`.

## Command-line interface

See **[docs/CLI.md](docs/CLI.md)** for the full reference. Every command below is a
single-line invocation — copy/paste, edit the values, run.

### `scripts/generate_report.py` — reports, listing, validation, trends

Flags: `--base PATH` · `--year YEAR` · `--location SUBSTR` · `--all` · `--list` ·
`--validate` · `--trend` · `--out DIR` · `--format {html,excel,pdf,both,all}` (default
`both` = HTML+Excel; `all` adds PDF) · `--speed-limit N` · `--include-compromised`
*(no-op today — see docs/CLI.md Caveats)*.

```bash
# discover
.venv\Scripts\python.exe scripts\generate_report.py --list
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --list

# one study (substring match) -> HTML + Excel in reports\<study>\
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --location 61stAv_no_60thAv

# one study, PDF only, forced 35 mph
.venv\Scripts\python.exe scripts\generate_report.py --location 61stAv_no_60thAv --format pdf --speed-limit 35

# a whole year, all formats (HTML + Excel + PDF)
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --all --format all

# PDF for every study, onto another drive (~750 studies — large!)
.venv\Scripts\python.exe scripts\generate_report.py --all --format pdf --out "D:\pdf_out"

# validate Python vs the legacy Excel reports (one year or all)
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --validate
.venv\Scripts\python.exe scripts\generate_report.py --validate

# per-location over-time CSV (all years) — the D-factor-across-years check
.venv\Scripts\python.exe scripts\generate_report.py --location 73rdAv_so_185thSt --trend
```

### `scripts/build_catalog.py` — study catalog (index + cached metrics)

Flags: `--base PATH` · `--no-metrics` (skip cached avg/85th/ADT/AWDT; structure-only,
instant). Writes `study_catalog.csv` next to the data. Incremental — reuses metrics
for unchanged studies.

```bash
# full/incremental catalog build (writes <base>\study_catalog.csv)
.venv\Scripts\python.exe scripts\build_catalog.py

# structure-only, no metrics (fastest)
.venv\Scripts\python.exe scripts\build_catalog.py --no-metrics

# point at a different data root
.venv\Scripts\python.exe scripts\build_catalog.py --base "D:\OtherData"
```

### `scripts/build_catalog_xy.py` — catalog with projected X/Y (EPSG:2926)

Same rows as `build_catalog.py` plus **X, Y** projected from each installation
photo's GPS to EPSG:2926 (NAD83(HARN) / Washington North, ftUS). Studies whose
photo has no GPS get empty X/Y. Incremental for both metrics and X/Y.

Flags: `--base PATH` · `--out CSV` (default `<base>\study_catalog_xy.csv`) ·
`--no-metrics` (X/Y still computed).

```bash
# full/incremental XY catalog build
.venv\Scripts\python.exe scripts\build_catalog_xy.py

# custom output path
.venv\Scripts\python.exe scripts\build_catalog_xy.py --out "D:\gis\studies_xy.csv"

# X/Y only, skip cached metrics
.venv\Scripts\python.exe scripts\build_catalog_xy.py --no-metrics
```

### `tools/backfill_limit_to_notes.py` — copy Excel posted limit into `_Notes.txt`

Flags: `--base PATH` · `--apply` (write; omit for dry-run preview) ·
`--no-create` (only append to existing `_Notes.txt`, don't create new ones).
Idempotent — folders that already have a `Limit:` line are skipped.

```bash
# preview only (no changes)
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py

# actually write the Limit: <n> lines
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py --apply

# only append to existing _Notes.txt files (don't create new ones)
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py --apply --no-create
```

## Streamlit dashboard

```bash
streamlit run app/streamlit_app.py      # or just run_dashboard.bat
```

Pick a **location**, then a **year** available for it (a date sub-selector appears when
a location has more than one study that year); set the speed limit and **Run report**.
Then browse summary metrics, figures, hourly/class tables, and diagnostics, and
download the HTML / Excel / PDF report. New years/locations appear after a catalog
rebuild. The header carries the **City of Kenmore** letterhead, and the installation
photo links to its **GPS location on Google Maps** (read from the photo's EXIF).

## Reports

Each HTML / PDF / Excel report contains:

- a **City of Kenmore letterhead** (logo + wordmark + gold rule) in the brand palette;
- **data-quality diagnostics** with an overall risk badge;
- a **Summary** split into Speed and Volume boxes (Volume leads with **AWDT**), colored
  vs. the posted limit / white→blue by volume, with the **speed limit** shown up top;
- a **Directional Split — D-Factor** chart (compass-labeled);
- per-direction **speed-percentile** and **vehicle-class** tables and figures;
- per-direction **hourly matrices** — Volume, 85th-percentile speed, and average speed
  (24h × day, colored, with a thick divider before the summary columns).

## Package layout

| Module | Responsibility |
|--------|----------------|
| `config.py` | Canonical schema, per-source parsing specs, analysis & diagnostic thresholds, **paths & Kenmore brand palette** |
| `sources.py` | Read a raw file → canonical `[timestamp, speed, vehicle_class, direction]` |
| `discovery.py` | Enumerate years/locations/studies; parse folder names; read `_Notes.txt`; **photo GPS → Google Maps URL** |
| `catalog.py` | Build/refresh the incremental study-catalog table (structure + cached metrics) |
| `study.py` | Load a study, select the best complete 7-day window, resolve the speed limit |
| `metrics.py` | All statistics & tables, matching the Excel exactly |
| `diagnostics.py` | Data-quality checks → findings + overall risk |
| `figures.py` | Matplotlib charts (percentile curve, weekday 85th, hourly/daily, D-factor) |
| `styling.py` | Speed/volume color scales and table styling (group dividers, coloring) |
| `report.py` | HTML + PDF + Excel export (branded, with hourly tables) |
| `trends.py` | Per-location over-time table + the two-panel trend figure |
| `pipeline.py` | `process_study` / `process_all` — the shared backbone |
| `validate.py` | Compare Python output against the legacy Excel cached values |

## Methodology notes (matching the legacy Excel)

- **Average / Median / Max speed** use all vehicles in the window.
- **Percentile speeds (85th, …)** use the **true Mon–Fri weekday** days in the window
  by default (`AnalysisConfig.percentile_window="weekday"`), with a cumulative
  `P(speed < s)` distribution and linear interpolation between integer speeds. This
  intentionally **corrects a legacy-Excel flaw**: the workbook used the *first 5
  calendar days* from the window start and mislabeled it "weekday", so a non-Monday-start
  study pulled weekend days into the weekday 85th. Set `percentile_window="first"` (or
  use `config.LEGACY_ANALYSIS`) to reproduce the old numbers — that's what `--validate`
  uses to confirm an exact Excel match.
- **ADT** = total ÷ number of days; **Average Weekday Traffic (AWDT)** = mean of Mon–Fri
  daily totals. AWDT is the headline volume metric on the summary and dashboard.
- **AM/PM/overall peak hour** = the busiest 60-minute window found by a **15-minute
  sliding window** over average **weekday** (Mon–Fri) volume; each peak carries its
  average weekday hourly volume.
- **Per-location trend over time**: `--trend --location <name>` writes a CSV with one
  row per study (all years) — ADT, AWDT, 85th & mean speed, overall D-factor, an AADT
  placeholder, and peak hour + volume. Also the dashboard's **"Over time"** tab (a
  two-panel chart: volume columns on top, speed lines below, one tick per year), which
  follows the selected direction and doubles as the D-factor-across-years check.
- Each report/dashboard shows the **installation site photo** (`*_Loc.*`), its **GPS
  Google Maps link**, and the **location map** (`*_Map.*`).
- See `tools/` for the reverse-engineering / verification / backfill scripts.

## Branding

The report letterhead and dashboard chrome use the City of Kenmore palette (navy
`#0E1E37`, amber `#FFB300`, teal `#016666`, light grey `#E4E6EA`) and the City logo
in `assets/`. Data color scales (speed green→red, volume white→blue) are left as
meaningful encodings, per the style guide's brand-vs-data distinction.

## Extending to SFS / GridSmart

Add a `SourceSpec` to `config.py` (its raw glob, column map, datetime formats, and
class/direction normalization). If a source needs special parsing, subclass
`SourceAdapter` in `sources.py`. Everything downstream is unchanged.
