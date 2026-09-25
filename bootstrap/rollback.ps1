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

function VersionKey { param($n) [int[]]($n -split '[.-]') }

$installed = Get-ChildItem -Path $versions -Directory |
    Where-Object { $_.Name -match '^\d{4}\.\d{1,2}\.\d{1,2}-\d+$' } |
    Where-Object { Test-Path (Join-Path $_.FullName '.venv\Scripts\python.exe') } |
    Sort-Object -Property @{ Expression = { , (VersionKey $_.Name) } } -Descending

if ($installed.Count -lt 1) { Fail 'No usable releases are installed.' }

$current = ''
if (Test-Path $pointer) { $current = (Get-Content $pointer -Raw).Trim() }

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
    $older = $installed | Where-Object { $_.Name -ne $current }
    if ($older.Count -lt 1) { Fail 'There is no other release to go back to.' }
    $targetName = $older[0].Name
}

Write-Host ''
$answer = Read-Host "  Switch to $targetName? (y/N)"
if ($answer -ne 'y' -and $answer -ne 'Y') {
    Write-Host '  Cancelled. Nothing changed.'
    exit 0
}

# Stop the running app so it is not holding the old release open.
Get-Process -Name 'python' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($versions, 'OrdinalIgnoreCase') } |
    ForEach-Object {
        Write-Host "  Stopping release process $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

$tmp = "$pointer.tmp"
Set-Content -Path $tmp -Value $targetName -Encoding ascii
Move-Item -Path $tmp -Destination $pointer -Force

Write-Host ''
Write-Host "OK Now running $targetName" -ForegroundColor Green
Write-Host '   Pre-update database backups, if you need one: data\backups\'
Write-Host ''

if (-not $NoStart) {
    $vbs = Join-Path $InstallRoot 'SynergyScan.vbs'
    if (Test-Path $vbs) {
        Start-Process -FilePath 'wscript.exe' -ArgumentList "`"$vbs`"" | Out-Null
        Write-Host '   Restarting SynergyScan.'
    }
}
Read-Host 'Press Enter to close'
