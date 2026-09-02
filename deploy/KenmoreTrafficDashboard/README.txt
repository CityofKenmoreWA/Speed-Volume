===========================================================================
 KENMORE TRAFFIC STUDY DASHBOARD
===========================================================================

 A read-only dashboard over the Speed and Volume study tree. Pick a location
 and a year, and get statistics, tables, figures, diagnostics, and
 HTML / Excel / PDF downloads.

 Nothing needs to be installed. Python and every library are inside the
 python\ folder. No installer, no admin rights, no PATH changes.

 There is ONE file to run: KenmoreTrafficDashboard.bat


---------------------------------------------------------------------------
 TO USE IT ON YOUR OWN COMPUTER
---------------------------------------------------------------------------

 Double-click KenmoreTrafficDashboard.bat and pick from the menu:

    1.  Start the dashboard      opens in your browser
    2.  Update the study list    refreshes the location / year picker
    3.  What is my setup?        versions, paths, study count

 To stop the dashboard, close the black console window.


---------------------------------------------------------------------------
 TO RUN IT 24/7 ON A SERVER
---------------------------------------------------------------------------

 There are two steps, and the second one is a script that does the rest.

 STEP 1 - Copy the whole folder to the server
 --------------------------------------------
 Anywhere local, for example  C:\Apps\KenmoreTrafficDashboard
 Copy the WHOLE folder. The python\ folder is what makes it run without
 installing anything.

 Open KenmoreTrafficDashboard.bat in Notepad and check the CONFIGURATION
 block at the top - mainly that TRAFFIC_DATA_BASE is right.

 STEP 2 - Run Setup-Server.ps1 once, as Administrator
 ----------------------------------------------------
 Right-click Start -> Windows PowerShell (Admin), then:

   cd C:\Apps\KenmoreTrafficDashboard
   powershell -ExecutionPolicy Bypass -File .\Setup-Server.ps1

 It asks for one thing: the account the dashboard should run as. Then it:

   - checks the folder, the bundled Python and the study share
   - creates the scheduled task KenmoreTrafficDashboard-Serve
       starts the dashboard at boot, restarts it if it stops
   - creates the scheduled task KenmoreTrafficDashboard-Refresh
       updates the study list every 5 minutes
   - opens TCP 8501 in Windows Firewall to the LOCAL NETWORK ONLY
       (Domain and Private profiles; never on a Public network)
   - starts the dashboard, waits for it to answer, prints the address

 Re-running it is safe. It replaces what it made last time.

 To take it all back off again:

   powershell -ExecutionPolicy Bypass -File .\Setup-Server.ps1 -Uninstall

 Useful options:

   -Port 8080                            use a different port
                                         (also change DASH_PORT in the .bat)
   -AllowedRemoteAddress "10.20.0.0/16"  allow only that range instead of
                                         the whole local subnet
   -SkipFirewall                         create the tasks, leave the
                                         firewall alone

 No NSSM, no downloads, no server roles to add. Both jobs are ordinary
 Scheduled Tasks.

 Logs:  logs\dashboard.log   the dashboard
        logs\catalog.log     the study-list update
                             exit 0 = updated, 1 = share unreachable,
                             2 = could not write study_catalog.csv
                                 (open in Excel? no write permission?)


 THERE IS NO LOGIN
 -----------------
 Anyone who can reach the server on the local network can open the dashboard
 and read every study, including the installation photos and the GPS
 locations recorded in them. The firewall rule limits WHERE people connect
 from, not WHO they are. That was a deliberate choice for this deployment.

 If a login is wanted later, the usual route in an AD shop is IIS with
 Windows Authentication reverse-proxying to http://127.0.0.1:8501 - set
 DASH_BIND=127.0.0.1 in the .bat and re-run the setup script with
 -SkipFirewall. The proxy has to forward the "Upgrade" and "Connection"
 headers, or the page loads and then hangs on "connecting".


---------------------------------------------------------------------------
 PERMISSIONS THE SERVICE ACCOUNT NEEDS
---------------------------------------------------------------------------

   ...\Speed and Volume Studies  and everything under it ....... READ
   ...\Speed and Volume Studies\study_catalog.csv .............. MODIFY
   The app folder on the server (writes logs\ and cache\) ...... MODIFY

 That is all. Every study FOLDER stays read-only - the only file anything
 here writes on the share is study_catalog.csv, and normally only the
 "refresh" task writes it. One exception: if study_catalog.csv is missing
 altogether, the dashboard writes a bare version of it so the page can still
 open. Letting the refresh task create it first avoids that entirely.


---------------------------------------------------------------------------
 SETTINGS
