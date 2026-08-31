<#
.SYNOPSIS
    One-shot server install for the Kenmore Traffic Study Dashboard.

.DESCRIPTION
    Run this ONCE, as Administrator, on the server, from inside the copied
    KenmoreTrafficDashboard folder. It does every server-side step:

      1. checks the folder, the bundled Python, and the study share
      2. registers "KenmoreTrafficDashboard-Serve"    - runs the dashboard at startup
      3. registers "KenmoreTrafficDashboard-Refresh"  - updates the study list every 5 min
      4. opens the port in Windows Firewall to the local network only
      5. starts the dashboard and waits until it answers
      6. prints the address to hand out, and how to check on it later

    There is no NSSM and nothing to download: both jobs are ordinary Scheduled
    Tasks. Re-running the script is safe - it replaces what it made last time.

    NOTE ON ACCESS: this install has NO login. Anyone who can reach the server on
    the local network can open the dashboard and read every study. The firewall
    rule below is the only limit, and it limits WHERE FROM, not WHO.

.PARAMETER Port
    TCP port the dashboard listens on. Default 8501. Must match DASH_PORT in
    KenmoreTrafficDashboard.bat.

.PARAMETER AllowedRemoteAddress
    Who may connect, as Windows Firewall remote addresses. Default "LocalSubnet"
    (the server's own network). Pass explicit ranges to be stricter, e.g.
    -AllowedRemoteAddress "10.20.0.0/16","10.30.4.0/24"

.PARAMETER DataBase
    The study folder (the one holding the year folders). Read from the
    CONFIGURATION block of KenmoreTrafficDashboard.bat when not given.

.PARAMETER SkipFirewall
    Register the scheduled tasks but do not touch Windows Firewall.

.PARAMETER Uninstall
    Remove both scheduled tasks and the firewall rule, then exit. Leaves the
    folder and the study share untouched.

.EXAMPLE
    .\Setup-Server.ps1
    Standard install. Prompts for the service account to run as.

.EXAMPLE
    .\Setup-Server.ps1 -AllowedRemoteAddress "10.20.0.0/16"
    Install, but allow only that range instead of the whole local subnet.

.EXAMPLE
    .\Setup-Server.ps1 -Uninstall
    Remove the tasks and the firewall rule.
#>
[CmdletBinding()]
param(
    [int]      $Port = 8501,
    [string[]] $AllowedRemoteAddress = @('LocalSubnet'),
    [string]   $DataBase,
    [switch]   $SkipFirewall,
    [switch]   $Uninstall
)

$ErrorActionPreference = 'Stop'

$TASK_SERVE   = 'KenmoreTrafficDashboard-Serve'
$TASK_REFRESH = 'KenmoreTrafficDashboard-Refresh'
$FW_RULE      = 'Kenmore Traffic Dashboard'

$Root = $PSScriptRoot
$Bat  = Join-Path $Root 'KenmoreTrafficDashboard.bat'

function Write-Step { param($m) Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    [ok]   $m" -ForegroundColor Green }
function Write-Note { param($m) Write-Host "    $m" -ForegroundColor Gray }
function Write-Warn { param($m) Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Stop-Setup {
    param($m)
    Write-Host ''
    Write-Host "  ERROR: $m" -ForegroundColor Red
    Write-Host ''
    exit 1
}

# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin  = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
                [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Stop-Setup 'Run this from an Administrator PowerShell window (right-click -> Run as administrator).'
}

Write-Host ''
Write-Host '  ===============================================================' -ForegroundColor White
Write-Host '   KENMORE TRAFFIC STUDY DASHBOARD  -  server install' -ForegroundColor White
Write-Host '  ===============================================================' -ForegroundColor White

# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #
if ($Uninstall) {
    Write-Step 'Removing scheduled tasks'
    foreach ($t in @($TASK_SERVE, $TASK_REFRESH)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            try { Stop-ScheduledTask -TaskName $t -ErrorAction Stop } catch { }
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Ok "removed $t"
        }
        else {
            Write-Note "$t was not present"
        }
    }

    Write-Step 'Removing the firewall rule'
    $existing = Get-NetFirewallRule -DisplayName $FW_RULE -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | Remove-NetFirewallRule
        Write-Ok "removed '$FW_RULE'"
    }
    else {
        Write-Note 'no rule to remove'
    }

    Write-Host ''
    Write-Host '  Uninstalled. The app folder and the study share were not touched.' -ForegroundColor White
    Write-Host ''
    exit 0
}

Write-Step 'Checking the folder'
if (-not (Test-Path $Bat)) {
    Stop-Setup 'KenmoreTrafficDashboard.bat is not next to this script. Run the script from inside the copied folder.'
}
$py = Join-Path $Root 'python\python.exe'
if (-not (Test-Path $py)) {
    Stop-Setup "The bundled Python is missing ($py). The whole folder has to be copied, python\ included."
}
$pyVersion = & $py -c 'import sys; print(sys.version.split()[0])'
Write-Ok "app folder    $Root"
Write-Ok "python        $pyVersion"

foreach ($d in @('logs', 'cache')) {
    $p = Join-Path $Root $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

Write-Step 'Checking the study folder'
if (-not $DataBase) {
    # Pull it out of the CONFIGURATION block. The '^\s*set' anchor skips the
    # commented-out "REM set ..." drive-letter fallback line.
    $hit = Select-String -Path $Bat -Pattern '^\s*set\s+"TRAFFIC_DATA_BASE=(.+)"\s*$' |
           Select-Object -First 1
    if (-not $hit) {
        Stop-Setup 'Could not find TRAFFIC_DATA_BASE in KenmoreTrafficDashboard.bat. Pass -DataBase instead.'
    }
    $DataBase = $hit.Matches[0].Groups[1].Value
}
Write-Note $DataBase

if (Test-Path $DataBase) {
    $years = @(Get-ChildItem -Path $DataBase -Directory -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -match '^\d{4}$' })
    Write-Ok "reachable from THIS account ($($years.Count) year folders)"
    if ($years.Count -eq 0) {
        Write-Warn 'No year folders in there. The path must point AT "Speed and Volume Studies".'
    }
}
else {
    Write-Warn 'Not reachable from the account running this script.'
    Write-Warn 'What matters is the service account below - if that one can see it, carry on.'
}

if ($DataBase -match '^[A-Za-z]:\\') {
    Write-Warn 'That is a drive letter. Mapped drives do not exist for a scheduled task.'
    Write-Warn 'Use the UNC path (\\server\share\...) in KenmoreTrafficDashboard.bat instead.'
}

# --------------------------------------------------------------------------- #
# Service account.
#
# A scheduled task needs a stored password (LogonType Password) to reach a
# network share; "run only when the user is logged on" and S4U both fail there.
# The password is typed into the standard Windows credential prompt and handed
# straight to Task Scheduler - this script never stores, logs, or echoes it.
# --------------------------------------------------------------------------- #
Write-Step 'Account the dashboard will run as'
Write-Note 'Needs: READ on the study folder,'
Write-Note '       MODIFY on study_catalog.csv inside it,'
Write-Note '       MODIFY on this app folder (it writes logs\ and cache\).'
Write-Note 'It does NOT need to be an administrator.'
Write-Host ''
$cred = Get-Credential -Message 'Account for the dashboard tasks (e.g. COK\svc_trafficdash)'
if (-not $cred) { Stop-Setup 'No account supplied.' }

$user = $cred.UserName
$pass = $cred.GetNetworkCredential().Password
if ([string]::IsNullOrEmpty($pass)) {
    Stop-Setup 'That account has no password. A scheduled task cannot reach a network share without one.'
}
Write-Ok "will run as $user"

# --------------------------------------------------------------------------- #
# Scheduled tasks
# --------------------------------------------------------------------------- #
function Register-DashTask {
    param($Name, $Argument, $Trigger, $Settings, $Description)
    $action = New-ScheduledTaskAction -Execute $Bat -Argument $Argument -WorkingDirectory $Root
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings $Settings -Description $Description `
        -User $user -Password $pass -RunLevel Limited -Force | Out-Null
}

Write-Step "Registering '$TASK_SERVE' (the dashboard itself)"
$serveTrigger = New-ScheduledTaskTrigger -AtStartup
# ExecutionTimeLimit 0 = never time out; this one is meant to run forever.
# IgnoreNew stops a second copy fighting over the port if the task fires again.
$serveSettings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-DashTask -Name $TASK_SERVE -Argument 'serve' -Trigger $serveTrigger `
    -Settings $serveSettings `
    -Description 'Kenmore Traffic Study Dashboard - always-on web app. Logs to logs\dashboard.log.'
Write-Ok 'starts at boot, restarts itself if it stops'

Write-Step "Registering '$TASK_REFRESH' (the study list)"
# Ten years rather than [TimeSpan]::MaxValue: the "indefinitely" sentinel is
# rejected by some Windows builds, and ten years is the same thing in practice.
$refreshTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$refreshSettings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-DashTask -Name $TASK_REFRESH -Argument 'refresh' -Trigger $refreshTrigger `
    -Settings $refreshSettings `
    -Description 'Kenmore Traffic Study Dashboard - refreshes study_catalog.csv every 5 minutes. Logs to logs\catalog.log. Exit 0 = ok, 1 = share unreachable, 2 = could not write the CSV.'
Write-Ok 'every 5 minutes; the first run may take ~10 minutes'

$pass = $null   # done with it

# --------------------------------------------------------------------------- #
# Firewall
# --------------------------------------------------------------------------- #
if ($SkipFirewall) {
    Write-Step 'Firewall'
    Write-Warn '-SkipFirewall was given; no rule created, so nothing off this server can connect yet.'
}
else {
    Write-Step "Opening TCP $Port to $($AllowedRemoteAddress -join ', ')"
    $old = Get-NetFirewallRule -DisplayName $FW_RULE -ErrorAction SilentlyContinue
    if ($old) { $old | Remove-NetFirewallRule }
    # Domain + Private only: if this NIC is ever classified Public, the port stays shut.
    New-NetFirewallRule -DisplayName $FW_RULE -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -RemoteAddress $AllowedRemoteAddress `
        -Profile Domain, Private `
        -Description 'Kenmore Traffic Study Dashboard (Streamlit). Local network only.' | Out-Null
    Write-Ok "rule '$FW_RULE' created (Domain and Private profiles only)"
}

# --------------------------------------------------------------------------- #
# Start it and wait for it to answer
# --------------------------------------------------------------------------- #
Write-Step 'Starting the dashboard'
Start-ScheduledTask -TaskName $TASK_SERVE
$health = "http://127.0.0.1:$Port/_stcore/health"
$up = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $up = $true; break }
    }
    catch { }
}
if ($up) {
    Write-Ok "answering on port $Port"
}
else {
    Write-Warn "No answer on $health after 60 seconds."
    Write-Warn "Check logs\dashboard.log, and the task's Last Run Result in Task Scheduler."
    Write-Warn 'The usual cause is the service account not being able to read the study folder.'
}

# --------------------------------------------------------------------------- #
# Hand-off summary
# --------------------------------------------------------------------------- #
$ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
         Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
         Select-Object -ExpandProperty IPAddress -Unique)

Write-Host ''
Write-Host '  ===============================================================' -ForegroundColor White
Write-Host '   DONE' -ForegroundColor Green
Write-Host '  ===============================================================' -ForegroundColor White
Write-Host ''
Write-Host '   Give people this address:' -ForegroundColor White
foreach ($ip in $ips) {
    Write-Host "     http://${ip}:$Port" -ForegroundColor Yellow
}
Write-Host "     http://$($env:COMPUTERNAME):$Port" -ForegroundColor Yellow
Write-Host ''
Write-Host '   NO LOGIN IS CONFIGURED. Anyone who can reach this server on the' -ForegroundColor Yellow
Write-Host '   local network can open the dashboard and read every study,' -ForegroundColor Yellow
Write-Host '   including the installation photos and their GPS locations.' -ForegroundColor Yellow
Write-Host ''
Write-Host '   Day to day:' -ForegroundColor White
Write-Host "     Task Scheduler   $TASK_SERVE"
Write-Host "                      $TASK_REFRESH"
Write-Host "     Dashboard log    $(Join-Path $Root 'logs\dashboard.log')"
Write-Host "     Study list log   $(Join-Path $Root 'logs\catalog.log')"
Write-Host "     Restart          Restart-ScheduledTask -TaskName $TASK_SERVE"
Write-Host '     Remove           .\Setup-Server.ps1 -Uninstall'
Write-Host ''
