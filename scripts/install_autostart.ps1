# install_autostart.ps1 -- register the supervisor to auto-start at user logon.
#
# Run once:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "E:\Claude code\mybot\scripts\install_autostart.ps1"
#
# Creates Scheduled Task "MaxcellonBotSupervisor": at logon it launches
# bot_supervisor.ps1 (hidden), which keeps the bot alive. The task itself is
# restarted every minute if the supervisor process ever dies.
#
# Remove autostart:
#   Unregister-ScheduledTask -TaskName "MaxcellonBotSupervisor" -Confirm:$false
#
# ASCII-only (parsed by Windows PowerShell 5.1).

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

Write-Host "OK: task '$TaskName' registered (starts at logon)."
Write-Host "Start now: Start-ScheduledTask -TaskName '$TaskName'"
