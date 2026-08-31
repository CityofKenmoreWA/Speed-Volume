@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  Kenmore Traffic Study Dashboard  -  single entry point
REM
REM  DOUBLE-CLICK this file for a menu.
REM
REM  For the server, call it with an argument (no menu, no prompts):
REM     KenmoreTrafficDashboard.bat serve      the always-on dashboard (service)
REM     KenmoreTrafficDashboard.bat refresh    the catalog update (scheduled task)
REM
REM  Python is bundled in the python\ folder. Nothing is installed on the machine.
REM ===========================================================================


REM ####################  CONFIGURATION - EDIT HERE  ##########################

REM Where the study data lives. This is the UNC path on purpose: it works both
REM from a normal login AND from a service. A drive letter like V: does NOT work
REM for a service, because drive mappings belong to a logged-in session.
set "TRAFFIC_DATA_BASE=\\cok-fs1\departments\Public Works\Engineering\TRAFFIC\Traffic Studies\Speed and Volume Studies"

REM Only if the UNC path ever fails, and only when running by hand. Never for
REM the service. Remove the REM at the start of the next line to use it.
REM set "TRAFFIC_DATA_BASE=V:\Public Works\Engineering\TRAFFIC\Traffic Studies\Speed and Volume Studies"

REM Figure resolution in the reports. 250 = print quality.
set "TRAFFIC_FIGURE_DPI=250"

REM Port the dashboard listens on.
set "DASH_PORT=8501"

REM Which network address the SERVER install listens on. 0.0.0.0 = every network
REM card, which is what lets other machines reach it. Who may actually connect is
REM decided by the Windows Firewall rule that Setup-Server.ps1 creates, not here.
REM Only "serve" mode uses this; double-clicking the .bat is always local-only.
set "DASH_BIND=0.0.0.0"

REM ##################  END CONFIGURATION  ####################################


cd /d "%~dp0"
set "APP_ROOT=%~dp0"
set "PY=%~dp0python\python.exe"

REM Keep the figure font cache inside this folder. A service account usually has
REM no loaded Windows profile, and without this matplotlib rebuilds its cache in a
REM brand-new temp folder on every single start.
set "MPLCONFIGDIR=%~dp0cache\matplotlib"
if not exist "%~dp0cache" mkdir "%~dp0cache" 2>nul
if not exist "%~dp0logs" mkdir "%~dp0logs" 2>nul

if not exist "%PY%" goto :no_python

if /i "%~1"=="serve"   goto :serve
if /i "%~1"=="refresh" goto :refresh
if /i "%~1"=="start"   goto :start_local
if "%~1"==""           goto :menu

echo.
echo  Unknown option "%~1"
echo  Use:  serve   ^|  refresh   ^|  start    (or no option for the menu)
echo.
exit /b 64


REM ---------------------------------------------------------------- menu -----
:menu
cls
echo.
echo   ===============================================================
echo    KENMORE TRAFFIC STUDY DASHBOARD
echo   ===============================================================
echo.
echo    Study folder:
echo      %TRAFFIC_DATA_BASE%
echo.
if not exist "%TRAFFIC_DATA_BASE%" (
  echo    *** WARNING: that folder is NOT reachable from this computer.
  echo    *** Check the path in the CONFIGURATION section of this file.
  echo.
)
echo   ---------------------------------------------------------------
echo.
echo     1.  Start the dashboard  ^(opens in your browser^)
echo.
echo     2.  Update the study list now
echo         ^(first time takes about 10 minutes, later runs seconds^)
echo.
echo     3.  What is my setup?  ^(show versions and paths^)
echo.
echo     Q.  Quit
echo.
echo   ---------------------------------------------------------------
echo.
set "opt="
set /p "opt=   Type 1, 2, 3 or Q then press Enter:  "
echo.
if /i "%opt%"=="1" goto :start_local
if /i "%opt%"=="2" goto :refresh_interactive
if /i "%opt%"=="3" goto :info
if /i "%opt%"=="q" goto :done
goto :menu


