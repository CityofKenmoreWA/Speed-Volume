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
set "TRAFFIC_DATA_BASE=V:\Public Works\Engineering\Traffic Studies\Speed and Volume Studies"
REM ==========================================================================

cd /d "%APP_ROOT%"

REM Stop any Streamlit instance already running this app (any Python, venv or global)
REM so we never end up with two servers fighting over port 8501.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'streamlit run' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM Build/refresh the study catalog table (all locations x years) before the app opens.
".venv\Scripts\python.exe" "scripts\build_catalog.py"

".venv\Scripts\python.exe" -m streamlit run "app\streamlit_app.py" --server.address 0.0.0.0 --server.port 8501
pause
