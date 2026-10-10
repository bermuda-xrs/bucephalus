# One-command setup. Creates the virtualenv and installs dependencies.
#
#   .\setup.ps1              # venv outside the sync folder (recommended)
#   .\setup.ps1 -InPlace     # venv at .\.venv instead
#
# Why outside by default: this project lives under OneDrive, and a venv is
# thousands of small files — exactly what an unstable sync loses. It was
# deleted twice during development while the multi-GB model blobs beside it
# survived untouched. A venv is regenerable scaffolding, so it is the thing to
# move rather than the source.

param([switch]$InPlace)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = if ($InPlace) { Join-Path $here ".venv" } else { Join-Path $env:USERPROFILE ".bucephalus-venv" }

Write-Host "venv -> $venv" -ForegroundColor Cyan

# Python 3.11 or 3.12. 3.13+ had package gaps when this was written.
$py = @(
    "$env:APPDATA\uv\python\cpython-3.11*\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
) | ForEach-Object { Get-Item $_ -ErrorAction SilentlyContinue } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $py) { $py = "python" }
Write-Host "using $py"

if (-not (Test-Path "$venv\Scripts\python.exe")) {
    & $py -m venv $venv
}
& "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$venv\Scripts\python.exe" -m pip install --quiet `
    pydantic httpx anthropic pyyaml fastapi uvicorn scipy rich textual platformdirs pytest pytest-asyncio pytest-xdist ruff `
    "sympy==1.13.3"

Write-Host "`nverifying..." -ForegroundColor Cyan
& "$venv\Scripts\python.exe" -c "import pydantic, scipy, rich, fastapi, yaml, httpx, pytest; print('dependencies ok')"

$env:PYTHONUTF8 = "1"
Push-Location $here
& "$venv\Scripts\python.exe" -m pytest tests -q --tb=line
Pop-Location

Write-Host @"

Next:
  1. .\ollama-serve.ps1                     (leave open; serves the models)
  2. .\bucephalus.ps1 --config config.local.yaml serve    (console on :8901)
  3. Read docs/HANDOVER.md
"@ -ForegroundColor Green
