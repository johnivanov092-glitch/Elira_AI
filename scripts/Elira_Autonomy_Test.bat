@echo off
cd /d "%~dp0.."
"backend\.venv\Scripts\python.exe" "scripts\run_autonomy_unattended.py" %*
if errorlevel 1 pause
