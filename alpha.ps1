# Launcher for PowerShell. See alpha.cmd for why the package is not installed
# and why the venv lives outside OneDrive.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$here\src;$env:PYTHONPATH"
$env:PYTHONUTF8 = "1"

$candidates = @(
    (Join-Path $here ".venv\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".alpha-test-1-venv\Scripts\python.exe")
)
$py = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { $py = "python" }

& $py -m agent.cli --config (Join-Path $here "config.yaml") @args
