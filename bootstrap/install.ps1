<#
.SYNOPSIS
    Installs SynergyScan on this PC.

.DESCRIPTION
    Run this once, on the PC that has the scanner and the label printer
    plugged in. It does everything: fetches a Python runtime, downloads the
    current release, sets it up, checks that it works, and adds the shortcuts.

    The trained person's whole job is "double-click install.bat". This script
    is what makes that possible, and the README documents what it does and how
    to undo it.

    Nothing here needs Administrator. Everything lands under one folder and a
    shortcut goes in your own Startup folder.

.PARAMETER InstallRoot
    Where to install. Default C:\SynergyScan.

.PARAMETER Repo
    GitHub owner/name to install from.

.PARAMETER Channel
    stable (default) or beta. Sets which channel file this machine follows,
    which is how you make one PC a canary.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Channel beta
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\SynergyScan',
    [string]$Repo        = 'DuskMcDusk/SynergyScan',
    [ValidateSet('stable', 'beta')]
    [string]$Channel     = 'stable',
    [string]$PythonVersion = '3.12',
    [switch]$NoAutostart,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'

# Windows 10 PowerShell 5.1 still negotiates TLS 1.0 by default, which GitHub
# refuses. Without this every download below fails with an unhelpful error.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$UvUrl      = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
$ChannelUrl = "https://raw.githubusercontent.com/$Repo/main/channels/$Channel.json"

function Say  { param($m) Write-Host "  $m" }
function Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Fail { param($m) Write-Host "`nX  $m" -ForegroundColor Red; exit 1 }
function Good { param($m) Write-Host "OK $m" -ForegroundColor Green }

Write-Host ''
Write-Host '  SynergyScan installer' -ForegroundColor White
Write-Host '  ---------------------'
Say "Installing to      $InstallRoot"
Say "Release channel    $Channel"
Say "Source repository  $Repo"

# --------------------------------------------------------------- 1. folders
Step 'Creating folders'
# data\ deliberately sits beside versions\, never inside a release, so updates
# and rollbacks cannot touch the database, config or logs.
foreach ($d in 'versions', 'data', 'data\logs', 'data\backups', 'staging') {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot $d) | Out-Null
}
Good "folders ready under $InstallRoot"

# -------------------------------------------------------------------- 2. uv
Step 'Fetching uv (this also installs Python, so nothing else has to be)'
$uvExe = Join-Path $InstallRoot 'uv.exe'
if (Test-Path $uvExe) {
    Say 'uv.exe already present, keeping it'
} else {
    $uvZip = Join-Path $env:TEMP 'uv-windows.zip'
    try {
        Invoke-WebRequest -Uri $UvUrl -OutFile $uvZip -UseBasicParsing
    } catch {
        Fail "Could not download uv. Check the internet connection.`n   $($_.Exception.Message)"
    }
    $uvTmp = Join-Path $env:TEMP 'uv-extract'
    if (Test-Path $uvTmp) { Remove-Item -Recurse -Force $uvTmp }
    Expand-Archive -Path $uvZip -DestinationPath $uvTmp -Force
    $found = Get-ChildItem -Path $uvTmp -Filter 'uv.exe' -Recurse | Select-Object -First 1
    if (-not $found) { Fail 'The uv download did not contain uv.exe.' }
    Copy-Item $found.FullName $uvExe -Force
    Remove-Item -Recurse -Force $uvTmp, $uvZip -ErrorAction SilentlyContinue
}
& $uvExe python install $PythonVersion 2>&1 | Out-Null
Good "uv ready, Python $PythonVersion available"

# --------------------------------------------------------------- 3. channel
Step 'Asking which release to install'
try {
    $chanRaw = Invoke-WebRequest -Uri $ChannelUrl -UseBasicParsing
    $chan = $chanRaw.Content | ConvertFrom-Json
} catch {
    Fail "Could not read the release channel at`n   $ChannelUrl`n   $($_.Exception.Message)"
}
$version = $chan.version
$zipUrl  = $chan.url
$sha     = $chan.sha256
if (-not $version -or -not $zipUrl -or -not $sha) {
    Fail 'The release channel file is incomplete (needs version, url and sha256).'
}
Good "release $version"

# -------------------------------------------------------------- 4. download
Step "Downloading release $version"
$zipPath = Join-Path $InstallRoot "staging\synergyscan-$version.zip"
try {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
} catch {
    Fail "Could not download the release.`n   $($_.Exception.Message)"
}

# Integrity check, not a signature: over HTTPS the realistic risk is a
# truncated or corrupted download rather than a substituted file.
$got = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
if ($got -ne $sha.ToLower()) {
    Remove-Item $zipPath -Force
    Fail "The download is corrupt.`n   expected $sha`n   got      $got`n   Try again."
}
Good "downloaded and verified ($([math]::Round((Get-Item $zipPath).Length / 1MB, 1)) MB)"

