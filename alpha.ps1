# Launcher for PowerShell. See alpha.cmd for why the package is not installed.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$here\src;$env:PYTHONPATH"
$env:PYTHONUTF8 = "1"
$py = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m agent.cli --config (Join-Path $here "config.yaml") @args
