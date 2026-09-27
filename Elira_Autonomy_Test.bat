@echo off
cd /d "%~dp0"
"%~dp0backend\.venv\Scripts\python.exe" "%~dp0scripts\run_autonomy_unattended.py" %*
if errorlevel 1 pause
