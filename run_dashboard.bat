@echo off
REM Double-click this file to launch the Traffic Study dashboard.
REM It uses the project's virtual environment and opens the app in your browser.

REM ===================== CONFIGURATION (edit these two) =====================
REM APP_ROOT: where the app lives. Everything the app produces (reports, catalog
REM handling, etc.) is anchored here. Defaults to this .bat file's own folder, so
REM you can move the whole project and it keeps working.
set "APP_ROOT=%~dp0"

REM TRAFFIC_DATA_BASE: the actual traffic-study DATA folder (the one holding the
REM <year> subfolders). This is the only directory outside the app; point it
REM wherever the data lives. Current default shown below.
set "TRAFFIC_DATA_BASE=V:\Public Works\Engineering\TRAFFIC\Traffic Studies\Speed and Volume Studies"

REM REFRESH_CATALOG: 1 to refresh the study list on launch (in the background, so
REM it never delays the dashboard), 0 to skip it and use what is already on disk.
set "REFRESH_CATALOG=1"
REM ==========================================================================

cd /d "%APP_ROOT%"

REM Stop any Streamlit instance already running this app (any Python, venv or global)
REM so we never end up with two servers fighting over port 8501.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'streamlit run' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM Refresh the study catalog IN THE BACKGROUND, so the dashboard opens straight
REM away instead of making you watch a console.
REM
REM This used to run in the foreground, which was fine while a refresh was
REM sub-second - it reuses the cached numbers for every unchanged study. But any
REM edit to a study's _Raw.csv, _Notes.txt or _Report.xlsx changes that study's
REM fingerprint and forces a recompute, so when many change at once the wait is
REM minutes. Worse, closing the window part-way meant the refreshed catalog was
REM never written, so the next launch started the same wait all over again.
REM
REM The dashboard does not need to wait: it reads the catalog CSV already on
REM disk, and picks up the refreshed one by itself when it lands, because it
REM watches that file's timestamp.
if not exist "reports" mkdir "reports"
if "%REFRESH_CATALOG%"=="1" (
  echo Refreshing the study list in the background ^(see reports\catalog_refresh.log^)
  start "Kenmore catalog refresh" /min cmd /c ".venv\Scripts\python.exe scripts\build_catalog.py >> reports\catalog_refresh.log 2>&1"
)

".venv\Scripts\python.exe" -m streamlit run "app\streamlit_app.py" --server.address 0.0.0.0 --server.port 8501
pause
