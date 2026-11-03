#Requires -Version 5.1
<#
.SYNOPSIS
    Run the mock bridge for UI work (no Blink account, no TV).
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Create .venv and install backend/requirements.txt first."
}
Push-Location $root
try {
    & $python "tools\mock_bridge.py"
} finally {
    Pop-Location
}