---------------------------------------------------------------------------

 Everything is in the CONFIGURATION block at the top of
 KenmoreTrafficDashboard.bat - open it in Notepad.

   TRAFFIC_DATA_BASE     the study folder (the one holding 2019, 2020, ...)
   TRAFFIC_FIGURE_DPI    report figure quality; 250 is print quality
   DASH_PORT             port the dashboard uses (8501)
   DASH_BIND             which address the SERVER install listens on
                         (0.0.0.0 = every network card). Double-clicking
                         the .bat is always local-only and ignores this.

 The path is set to the UNC form (\\cok-fs1\departments\...) on purpose: it
 works both from a normal login and from a service. A drive letter like V:
 does NOT work for a service, because drive mappings only exist inside a
 logged-in session. A commented V: line is there as a fallback.


---------------------------------------------------------------------------
 THINGS WORTH KNOWING
---------------------------------------------------------------------------

 The numbers changed in 2026, on purpose. This app no longer reproduces the
 old Excel workbook; several of the workbook's formulas were wrong and the
 correct calculation is used instead. Reports produced now will not match
 archived Excel reports cell for cell. The differences that matter:

   - "Weekday" means Monday to Friday. The workbook's hourly weekday columns
     used Monday to Thursday and silently dropped Friday.
   - Hourly speed summaries pool the actual speeds. The workbook averaged
     each day's average, so a day with one vehicle counted as much as a day
     with two hundred.
   - The morning peak hour must now START by 11:00 AM, and the evening peak
     between 12:00 PM and 11:00 PM. The workbook's morning search ran to an
     11:45 AM start, so it could report 11:45 AM - 12:45 PM as the "AM peak".
   - The 85th-percentile speed uses the true Monday-to-Friday days in the
     window, not the first five calendar days of it.

 Nothing needs to be done about this. It is recorded here so that a number
 that differs from an old report is not mistaken for a fault.


 The study list updates itself. There is no rebuild button and no data
 folder box in the dashboard - both were removed on purpose. The scheduled
 task owns the study list.

 A dashboard that is already open notices an update by itself. It watches
 the study list's timestamp, so the next click shows the new data. No
 restart, nothing to press.

 How changes are spotted. Each study is stamped with the newest modified
 time and total size of its _Raw.csv, _Notes.txt and _Report.xlsx files.
 Numbers are reused only when the folder AND that stamp both match. So a
 new study appears, a deleted one drops off, and a study that was corrected
 in place gets recalculated - a re-pulled _Raw.csv, a "Limit:" added to
 _Notes.txt, a replaced _Report.xlsx.

 The first update is slow. The very first run checks every study and takes
 about 10 minutes over the share. Later runs take seconds. (This has
 already been done once on the live share.)

 Long file names. Six files under the study root have names so long they
 exceed the old Windows 260-character limit and cannot be opened. This is
 not new and none of them belong to a study the dashboard uses, so nothing
 is broken. Optional fix on the server, from an admin command prompt:

   reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f

 Then reboot.


---------------------------------------------------------------------------
 IF SOMETHING GOES WRONG
---------------------------------------------------------------------------

 "bundled Python is missing"
     The python\ folder did not come across. Copy the whole folder.

 "the study folder is not reachable"
     The account cannot see the share, or the path in the .bat is wrong.

 "No studies found"
     The path resolved but has no year folders in it. It must point AT
     "Speed and Volume Studies", not above or below it.

 Recent studies missing from the picker
     The scheduled task is not running, or is failing. Check
     logs\catalog.log and the task's Last Run Result in Task Scheduler.

 It works on the server itself, but nobody else can reach it
     The firewall rule is missing or too narrow, or the server's network is
     classified Public. Check:
       Get-NetFirewallRule -DisplayName "Kenmore Traffic Dashboard"
       Get-NetConnectionProfile
     DASH_BIND in the .bat has to be 0.0.0.0, not 127.0.0.1.

 The dashboard is not running after a reboot
     Check KenmoreTrafficDashboard-Serve in Task Scheduler, and
     logs\dashboard.log. The usual cause is the service account's password
     having changed - re-run Setup-Server.ps1 to store the new one.

 The first report download is slow
     Normal. Each study's downloads are built once, then reused.


---------------------------------------------------------------------------
 WHAT IS IN THIS FOLDER
---------------------------------------------------------------------------

   KenmoreTrafficDashboard.bat   the only thing you run; settings inside it
   Setup-Server.ps1              one-shot server install (Administrator)
   python\                       Python 3.12.10 and every library
   app\  traffic_diag\           the application itself
   scripts\                      the study-list updater
   assets\                       city logos
   .streamlit\                   server settings (port, no telemetry)
   logs\                         log files
   cache\                        scratch files; safe to delete
   requirements.txt              the exact library versions bundled
   README.txt                    this file

 The development repo also contains tools that WRITE INTO study folders.
 They are deliberately not included here.

===========================================================================
