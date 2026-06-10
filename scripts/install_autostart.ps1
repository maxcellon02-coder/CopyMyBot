# install_autostart.ps1 -- auto-start the supervisor at user logon (no admin).
#
# Run once:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "E:\Claude code\mybot\scripts\install_autostart.ps1"
#
# Adds an HKCU\...\Run entry "MaxcellonBotSupervisor" that launches
# bot_supervisor.ps1 (hidden) at logon. The supervisor keeps the bot alive.
# This needs NO administrator rights (unlike a Scheduled Task).
#
# Remove autostart:
#   Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MaxcellonBotSupervisor"
#
# ASCII-only (parsed by Windows PowerShell 5.1).

$ErrorActionPreference = "Stop"

$Root    = "E:\Claude code\mybot"
$Sup     = Join-Path $Root "scripts\bot_supervisor.ps1"
$RunKey  = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$Name    = "MaxcellonBotSupervisor"

$cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Sup`""

Set-ItemProperty -Path $RunKey -Name $Name -Value $cmd

Write-Host "OK: autostart registered in HKCU Run -> '$Name'"
Write-Host "Value: $cmd"
Write-Host "It will start automatically at next logon. Supervisor is already running now."
