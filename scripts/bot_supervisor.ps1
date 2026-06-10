# bot_supervisor.ps1 -- keeps the bot alive: restarts it whenever it exits.
#
# Run manually (background):
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "E:\Claude code\mybot\scripts\bot_supervisor.ps1"
# Or via autostart: scripts\install_autostart.ps1 (Task Scheduler at logon).
#
# Behaviour:
#   - launches .venv\Scripts\python.exe main.py (hidden window)
#   - writes bot pid to data\bot.pid
#   - waits for the process; on exit logs code+uptime to logs\watchdog.log
#   - restarts with backoff (5..60s); rapid crashes -> longer pause
#   - single instance: a second supervisor will not start (lock data\supervisor.pid)
#
# ASCII-only on purpose: this file is parsed by Windows PowerShell 5.1, which
# misreads UTF-8-without-BOM and would break on non-ASCII characters.

$ErrorActionPreference = "Stop"

$Root    = "E:\Claude code\mybot"
$Py      = Join-Path $Root ".venv\Scripts\python.exe"
$PidFile = Join-Path $Root "data\bot.pid"
$SupLock = Join-Path $Root "data\supervisor.pid"
$WLog    = Join-Path $Root "logs\watchdog.log"
$OutLog  = Join-Path $Root "logs\bot_console.out"
$ErrLog  = Join-Path $Root "logs\bot_console.err"

function Write-WLog([string]$msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $WLog -Value "$ts  $msg" -Encoding utf8
}

# -- Single supervisor instance --------------------------------------------
if (Test-Path $SupLock) {
    $oldSup = Get-Content $SupLock -ErrorAction SilentlyContinue
    if ($oldSup) {
        $alive = Get-Process -Id $oldSup -ErrorAction SilentlyContinue
        if ($alive -and $alive.ProcessName -like "powershell*") {
            Write-WLog "Supervisor already running (pid=$oldSup) - exit"
            return
        }
    }
}
Set-Content -Path $SupLock -Value $PID -Encoding ascii
Write-WLog "Supervisor started (pid=$PID)"

# -- Stop an already running bot (avoid two sessions) ----------------------
if (Test-Path $PidFile) {
    $oldBot = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($oldBot) {
        $bp = Get-Process -Id $oldBot -ErrorAction SilentlyContinue
        if ($bp) {
            Write-WLog "Stopping pre-existing bot pid=$oldBot"
            Stop-Process -Id $oldBot -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
        }
    }
}

$failFast = 0
while ($true) {
    $start = Get-Date
    try {
        $p = Start-Process -FilePath $Py -ArgumentList "main.py" -WorkingDirectory $Root `
             -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog `
             -WindowStyle Hidden -PassThru
    } catch {
        Write-WLog "Failed to start bot: $_"
        Start-Sleep -Seconds 15
        continue
    }

    Set-Content -Path $PidFile -Value $p.Id -Encoding ascii
    Write-WLog "Bot started pid=$($p.Id)"

    Wait-Process -Id $p.Id
    $code   = $p.ExitCode
    $uptime = [int]((New-TimeSpan -Start $start -End (Get-Date)).TotalSeconds)
    Write-WLog "Bot exited pid=$($p.Id) code=$code uptime=$uptime sec"

    if ($uptime -lt 30) { $failFast++ } else { $failFast = 0 }
    $backoff = [Math]::Min(60, 5 * [Math]::Max(1, $failFast))
    Write-WLog "Restarting in $backoff sec (failFast=$failFast)"
    Start-Sleep -Seconds $backoff
}
