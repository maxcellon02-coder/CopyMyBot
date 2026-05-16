@echo off
cd /d "E:\Claude code\mybot"

echo Starting main bot...
start "Maxcellon Bot" cmd /k "set PYTHONUTF8=1 && .venv\Scripts\python.exe main.py"

timeout /t 3 /nobreak >nul

echo Starting leads notifier...
start "Leads Notifier" cmd /k "set PYTHONUTF8=1 && .venv\Scripts\python.exe scripts\check_leads.py"

echo Both started. Close this window.
timeout /t 2 /nobreak >nul