# ---------------------------------------------------------------- 5. unpack
Step 'Unpacking'
$releaseDir = Join-Path $InstallRoot "versions\$version"
if (Test-Path $releaseDir) { Remove-Item -Recurse -Force $releaseDir }
Expand-Archive -Path $zipPath -DestinationPath $releaseDir -Force
if (-not (Test-Path (Join-Path $releaseDir 'pyproject.toml'))) {
    Fail 'The archive does not look like a SynergyScan release.'
}
Good "unpacked to $releaseDir"

# ------------------------------------------------------------------ 6. venv
Step 'Installing dependencies (a minute or two the first time)'
Push-Location $releaseDir
try {
    # --frozen installs exactly what uv.lock pins, so this machine gets the
    # versions that were tested rather than whatever resolves today.
    & $uvExe sync --frozen --no-dev
    if ($LASTEXITCODE -ne 0) { Fail 'Could not install the dependencies.' }
} finally {
    Pop-Location
}
$venvPy = Join-Path $releaseDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { Fail "No interpreter at $venvPy after setup." }
Good 'dependencies installed'

# ------------------------------------------------------------- 7. self-test
Step 'Checking the release actually works'
$env:SYNERGYSCAN_DATA = Join-Path $InstallRoot 'data'
& $venvPy -m synergyscan.selfupdate.selftest | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $venvPy -m synergyscan.selfupdate.selftest
    Fail 'The self-test failed, so this release was not activated. Nothing has been changed.'
}
Good 'self-test passed'

Step 'Setting up the database'
& $venvPy -m synergyscan.selfupdate.migrate | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $venvPy -m synergyscan.selfupdate.migrate
    Fail 'The database could not be set up. Nothing has been activated.'
}
Good 'database ready'

# --------------------------------------------------------------- 8. pointer
Step 'Activating this release'
# Written last, and only after every check above passed. Until this line, the
# release is inert.
Set-Content -Path (Join-Path $InstallRoot 'current.txt') -Value $version -Encoding ascii
Good "current.txt now reads $version"

# ------------------------------------------------------------- 9. bootstrap
Step 'Installing the launcher and helper scripts'
# These are copied OUT of the release on purpose. They live outside versions\
# so they survive updates and rollbacks - and so an update never rewrites the
# one piece that has to keep working when a release is broken. Changing them
# means re-running this installer, which is a rare and deliberate act.
$src = Join-Path $releaseDir 'bootstrap'
foreach ($f in 'launcher.py', 'rollback.bat', 'rollback.ps1', 'uninstall.ps1',
               'install.ps1', 'install.bat') {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $InstallRoot $f) -Force }
}

# Visible console: what you run when something is wrong and you want to watch.
@"
@echo off
cd /d "%~dp0"
"%~dp0uv.exe" run --no-project --python $PythonVersion launcher.py %*
pause
"@ | Set-Content -Path (Join-Path $InstallRoot 'SynergyScan-console.bat') -Encoding ascii

# Hidden: what the shortcuts use, so nobody sees a black window all day.
@"
' Starts SynergyScan with no console window.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$InstallRoot"
sh.Run """$InstallRoot\uv.exe"" run --no-project --python $PythonVersion launcher.py", 0, False
"@ | Set-Content -Path (Join-Path $InstallRoot 'SynergyScan.vbs') -Encoding ascii
Good 'launcher installed'

# ------------------------------------------------------------- 10. shortcuts
Step 'Adding shortcuts'
$shell = New-Object -ComObject WScript.Shell
$vbs = Join-Path $InstallRoot 'SynergyScan.vbs'

$desktop = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'SynergyScan.lnk'))
$desktop.TargetPath = $vbs
$desktop.WorkingDirectory = $InstallRoot
$desktop.Description = 'Open SynergyScan inventory'
$desktop.Save()
Good 'desktop shortcut'

if (-not $NoAutostart) {
    # Startup folder rather than a Scheduled Task: no elevation, nothing to
    # misconfigure, and the app is simply already running when someone sits
    # down. See the README if you need it to start before anyone logs on.
    $startup = [Environment]::GetFolderPath('Startup')
    $auto = $shell.CreateShortcut((Join-Path $startup 'SynergyScan.lnk'))
    $auto.TargetPath = $vbs
    $auto.WorkingDirectory = $InstallRoot
    $auto.Save()
    Good 'starts automatically when you log on'
}

Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

# ----------------------------------------------------------------- 11. start
Write-Host ''
Write-Host '  Installed.' -ForegroundColor Green
Say "Version    $version"
Say "Folder     $InstallRoot"
Say "Data       $InstallRoot\data   (the database lives here - back this up)"
Say 'Open with  the SynergyScan icon on the desktop'
Say '           or http://localhost:8000 in a browser'
Write-Host ''

if (-not $NoStart) {
    Step 'Starting SynergyScan'
    Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`"" | Out-Null
    Say 'A browser window should open in a few seconds.'
}
