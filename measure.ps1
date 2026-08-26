# The measurement, step by step, with the decision gates enforced.
#
#   .\measure.ps1 status      where things stand and what may run next
#   .\measure.ps1 next        run the next step IF the gates allow it
#   .\measure.ps1 watch       tail the running step
#   .\measure.ps1 stop        stop the running step (the ledger keeps the work)
#
# Why a driver at all, rather than pasting commands:
#
#   * Every step runs DETACHED. These processes were killed repeatedly by
#     session teardown mid-run; Start-Process survives it.
#   * Nothing starts while something else is running. Two measurement
#     processes overlapped once and measured three tasks under 2x CPU load,
#     which can manufacture `budget_exhausted` on a task that would otherwise
#     finish. bench/nextstep.py owns the gates; this script only obeys them.
#   * `next` refuses to chain past a gate. A decision point that lives in
#     someone's memory is the one skipped at 2am.

param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "next", "watch", "stop")]
    [string]$Command = "status",

    # The calibration needs a skill id; ignored by every other step.
    [string]$SkillId = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = @(
    (Join-Path $here ".venv\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".alpha-test-1-venv\Scripts\python.exe")
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { $py = "python" }

$env:PYTHONUTF8 = "1"
$runs = Join-Path $here "runs"
$lock = Join-Path $runs ".measurement.lock"
$log = Join-Path $runs "measure.log"

function Get-RunningStep {
    # A held lock means a live measurement: the OS drops it when the process
    # dies, so a stale file is not mistaken for a running job.
    if (-not (Test-Path $lock)) { return $null }
    $held = $null
    try {
        $fs = [System.IO.File]::Open($lock, 'Open', 'ReadWrite', 'None')
        $fs.Close()          # opened exclusively => nobody holds it
    } catch {
        $held = (Get-Content $lock -ErrorAction SilentlyContinue).Trim()
    }
    return $held
}

function Show-Status {
    # Writes to the host and returns ONLY the gate exit code. A function that
    # also emits to the output stream returns those lines as well, so
    # `$gate -ne 0` was true on every run and every gate read as "stop".
    $running = Get-RunningStep
    if ($running) {
        Write-Host "RUNNING  a measurement holds the lock (PID $running)" -ForegroundColor Yellow
    } else {
        Write-Host "idle     no measurement running" -ForegroundColor DarkGray
    }
    Write-Host ""
    & $py (Join-Path $here "bench\nextstep.py") status | Out-Host
    return [int]$LASTEXITCODE
}

switch ($Command) {

    "status" { exit (Show-Status) }

    "watch" {
        if (-not (Test-Path $log)) { Write-Host "no $log yet"; exit 1 }
        Get-Content $log -Tail 30 -Wait
    }

    "stop" {
        $running = Get-RunningStep
        if (-not $running) { Write-Host "nothing running"; exit 0 }
        Write-Host "stopping PID $running - completed tasks stay in the ledger" -ForegroundColor Yellow
        Stop-Process -Id ([int]$running) -Force -ErrorAction SilentlyContinue
        exit 0
    }

    "next" {
        $running = Get-RunningStep
        if ($running) {
            Write-Host "REFUSING: a measurement is already running (PID $running)." -ForegroundColor Red
            Write-Host "Two at once measure their tasks under CPU contention."
            exit 2
        }

        $gate = [int](Show-Status)
        if ($gate -ne 0) {
            Write-Host ""
            Write-Host "A GATE SAYS STOP. Not starting anything." -ForegroundColor Red
            Write-Host "This is a decision for you, not for the script."
            exit 1
        }

        # nextstep prints the exact command; run that rather than re-deriving
        # it here, so there is one source of truth for what comes next.
        $cmd = (& $py (Join-Path $here "bench\nextstep.py") status --json |
                ConvertFrom-Json).next_command
        if (-not $cmd) { Write-Host "nothing to run"; exit 1 }

        if ($cmd -match "<skill_id>") {
            if (-not $SkillId) {
                Write-Host "This step needs -SkillId. List candidates with:" -ForegroundColor Yellow
                Write-Host "  .\alpha.ps1 --config config.local.yaml skills"
                Write-Host "  .\measure.ps1 next -SkillId <id>"
                exit 2
            }
            $cmd = $cmd -replace "<skill_id>", $SkillId
        }

        $argv = $cmd -split "\s+"
        $rest = $argv[1..($argv.Length - 1)]
        Write-Host ""
        Write-Host "starting (detached): $cmd" -ForegroundColor Cyan
        $p = Start-Process -FilePath $py -ArgumentList $rest `
            -WorkingDirectory $here `
            -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
            -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 3
        if ($p.HasExited) {
            Write-Host "it exited immediately - output:" -ForegroundColor Red
            Get-Content $log -Tail 20
            exit 1
        }
        Write-Host "PID $($p.Id) · log: $log" -ForegroundColor Green
        Write-Host "  .\measure.ps1 watch    to follow it"
        Write-Host "  .\measure.ps1 status   to check the gates"
        exit 0
    }
}
