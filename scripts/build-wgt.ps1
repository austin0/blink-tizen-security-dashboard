#Requires -Version 5.1
<#
.SYNOPSIS
    Build (and sign, when Tizen CLI is available) the Blink Live .wgt.

.DESCRIPTION
    The app folder is always packaged into dist/BlinkLive.wgt.  Signing uses a
    Tizen security profile.  If Tizen CLI is not found, an unsigned .wgt is
    produced for inspection and for Apps2Samsung's custom-WGT re-sign path.

.EXAMPLE
    .\scripts\build-wgt.ps1 -Profile BlinkLive
#>
param(
    [string]$Profile = "BlinkLive",
    [string]$Out = "dist\BlinkLive.wgt",
    [string]$TizenCli = "",
    [switch]$Unsigned
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $root "tizen"
$outPath = if ([System.IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $root $Out }
$outDir = Split-Path -Parent $outPath
New-Item -ItemType Directory -Force $outDir | Out-Null

if (-not (Test-Path (Join-Path $appDir "config.xml"))) {
    throw "Tizen app folder not found: $appDir"
}

function Find-TizenCli {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path $Explicit)) { return (Resolve-Path $Explicit).Path }
    $cmd = Get-Command tizen.bat -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command tizen -ErrorAction SilentlyContinue }
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "C:\tizen-studio\tools\ide\bin\tizen.bat",
        (Join-Path $env:USERPROFILE "tizen-studio\tools\ide\bin\tizen.bat"),
        (Join-Path $env:LOCALAPPDATA "Tizen\tizen-studio\tools\ide\bin\tizen.bat")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function New-UnsignedWgt {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $outPath) { Remove-Item $outPath -Force }
    $zip = [System.IO.Compression.ZipFile]::Open($outPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        $files = Get-ChildItem $appDir -Recurse -File
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($appDir.Length + 1).Replace('\', '/')
            $entry = $zip.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
            $stream = $entry.Open()
            try {
                $input = [System.IO.File]::OpenRead($file.FullName)
                try { $input.CopyTo($stream) } finally { $input.Dispose() }
            } finally { $stream.Dispose() }
        }
    } finally { $zip.Dispose() }
}

$tizen = Find-TizenCli $TizenCli
if ($Unsigned -or -not $tizen) {
    if (-not $tizen) {
        Write-Warning "Tizen CLI was not found. Building an UNSIGNED .wgt."
        Write-Warning "Apps2Samsung can re-sign a custom WGT during install, or install Tizen Studio and pass -TizenCli."
    }
    New-UnsignedWgt
    Write-Host "Unsigned package: $outPath" -ForegroundColor Yellow
    exit 0
}

$before = @(Get-ChildItem $outDir -Filter "*.wgt" -File | Select-Object -ExpandProperty FullName)
Push-Location $appDir
try {
    & $tizen package -t wgt -s $Profile -o $outDir -- .
    if ($LASTEXITCODE -ne 0) { throw "Tizen packaging failed (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}

$after = @(Get-ChildItem $outDir -Filter "*.wgt" -File | Select-Object -ExpandProperty FullName)
$new = $after | Where-Object { $before -notcontains $_ } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $new) {
    $new = $after | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $new) { throw "Tizen did not produce a .wgt." }
if ($new -ne $outPath) {
    if (Test-Path $outPath) { Remove-Item $outPath -Force }
    Move-Item $new $outPath -Force
}
$item = Get-Item $outPath
$hash = (Get-FileHash $outPath -Algorithm SHA256).Hash
Write-Host "Signed package: $($item.FullName)" -ForegroundColor Green
Write-Host "SHA-256: $hash"
