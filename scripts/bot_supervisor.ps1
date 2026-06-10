# bot_supervisor.ps1 — следит за ботом и перезапускает при падении.
#
# Запуск вручную (в фоне):
#   powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "E:\Claude code\mybot\scripts\bot_supervisor.ps1"
# Или через автозапуск: scripts\install_autostart.ps1 (Task Scheduler при входе).
#
# Что делает:
#   • запускает .venv\Scripts\python.exe main.py (скрытое окно)
#   • пишет pid бота в data\bot.pid
#   • ждёт завершения процесса; при выходе логирует код+аптайм в logs\watchdog.log
#   • перезапускает с backoff (5..60с), быстрые падения → больше пауза
#   • одиночный запуск: повторный супервизор не стартует (lock data\supervisor.pid)

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

# ── Одиночный запуск супервизора ────────────────────────────────────────────
if (Test-Path $SupLock) {
    $oldSup = Get-Content $SupLock -ErrorAction SilentlyContinue
    if ($oldSup) {
        $alive = Get-Process -Id $oldSup -ErrorAction SilentlyContinue
        if ($alive -and $alive.ProcessName -like "powershell*") {
            Write-WLog "Supervisor already running (pid=$oldSup) — exit"
            return
        }
    }
}
Set-Content -Path $SupLock -Value $PID -Encoding ascii
Write-WLog "Supervisor started (pid=$PID)"

# ── Остановить уже запущенный бот (чтобы не было двух сессий) ────────────────
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
    Write-WLog ("Bot exited pid={0} code={1} uptime={2}s" -f $p.Id, $code, $uptime)

    if ($uptime -lt 30) { $failFast++ } else { $failFast = 0 }
    $backoff = [Math]::Min(60, 5 * [Math]::Max(1, $failFast))
    Write-WLog "Restarting in ${backoff}s (failFast=$failFast)"
    Start-Sleep -Seconds $backoff
}
