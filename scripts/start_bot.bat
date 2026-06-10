@echo off
REM start_bot.bat — запустить бота под супервизором (скрыто, с авто-перезапуском).
REM Двойной клик или: scripts\start_bot.bat
start "" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "E:\Claude code\mybot\scripts\bot_supervisor.ps1"
echo Supervisor launched. Logs: logs\watchdog.log
