@echo off
REM Double-click this file to launch the Traffic Study dashboard.
REM It uses the project's virtual environment and opens the app in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run "app\streamlit_app.py"
pause
