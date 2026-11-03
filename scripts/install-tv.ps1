#Requires -Version 5.1
<#
.SYNOPSIS
    Install dist/BlinkLive.wgt on a developer-mode Samsung TV with sdb.

.DESCRIPTION
    Apps2Samsung's Custom WGT flow is the recommended path because it signs the
    package with the certificate already covering your TV.  This script is the
    direct Tizen/SDB alternative.

.EXAMPLE
    .\scripts\install-tv.ps1 -Tv 192.0.2.10
#>
param(
    [string]$Wgt = "dist\BlinkLive.wgt",
    [string]$Tv = "192.0.2.10",
    [string]$Sdb = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$wgtPath = if ([System.IO.Path]::IsPathRooted($Wgt)) { $Wgt } else { Join-Path $root $Wgt }
if (-not (Test-Path $wgtPath)) { throw "WGT not found: $wgtPath" }

function Find-Sdb {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path $Explicit)) { return (Resolve-Path $Explicit).Path }
    $cmd = Get-Command sdb.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "C:\tizen-studio\tools\sdb.exe",
        (Join-Path $env:USERPROFILE "tizen-studio\tools\sdb.exe"),
        (Join-Path $env:LOCALAPPDATA "Tizen\tizen-studio\tools\sdb.exe")
    )
    foreach ($candidate in $candidates) { if (Test-Path $candidate) { return $candidate } }
    return $null
}

$sdbPath = Find-Sdb $Sdb
if (-not $sdbPath) {
    throw "sdb.exe was not found. Install Tizen Studio, or use Apps2Samsung -> Custom WGT."
}

Write-Host "Connecting to $Tv ..."
& $sdbPath connect $Tv
& $sdbPath devices
Write-Host "Installing $wgtPath ..."
& $sdbPath install $wgtPath
if ($LASTEXITCODE -ne 0) { throw "sdb install failed (exit $LASTEXITCODE)." }
Write-Host "Installed. Open Blink Live from the TV's app list." -ForegroundColor Green
