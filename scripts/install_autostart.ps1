# install_autostart.ps1 — регистрирует автозапуск супервизора при входе в систему.
#
# Запуск (один раз):
#   powershell -NoProfile -ExecutionPolicy Bypass -File "E:\Claude code\mybot\scripts\install_autostart.ps1"
#
# Создаёт задачу планировщика «MaxcellonBotSupervisor», которая при логоне
# текущего пользователя запускает bot_supervisor.ps1 (скрыто). Супервизор сам
# держит бота живым. Перезапуск задачи каждую минуту, если упадёт сам супервизор.
#
# Удалить автозапуск:
#   Unregister-ScheduledTask -TaskName "MaxcellonBotSupervisor" -Confirm:$false

$ErrorActionPreference = "Stop"

$Root     = "E:\Claude code\mybot"
$Sup      = Join-Path $Root "scripts\bot_supervisor.ps1"
$TaskName = "MaxcellonBotSupervisor"

$psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Sup`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Maxcellon Telegram bot supervisor (auto-restart)" -Force | Out-Null

Write-Host "OK: задача '$TaskName' зарегистрирована (запуск при входе в систему)."
Write-Host "Запустить прямо сейчас: Start-ScheduledTask -TaskName '$TaskName'"
