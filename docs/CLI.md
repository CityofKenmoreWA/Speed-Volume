# CLI Guide — Traffic Study Diagnostics

All commands run from the repo root. Use the venv's Python explicitly (works without
activating the environment):

```
.venv\Scripts\python.exe <script> <flags>
```

---

## 0. Configuration (paths)

Two directories, both settable as environment variables — the launcher sets them for
you (`run_dashboard.bat` → `CONFIGURATION` block):

| Variable | What it is | Default |
|----------|-----------|---------|
| `TRAFFIC_DATA_BASE` | the DATA root holding the `<year>` folders | `…\Speed and Volume Studies` |
| `APP_ROOT` | the app/repo root (anchors `reports/`, `assets/`) | the repo folder |
| `TRAFFIC_REPORTS_DIR` | where reports are written | `<APP_ROOT>\reports` |

Set for one command (PowerShell):
```powershell
$env:TRAFFIC_DATA_BASE = "D:\OtherData"
.venv\Scripts\python.exe scripts\generate_report.py --list
```
Every script also accepts `--base` to override the data folder inline.

---

## 1. Launch the dashboard

```
run_dashboard.bat
```
Sets the paths, kills any stale Streamlit, rebuilds the study catalog, then starts
Streamlit on `http://<your-ip>:8501`. (Equivalent manual command:
`.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py`.)

---

## 2. `scripts/generate_report.py` — reports, listing, validation, trends

```
.venv\Scripts\python.exe scripts\generate_report.py [flags]
```

| Flag | Meaning |
|------|---------|
| `--base PATH` | data root (default: `TRAFFIC_DATA_BASE`) |
| `--year YEAR` | restrict to one year (e.g. `2026`) |
| `--location SUBSTR` | match a location/study by substring |
| `--all` | process every matching study (all years if no `--year`) |
| `--list` | list years (or, with `--year`, that year's studies) and exit |
| `--validate` | compare Python output to the legacy Excel reports |
| `--trend` | with `--location`: write a per-location over-time CSV (all years) |
| `--out DIR` | output directory (default: `<APP_ROOT>\reports`) |
| `--format {html,excel,pdf,both,all}` | what to write (default `both` = HTML+Excel; `all` adds PDF) |
| `--speed-limit N` | force the posted limit (mph); else resolved per study |
| `--include-compromised` | *(currently a no-op — see Caveats)* |

**Speed-limit resolution** (when `--speed-limit` omitted): `_Notes.txt` `Limit:` line →
existing Excel report → default **25 mph**.

**Examples**
```powershell
.venv\Scripts\python.exe scripts\generate_report.py --list
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --list

# one study → HTML + Excel (into reports\<study>\)
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --location 61stAv_no_60thAv

# one study, PDF only, forced 35 mph
.venv\Scripts\python.exe scripts\generate_report.py --location 61stAv_no_60thAv --format pdf --speed-limit 35

# a whole year, all formats
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --all --format all

# EVERY study, PDF only, onto another drive (≈750 studies — large!)
.venv\Scripts\python.exe scripts\generate_report.py --all --format pdf --out "D:\pdf_out"

# validate one year / all years against Excel
.venv\Scripts\python.exe scripts\generate_report.py --year 2026 --validate
.venv\Scripts\python.exe scripts\generate_report.py --validate

# over-time CSV for one location
.venv\Scripts\python.exe scripts\generate_report.py --location 73rdAv_so_185thSt --trend
```

**Output layout:** `reports\<study_id>\<study_id>_report.{html,xlsx,pdf}`

---

## 3. `scripts/build_catalog.py` — (re)build the study catalog

```
.venv\Scripts\python.exe scripts\build_catalog.py [--base PATH] [--no-metrics]
```
Scans every year folder and writes `study_catalog.csv` into the **data** folder
(location × year × date × path + cached `avg_speed, p85_speed, adt, awdt`). It is
**incremental**: metrics for studies already in the CSV are reused; only new studies
are computed (the run prints e.g. `metrics: 8 new, 745 reused`). Pass `--no-metrics`
for a structure-only, instant rebuild. Runs automatically at launch and via the
dashboard's **🔄 Rebuild catalog** button.

---

## 4. `tools/backfill_limit_to_notes.py` — copy Excel limit into `_Notes.txt`

```
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py                # preview (no changes)
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py --apply        # write the files
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py --apply --no-create   # only append to existing notes
.venv\Scripts\python.exe tools\backfill_limit_to_notes.py --base PATH
```
Reads the posted limit from each `*_Report.xlsx` and adds a `Limit: <n>` line to that
folder's `_Notes.txt` (creating it if absent). Idempotent — folders that already have a
`Limit:` line are skipped. Dry-run by default.

---

## Caveats

- **`--include-compromised` is currently a no-op** — `generate_report.py` already
  includes `_Compromised Studies` / `_Incomplete` folders. (Wiring it to exclude bad
  studies by default is a small pending fix.)
- **`--all` with no `--year`** is ≈750 studies; prefer `--out` to a data drive and/or
  go year-by-year, especially for PDF.
- The catalog CSV lives in the data folder; if it's **open in Excel** the refresh can't
  overwrite it (it falls back to building in memory for that session).
