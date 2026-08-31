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

### Correctness over Excel-compatibility

This started as a reimplementation of the legacy Excel workbook, but the workbook is
**not the reference any more** — several of its methods were simply wrong, and the
code now does the correct thing instead. See
[Where this deliberately differs from the Excel](#where-this-deliberately-differs-from-the-excel).

The headline scalars — total vehicles, average / median / max speed, ADT, AWDT and the
85th-percentile speed — still reconcile with the legacy reports to within 1e-6 across
**742 studies (99.60 % of 15,547 comparisons)**; the residual mismatches are broken
cached cells in a handful of old workbooks, not disagreements about method. Run
`--validate` to reproduce that, and set `config.LEGACY_ANALYSIS` to regenerate the old
numbers wholesale when you need to explain a difference.

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
`run_dashboard.bat` (automatic at launch) or `python scripts/build_catalog.py`.
The dashboard itself never rebuilds the catalog — it reads the CSV and picks up a
refresh automatically by watching the file's timestamp.

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

### `scripts/build_catalog_xy.py` — catalog with lat/lon (WGS84)

Same rows as `build_catalog.py` plus **lat, lon** read straight from each
installation photo's EXIF GPS (WGS84 / EPSG:4326, rounded to 6 dp ≈ 0.1 m).
Studies whose photo has no GPS (or has a 0/0 fix) get empty lat/lon.
Incremental for both metrics and lat/lon — reruns only touch new studies.
No pyproj / projection dependency.

Flags: `--base PATH` · `--out CSV` (default `<base>\study_catalog_latlon.csv`) ·
`--no-metrics` (lat/lon still read).

```bash
# full/incremental lat-lon catalog build for ALL studies (all locations × years)
.venv\Scripts\python.exe scripts\build_catalog_xy.py

# custom output path
.venv\Scripts\python.exe scripts\build_catalog_xy.py --out "D:\gis\studies_latlon.csv"

# lat/lon only, skip cached metrics (fastest)
.venv\Scripts\python.exe scripts\build_catalog_xy.py --no-metrics

# point at a different data root
.venv\Scripts\python.exe scripts\build_catalog_xy.py --base "D:\OtherData"
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

## Data-quality diagnostics — flags & severity

Each report and the dashboard carry a **diagnostics** block: individual **findings**
(with per-check severity) plus an **overall risk badge** derived from them.
Thresholds live in `config.DiagnosticThresholds` (`DEFAULT_THRESHOLDS`) and are
tunable in code without touching the checks themselves.

### Findings (all checks)

| Category | Severity | Trigger |
|---|---|---|
| `standard_short_count` | info | Window is exactly 3 days AND those days are Tue–Wed–Thu (recognized standard short count — not a risk). |
| `insufficient_days` | error | 3-day window that is **not** Tue–Thu, **or** the study loader had to shorten the window ("Only …" note). |
| `insufficient_days` | warning | Selected window has < 7 days (but isn't the 3-day error case). |
| `incomplete_day` | info | A single day has fewer than `min_hourly_coverage` × 24 hours of data (default 75% → < 18 h). |
| `data_gap` | warning | One or more gaps > `max_gap_hours` (default **3 h**) that start in the active window (05:00–21:00), or gaps ≥ 6 h at any time. |
| `no_data` | error | Selected window contains zero vehicles. |
| `low_volume` | warning | Mean daily volume < `low_volume_per_day` (default **50 veh/day**). |
| `erratic_volume` | warning | Day-to-day volume swing (max/min) > `max_daily_volume_ratio` (default **3.0×**). |
| `adt_exceeds_awdt` | warning | ADT > AWDT (weekends busier than weekdays — verify before trusting the weekday D-factor). |
| `single_direction` | info | Only one travel direction is present in the data. |
| `direction_imbalance` | warning | Busier direction's share > `max_direction_share` (default **70 %**). |
| `speed_outliers` | info / warning | Any speeds outside [`speed_min`, `speed_max`] mph (defaults **5**–**90**). **warning** if outliers ≥ 1 % of records, otherwise **info**. |
| `class_distribution` | warning | Heavy-vehicle share ("Large") > **15 %**. |
| `class_distribution` | info | All vehicles fall in a single class. |
| `duplicate_records` | warning | > **40 %** of raw rows are fully-identical (timestamp + speed + class + direction) — possible data doubling. |
| `unordered_timestamps` | info | Raw timestamps are not monotonically increasing. |
| `stray_raw_file` | warning | Study folder contains more than one raw file when exactly one is expected. |
| `technician_note` | warning | `_Notes.txt` contains a quality keyword (snow, ice, imbalance, construction, closure, rain, flood, incomplete, error, parking, check). |
| `diagnostic_error` | info | A check itself raised an exception (defensive — never sinks the whole report). |

### Overall risk (badge)

Aggregated from the individual severities of all findings:

| Overall risk | Rule |
|---|---|
| 🟥 **high** | at least one `error` finding |
| 🟨 **moderate** | at least one `warning` finding (and no errors) |
| 🟩 **low** | only `info` findings, or none at all |

Severity order (used for sorting the findings table, worst first):
`error (3) > warning (2) > info (1) > ok (0)`.

### Threshold defaults (`DiagnosticThresholds`)

| Field | Default | Used by |
|---|---|---|
| `min_hourly_coverage` | `0.75` | `incomplete_day` |
| `low_volume_per_day` | `50` | `low_volume` |
| `max_direction_share` | `0.70` | `direction_imbalance` |
| `speed_min` / `speed_max` | `5.0` / `90.0` mph | `speed_outliers` |
| `max_gap_hours` | `3.0` h | `data_gap` |
| `max_daily_volume_ratio` | `3.0` | `erratic_volume` |

The 15 % heavy-vehicle share, 40 % duplicate share, and technician-note keyword list
are baked into the checks themselves (`traffic_diag/diagnostics.py`).

## Package layout

| Module | Responsibility |
|--------|----------------|
| `config.py` | Canonical schema, per-source parsing specs, analysis & diagnostic thresholds, **paths & Kenmore brand palette** |
| `sources.py` | Read a raw file → canonical `[timestamp, speed, vehicle_class, direction]` |
| `discovery.py` | Enumerate years/locations/studies; parse folder names; read `_Notes.txt`; **photo GPS → Google Maps URL** |
| `catalog.py` | Build/refresh the incremental study-catalog table (structure + cached metrics) |
| `study.py` | Load a study, select the best complete 7-day window, resolve the speed limit |
| `metrics.py` | All statistics & tables (standard practice, not Excel-bug-compatible) |
| `diagnostics.py` | Data-quality checks → findings + overall risk |
| `figures.py` | Matplotlib charts (percentile curve, weekday 85th, hourly/daily, D-factor) |
| `styling.py` | Speed/volume color scales and table styling (group dividers, coloring) |
| `report.py` | HTML + PDF + Excel export (branded, with hourly tables) |
| `trends.py` | Per-location over-time table + the two-panel trend figure |
| `pipeline.py` | `process_study` / `process_all` — the shared backbone |
| `validate.py` | Compare Python output against the legacy Excel cached values |

## Methodology notes

- **Average / Median / Max speed** use all vehicles in the window.
- **Percentile speeds (85th, …)** use the **true Mon–Fri weekday** days in the window
  by default (`AnalysisConfig.percentile_window="weekday"`), with a cumulative
  `P(speed < s)` distribution and linear interpolation between integer speeds. This
  intentionally **corrects a legacy-Excel flaw**: the workbook used the *first 5
  calendar days* from the window start and mislabeled it "weekday", so a non-Monday-start
  study pulled weekend days into the weekday 85th. Set `percentile_window="first"` (or
  use `config.LEGACY_ANALYSIS`) to reproduce the old numbers. `--validate` does not go
  through that setting: it passes the legacy day set to the metrics directly, so it
  confirms the exact Excel match whatever the configured default is.
  The **Percentile Speed** chart plots this same day set — it reads the dates back off
  `DirectionMetrics.design_dates`, so the curve and the 85th-percentile marker drawn on
  it always agree.
- **ADT** = total ÷ number of days; **Average Weekday Traffic (AWDT)** = mean of Mon–Fri
  daily totals. AWDT is the headline volume metric on the summary and dashboard.
- **Hourly tables.** Volume columns average the per-day counts. Speed columns
  (mean and 85th) **pool the underlying speeds** rather than averaging per-day
  statistics — averaging per-day means would let a day with one vehicle count as much
  as a day with two hundred, and the mean of per-day 85th percentiles is not an 85th
  percentile of anything. "Weekday" means **Mon–Fri** throughout.
- **Percentile grid** spans every observed speed, so no vehicle is missing from the
  denominator. **10-MPH pace** covers exactly ten consecutive integer speeds.
- **D-Factor** is reported only when both directions were measured; a one-way study
  gets a blank, not a 1.00 that looks like a measured 100/0 split.
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

## Where this deliberately differs from the Excel

Each of these was a defect in the workbook, confirmed against the data and fixed here.
`config.LEGACY_ANALYSIS` restores the old behaviour for side-by-side comparison.

| What the Excel did | Why it was wrong | Real-world effect |
|---|---|---|
| "Weekday" 85th percentile = the **first 5 calendar days** of the window | Mislabels weekend days as weekdays whenever a study does not start on a Monday | Corrected — this is the `percentile_window` switch |
| Hourly **"Weekday Avg"** column = **Mon–Thu**, silently dropping Friday | Disagreed with AWDT on the same report, which used Mon–Fri | Moved in **542 of 744** studies; median 2.9 veh/h, max 86 |
| Hourly **85th "Weekday Overall"** = Mon–Thu | Same missing Friday, so Friday speeding was invisible | Moved in **542 of 744** studies; median 1.4 mph, max 21.6 |
| Hourly **mean speed** summary = mean of the per-day means | Unweighted: a day with 1 vehicle weighed the same as a day with 200 | Moved in **744 of 745** studies; median 1.0 mph, max 4.8 |
| Percentile denominator covered speeds **1–99 mph** only | Vehicles at 100+ mph vanished from every percentile while still counting toward `max_speed` | 8 studies record 100–104 mph |
| "10-MPH pace" convolved **11** speed bins | An 11-mph-wide pace overstates its share of traffic | Latent — `pace` is computed but not yet displayed |
| Speed→percentile table indexed the cumulative array **positionally** | Reported the figure for the *next* integer speed up | Latent — `speed_pct_table` is computed but not yet displayed |

Two further fixes were ours, not the Excel's: a window longer than 7 days produced
**duplicate weekday columns** (2 studies), and an absent direction reported the default
**7-day** window length rather than the real one.

## Branding

The report letterhead and dashboard chrome use the City of Kenmore palette (navy
`#0E1E37`, amber `#FFB300`, teal `#016666`, light grey `#E4E6EA`) and the City logo
in `assets/`. Data color scales (speed green→red, volume white→blue) are left as
meaningful encodings, per the style guide's brand-vs-data distinction.

## Extending to SFS / GridSmart

Add a `SourceSpec` to `config.py` (its raw glob, column map, datetime formats, and
class/direction normalization). If a source needs special parsing, subclass
`SourceAdapter` in `sources.py`. Everything downstream is unchanged.
