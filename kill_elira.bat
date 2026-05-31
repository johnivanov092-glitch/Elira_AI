@echo off
setlocal
title Elira AI - Hard Kill
color 0C

echo.
echo === Elira hard-kill ===
echo This will force-kill ALL Python uvicorn processes and anything on port 8000.
echo.

rem ─── Method 1: kill by port 8000 ────────────────────────────────────
echo [1/4] Killing whatever owns port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo   killing PID %%a
    taskkill /F /T /PID %%a 2>nul
    wmic process where ProcessId=%%a delete 2>nul
)

rem ─── Method 2: kill Elira venv python ───────────────────────────────
echo [2/4] Killing backend\.venv\Scripts\python.exe...
for /f "tokens=*" %%i in ('wmic process where "ExecutablePath like '%%backend\\.venv\\Scripts\\python.exe'" get ProcessId /value 2^>nul ^| findstr "="') do (
    set "%%i"
)
if defined ProcessId (
    echo   killing PID %ProcessId%
    taskkill /F /T /PID %ProcessId% 2>nul
)

rem ─── Method 3: kill ALL python.exe running uvicorn app.main ─────────
echo [3/4] Killing any python running uvicorn app.main...
for /f "tokens=2 delims==," %%a in ('wmic process where "CommandLine like '%%uvicorn app.main%%'" get ProcessId /format:csv 2^>nul ^| findstr /R "[0-9]"') do (
    echo   killing PID %%a
    taskkill /F /T /PID %%a 2>nul
)

rem ─── Method 4: nuke any Elira Backend cmd window ────────────────────
echo [4/4] Closing 'Elira Backend' cmd windows...
taskkill /F /FI "WINDOWTITLE eq Elira Backend*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Elira AI*" 2>nul

timeout /t 1 /nobreak >nul

rem ─── Verify ─────────────────────────────────────────────────────────
echo.
echo === Status ===
netstat -ano | findstr ":8000" >nul
if errorlevel 1 (
    echo [OK] Port 8000 is free.
) else (
    echo [WARN] Something STILL on port 8000:
    netstat -ano | findstr ":8000"
    echo.
    echo [HINT] Try running this script as Administrator, or reboot.
)

echo.
echo Done. You can now run Elira.bat fresh.
echo.
pause
endlocal
