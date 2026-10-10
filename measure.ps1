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
    (Join-Path $env:USERPROFILE ".bucephalus-venv\Scripts\python.exe")
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $py) { $py = "python" }

$env:PYTHONUTF8 = "1"
$runs = Join-Path $here "runs"
$lock = Join-Path $runs ".measurement.lock"
$log = Join-Path $runs "measure.log"

function Get-RunningStep {
    # A held lock means a live measurement: the OS drops it when the process
    # dies, so a stale file is never mistaken for a running job.
    #
    # The exclusive Open() failing IS the answer. Reading the PID out of the
    # file can fail too - Windows refuses to read a byte range another
    # process holds - and an earlier version treated that failed read as
    # "nobody is running", which is the most dangerous way to be wrong here.
    if (-not (Test-Path $lock)) { return $null }
    try {
        $fs = [System.IO.File]::Open($lock, 'Open', 'ReadWrite', 'None')
        $fs.Close()
        return $null              # opened exclusively => nobody holds it
    } catch {
        $pid_ = "unknown"
        try { $pid_ = (Get-Content $lock -ErrorAction Stop).Trim() } catch { }
        if (-not $pid_) { $pid_ = "unknown" }
        return $pid_              # always truthy: something holds the lock
    }
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
        if ($running -eq "unknown") {
            Write-Host "A run holds the lock but its PID is unreadable." -ForegroundColor Yellow
            Write-Host "Find it with: Get-CimInstance Win32_Process -Filter \"Name like '%python%'\""
            exit 1
        }
        Write-Host "stopping PID $running - completed tasks stay in the ledger" -ForegroundColor Yellow
        # D10. Stop-Process kills THAT process. A venv python.exe is a
        # trampoline (C13), so it left the real interpreter running and the
        # ledger still growing while the run was believed stopped - four
        # occurrences. kill_tree is the one implementation that handles it.
        & $py -c "import sys; from agent.proc import kill_tree; sys.exit(0 if kill_tree(int(sys.argv[1])) else 1)" $running
        exit $LASTEXITCODE
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
                Write-Host "  .\bucephalus.ps1 --config config.local.yaml skills"
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
