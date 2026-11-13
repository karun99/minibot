# Install minibot into an isolated virtual environment on Windows.
#
# Usage:
#   .\scripts\install.ps1
#   $env:MINIBOT_PREFIX = "C:\Tools\minibot"; .\scripts\install.ps1
#
# The virtual environment is created under the prefix and the `minibot`
# launcher is copied into <prefix>\bin so it can be added to PATH.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir

$Prefix = if ($env:MINIBOT_PREFIX) {
    $env:MINIBOT_PREFIX
} else {
    Join-Path $env:LOCALAPPDATA "minibot"
}
$Venv = Join-Path $Prefix "venv"
$BinDir = Join-Path $Prefix "bin"

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    throw "python was not found on PATH. Install Python 3.10+ from python.org and try again."
}

$isOld = & python -c "import sys; print(1 if sys.version_info < (3, 10) else 0)"
if ($isOld.Trim() -eq "1") {
    throw "minibot needs Python 3.10 or newer (found $(& python -V 2>&1))."
}

Write-Host "==> creating virtual environment at $Venv"
& python -m venv $Venv

Write-Host "==> installing minibot from $RepoDir"
& (Join-Path $Venv "Scripts\python.exe") -m pip install --quiet --upgrade pip
& (Join-Path $Venv "Scripts\python.exe") -m pip install --quiet $RepoDir

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item (Join-Path $Venv "Scripts\minibot.exe") (Join-Path $BinDir "minibot.exe") -Force

Write-Host ""
Write-Host "Installed: $(Join-Path $BinDir 'minibot.exe')"
Write-Host "Add it to your PATH if needed:"
Write-Host "  `$env:Path = `"$BinDir;`$env:Path`""
Write-Host ""
Write-Host "Next: start Ollama, then run:"
Write-Host "  minibot --help"
