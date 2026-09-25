<#
.SYNOPSIS
    Removes SynergyScan from this PC.

.DESCRIPTION
    Removes the program, the shortcuts and the installed releases.

    It does NOT delete your data unless you pass -DeleteData. The database is
    the one thing here that cannot be downloaded again, so removing it is
    always an explicit choice, never a side effect of uninstalling.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = 'C:\SynergyScan',
    [switch]$DeleteData,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Fail { param($m) Write-Host "`nX  $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $InstallRoot)) { Fail "Nothing installed at $InstallRoot" }

$dataDir = Join-Path $InstallRoot 'data'
$hasData = Test-Path $dataDir

Write-Host ''
Write-Host '  SynergyScan uninstaller' -ForegroundColor White
Write-Host '  -----------------------'
Write-Host "  Folder: $InstallRoot"
if ($hasData) {
    if ($DeleteData) {
        Write-Host '  DATA WILL BE DELETED - the inventory database and all history.' -ForegroundColor Red
    } else {
        Write-Host "  Your data will be KEPT in $dataDir" -ForegroundColor Yellow
        Write-Host '  (add -DeleteData to remove it as well)'
    }
}

if (-not $Force) {
    Write-Host ''
    $answer = Read-Host '  Continue? (y/N)'
    if ($answer -ne 'y' -and $answer -ne 'Y') { Write-Host '  Cancelled.'; exit 0 }
}

# Stop anything running out of the install folder.
Get-Process -Name 'python', 'uv' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($InstallRoot, 'OrdinalIgnoreCase') } |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

foreach ($folder in @([Environment]::GetFolderPath('Desktop'),
                      [Environment]::GetFolderPath('Startup'))) {
    $lnk = Join-Path $folder 'SynergyScan.lnk'
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "  removed shortcut: $lnk" }
}

if ($DeleteData) {
    Remove-Item -Recurse -Force $InstallRoot
    Write-Host ''
    Write-Host 'OK Removed, including all data.' -ForegroundColor Green
} else {
    foreach ($name in 'versions', 'staging', 'uv.exe', 'launcher.py', 'current.txt',
                      'SynergyScan.vbs', 'SynergyScan-console.bat', 'rollback.bat',
                      'rollback.ps1', 'install.ps1', 'install.bat', 'uninstall.ps1') {
        $p = Join-Path $InstallRoot $name
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
    Write-Host ''
    Write-Host 'OK Removed.' -ForegroundColor Green
    Write-Host "   Your data is still at $dataDir"
    Write-Host '   Reinstalling into the same folder will pick it up again.'
}