REM ------------------------------------------------- 1. local dashboard ------
:start_local
if not exist "%TRAFFIC_DATA_BASE%" goto :no_share
echo  Starting the dashboard...
echo.
echo  Your browser will open at http://localhost:%DASH_PORT%
echo  LEAVE THIS WINDOW OPEN. Close it to stop the dashboard.
echo.
start "" "http://localhost:%DASH_PORT%"
"%PY%" -m streamlit run "app\streamlit_app.py" --server.port %DASH_PORT%
echo.
echo  Dashboard stopped.
pause
exit /b 0


REM ------------------------------------------- 2. catalog refresh (by hand) --
:refresh_interactive
if not exist "%TRAFFIC_DATA_BASE%" goto :no_share
echo  Updating the study list. This writes one file to the share
echo  ^(study_catalog.csv^) and changes nothing else.
echo.
echo  The FIRST run checks every study and takes around 10 minutes.
echo  Later runs only look at what changed and take seconds.
echo.
"%PY%" "scripts\build_catalog.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo  Done - the study list is up to date.
if "%RC%"=="1" echo  FAILED - the study folder was not reachable.
if "%RC%"=="2" echo  FAILED - could not write study_catalog.csv. Is it open in Excel?
echo.
pause
goto :menu


REM ------------------------------------------------------- 3. diagnostics ----
:info
echo   Bundled Python:
"%PY%" -c "import sys; print('     ', sys.version.split()[0], '-', sys.executable)"
echo.
echo   Libraries:
"%PY%" -c "import importlib.metadata as m; [print('     ', p, m.version(p)) for p in ('streamlit','pandas','numpy','matplotlib','reportlab','openpyxl','XlsxWriter','jinja2')]"
echo.
echo   Study folder:  %TRAFFIC_DATA_BASE%
if exist "%TRAFFIC_DATA_BASE%" (echo   Reachable:     YES) else (echo   Reachable:     NO)
echo.
echo   Study list:
"%PY%" -c "import os,sys; sys.path.insert(0,os.getcwd()); from traffic_diag.config import DEFAULT_BASE; from traffic_diag.catalog import read_catalog, catalog_path; d=read_catalog(DEFAULT_BASE); p=catalog_path(DEFAULT_BASE); import datetime as dt; print('     ', 0 if d is None else len(d), 'studies'); print('     last updated', dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime('%%Y-%%m-%%d %%H:%%M') if os.path.exists(p) else 'never')"
echo.
pause
goto :menu


REM -------------------------------------------------- serve (service mode) ---
:serve
REM Always-on dashboard, run by the "KenmoreTrafficDashboard-Serve" scheduled task
REM that Setup-Server.ps1 creates. Listens on DASH_BIND (see CONFIGURATION above);
REM the firewall rule is what limits this to the local network.
"%PY%" -m streamlit run "app\streamlit_app.py" --server.address %DASH_BIND% --server.port %DASH_PORT% >> "%~dp0logs\dashboard.log" 2>&1
exit /b %ERRORLEVEL%


REM ---------------------------------------------- refresh (scheduled task) --
:refresh
REM Exit codes: 0 updated | 1 share unreachable | 2 could not write the CSV.
"%PY%" "scripts\build_catalog.py" >> "%~dp0logs\catalog.log" 2>&1
exit /b %ERRORLEVEL%


REM ------------------------------------------------------------- errors ------
:no_python
echo.
echo  ERROR: the bundled Python is missing. Expected it here:
echo    %PY%
echo.
echo  The python\ folder has to be copied along with everything else.
echo  Copy the WHOLE folder, not just the loose files.
echo.
pause
exit /b 1

:no_share
echo.
echo  ERROR: the study folder is not reachable from this computer:
echo    %TRAFFIC_DATA_BASE%
echo.
echo  Fix the path in the CONFIGURATION section near the top of this file.
echo.
pause
goto :menu

:done
endlocal
exit /b 0
