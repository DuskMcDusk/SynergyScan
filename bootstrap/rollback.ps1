<#
.SYNOPSIS
    Goes back to the previous version of SynergyScan.

.DESCRIPTION
    Because every release stays on disk in its own folder, going back is one
    line in current.txt - not a reinstall. That is the whole reason the
    versioned layout exists, and this script is what makes it usable by
    someone who has been told "run rollback, then call us".

    It changes nothing except which release runs. The database, settings and
    logs in data\ are untouched.

    Note: if the newer release applied a database migration, the older release
    may not understand the schema. The pre-update backups are in
    data\backups\ - restoring one is a support job, not a self-service one, so
    this script tells you rather than guessing.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = $PSScriptRoot,
    [string]$To,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
if (-not $InstallRoot) { $InstallRoot = 'C:\SynergyScan' }

$pointer  = Join-Path $InstallRoot 'current.txt'
$versions = Join-Path $InstallRoot 'versions'

function Fail { param($m) Write-Host "`nX  $m" -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }

if (-not (Test-Path $versions)) { Fail "No versions folder at $versions. Is SynergyScan installed here?" }

# Zero-padded so a plain string sort is a numeric sort (int[] is not comparable).
# $null for anything that is not a release version: current.txt can hold junk,
# and with ErrorActionPreference Stop an [int] cast on it would end the script.
function VersionKey {
    param($n)
    if ([string]$n -notmatch '^\d{4}\.\d{1,2}\.\d{1,2}-\d+$') { return $null }
    (($n -split '[.-]' | ForEach-Object { '{0:D6}' -f [int]$_ }) -join '.')
}

$installed = @(Get-ChildItem -Path $versions -Directory |
    Where-Object { $_.Name -match '^\d{4}\.\d{1,2}\.\d{1,2}-\d+$' } |
    Where-Object { Test-Path (Join-Path $_.FullName '.venv\Scripts\python.exe') } |
    Sort-Object -Property @{ Expression = { VersionKey $_.Name } } -Descending)

if ($installed.Count -lt 1) { Fail 'No usable releases are installed.' }

$current = ''
# -Raw gives $null for an empty file, so go through a string before Trim().
if (Test-Path $pointer) { $current = ([string](Get-Content $pointer -Raw)).Trim() }

Write-Host ''
Write-Host '  SynergyScan rollback' -ForegroundColor White
Write-Host '  --------------------'
Write-Host "  Currently running: $current"
Write-Host '  Installed releases:'
foreach ($v in $installed) {
    $mark = ''
    if ($v.Name -eq $current) { $mark = '  <- current' }
    Write-Host "    $($v.Name)$mark"
}

if ($To) {
    $target = ($installed | Where-Object { $_.Name -eq $To } | Select-Object -First 1)
    if (-not $target) { Fail "Release $To is not installed." }
    $targetName = $target.Name
} else {
    # Newest release that is older than the current one (list is newest first).
    $currentInstalled = $current -and ($installed | Where-Object { $_.Name -eq $current })
    if ($currentInstalled) {
        # Ordinal compare: the keys are zero-padded, and -lt is culture-sensitive.
        $curKey = VersionKey $current
        $older = @($installed | Where-Object {
            [string]::CompareOrdinal((VersionKey $_.Name), $curKey) -lt 0 })
        if ($older.Count -lt 1) { Fail 'There is no older release to go back to.' }
        $targetName = $older[0].Name
    } else {
        # current.txt is missing or names a release that is not usable: the
        # newest working release is the sensible place to land.
        $targetName = $installed[0].Name
    }
}

Write-Host ''
$answer = Read-Host "  Switch to $targetName? (y/N)"
if ($answer -ne 'y' -and $answer -ne 'Y') {
    Write-Host '  Cancelled. Nothing changed.'
    exit 0
}

# Decide everything that can fail before anything is stopped: from the moment
# the app is stopped until it is started again below, the shop has nothing.
# Going back blocks the release being left, or the older release would just
# update straight back to it. Going forward (or from junk) blocks nothing.
$curKey = VersionKey $current
$targetKey = VersionKey $targetName
$blockCurrent = [bool]($curKey -and $targetKey -and
    [string]::CompareOrdinal($targetKey, $curKey) -lt 0)

# Stop the launcher first, or it restarts the app 3 seconds after we kill it
# and races the launcher we start below for the port.
# The launcher is started as "<InstallRoot>\uv.exe run ... launcher.py" (a
# relative script name), so identify it by that uv.exe, not by a bare filename
# that some other program on the PC could share.
$uvPath = Join-Path $InstallRoot 'uv.exe'
$all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$uvProcs = $all | Where-Object {
    $_.ExecutablePath -and ($_.ExecutablePath -ieq $uvPath) -and
    $_.CommandLine -and ($_.CommandLine -match 'launcher\.py')
}
foreach ($u in $uvProcs) {
    $all | Where-Object { $_.ParentProcessId -eq $u.ProcessId } | ForEach-Object {
        Write-Host "  Stopping launcher process $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $u.ProcessId -Force -ErrorAction SilentlyContinue
}

# Stop the running app so it is not holding the old release open.
Get-Process -Name 'python' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($versions, 'OrdinalIgnoreCase') } |
    ForEach-Object {
        Write-Host "  Stopping release process $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

try {
    if ($blockCurrent) {
        # A bare version: a person chose to leave it, so it stays blocked until a
        # newer release ships. (The launcher adds an expiry to its own entries.)
        # The name must match synergyscan.paths.BLOCKED_VERSIONS.
        try {
            $dataDir = Join-Path $InstallRoot 'data'
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
            Add-Content -Path (Join-Path $dataDir 'blocked_versions.txt') -Value $current -Encoding utf8
        } catch {
            Write-Host "  Could not record $current as blocked ($_). It may update back to it." -ForegroundColor Yellow
        }
    }

    try {
        $tmp = "$pointer.tmp"
        Set-Content -Path $tmp -Value $targetName -Encoding ascii
        Move-Item -Path $tmp -Destination $pointer -Force
        Write-Host ''
        Write-Host "OK Now running $targetName" -ForegroundColor Green
        Write-Host '   Pre-update database backups, if you need one: data\backups\'
        Write-Host ''
    } catch {
        Write-Host "`nX  Could not switch to ${targetName}: $_" -ForegroundColor Red
        Write-Host '   Nothing was changed; SynergyScan starts again on the release it was on.'
    }
} finally {
    # Always start it again, whatever happened above: the app was stopped.
    if (-not $NoStart) {
        $vbs = Join-Path $InstallRoot 'SynergyScan.vbs'
        if (Test-Path $vbs) {
            Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`"" | Out-Null
            Write-Host '   Restarting SynergyScan.'
        }
    }
}
Read-Host 'Press Enter to close'
