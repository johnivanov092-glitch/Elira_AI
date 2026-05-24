@echo off
setlocal
title Elira AI
color 0A

cd /d "%~dp0"
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"
set "ELIRA_DATA_DIR=%REPO_ROOT%\data"
set "BACKEND_PY=%REPO_ROOT%\backend\.venv\Scripts\python.exe"
set "BACKEND_ENV=%REPO_ROOT%\backend\.env"
set "BACKEND_ENV_LOCAL=%REPO_ROOT%\backend\.env.local"

echo.
echo [INFO] Startup order: backend ^> Tauri
echo [INFO] Runtime data dir: %ELIRA_DATA_DIR%
echo [INFO] Core deps: backend\requirements.txt and npm install
echo [INFO] Optional deps: backend\requirements-optional.txt
echo [INFO] Missing optional packages only disable vector memory and screenshot.
echo [INFO] Check Dashboard for Project Brain and Runtime warnings.
echo.

if exist "%BACKEND_ENV%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%BACKEND_ENV%") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
)
if exist "%BACKEND_ENV_LOCAL%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%BACKEND_ENV_LOCAL%") do (
        if not "%%~A"=="" set "%%~A=%%~B"
    )
)

if not exist "%BACKEND_PY%" (
    echo [ERROR] Missing backend virtualenv: backend\.venv\Scripts\python.exe
    echo [HINT] Run:
    echo        cd backend
    echo        python -m venv .venv
    echo        .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "node_modules" (
    echo [0/3] Installing root npm dependencies...
    call npm.cmd install
    if errorlevel 1 (
        echo [ERROR] npm install failed
        pause
        exit /b 1
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\backend_preflight.ps1" -RepoRoot "%REPO_ROOT%" -BackendPython "%BACKEND_PY%" -Port 8000 -AutoStopEliraBackend
set "PREFLIGHT_EXIT=%ERRORLEVEL%"

if "%PREFLIGHT_EXIT%"=="20" (
    echo.
    echo [ERROR] Port 8000 already belongs to another process.
    echo [HINT] Close the conflicting backend and retry.
    pause
    exit /b 1
)

if "%PREFLIGHT_EXIT%"=="21" (
    echo.
    echo [ERROR] Failed to stop the previous Elira backend automatically.
    echo [HINT] Close it manually and retry.
    pause
    exit /b 1
)

set "BACKEND_FRESH=0"
set "BACKEND_LOG=%REPO_ROOT%\backend\backend.log"

if "%PREFLIGHT_EXIT%"=="11" (
    echo [1/3] Waiting for port 8000 to release after AutoStop...
    rem Windows can hold the socket in TIME_WAIT after kill - poll until truly free.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "for ($i = 0; $i -lt 20; $i++) { $b = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; if (-not $b) { Write-Host ('[OK] Port free after ' + ($i * 250) + ' ms.'); break }; Start-Sleep -Milliseconds 250 }"
    echo [1/3] Restarting Elira backend on 127.0.0.1:8000 ^(stdout -^> backend\backend.log^)...
    if exist "%BACKEND_LOG%" del "%BACKEND_LOG%" >nul 2>&1
    start /min "Elira Backend" cmd /c "set ELIRA_DATA_DIR=%ELIRA_DATA_DIR%&& cd /d %REPO_ROOT%\backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload > %BACKEND_LOG% 2>&1"
    set "BACKEND_FRESH=1"
) else (
    if "%PREFLIGHT_EXIT%"=="0" (
        echo [1/3] Starting backend on 127.0.0.1:8000 ^(stdout -^> backend\backend.log^)...
        if exist "%BACKEND_LOG%" del "%BACKEND_LOG%" >nul 2>&1
        start /min "Elira Backend" cmd /c "set ELIRA_DATA_DIR=%ELIRA_DATA_DIR%&& cd /d %REPO_ROOT%\backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload > %BACKEND_LOG% 2>&1"
        set "BACKEND_FRESH=1"
    ) else (
        echo [1/3] Reusing existing backend on 127.0.0.1:8000...
    )
)

rem Health-check loop: poll /health for up to 30 seconds before launching Tauri.
rem Without this, a crashed backend just produced a silent frontend retry-loop.
echo [1/3] Waiting for backend health on 127.0.0.1:8000/health (up to 30 sec)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok = $false; for ($i = 0; $i -lt 30; $i++) { try { $r = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 1 -ErrorAction Stop; if ($r.service -eq 'elira-ai-api') { $ok = $true; Write-Host ('[OK] Backend up after ' + ($i + 1) + ' sec.'); break } } catch {}; Start-Sleep -Seconds 1 }; if (-not $ok) { exit 30 }"
if errorlevel 30 (
    echo.
    echo [ERROR] Backend is not responding on http://127.0.0.1:8000/health after 30 seconds.
    if "%BACKEND_FRESH%"=="1" (
        echo [HINT] Last 40 lines of backend\backend.log:
        echo ----------------------------------------------------------------------
        if exist "%BACKEND_LOG%" (
            powershell -NoProfile -Command "Get-Content '%BACKEND_LOG%' -Tail 40"
        ) else (
            echo [no log file found - start /min cmd likely failed to launch python]
        )
        echo ----------------------------------------------------------------------
        echo [HINT] Common causes:
        echo          - missing core dependency  : cd backend ^&^& .venv\Scripts\pip install -r requirements.txt
        echo          - corrupted .venv          : delete backend\.venv and recreate
        echo          - port 8000 stuck on dead process : reboot or kill it with `taskkill /PID ^<pid^> /F`
    ) else (
        echo [HINT] The previous Elira backend that preflight tried to reuse is unresponsive.
        echo [HINT] Close any "Elira Backend" cmd window and rerun Elira.bat.
    )
    pause
    exit /b 1
)

echo [2/3] Starting Tauri dev shell...
rem Tell Rust setup handler that backend was started externally (Phase 7 fix).
set "ELIRA_EXTERNAL_BACKEND=1"
call npm.cmd run tauri dev

if errorlevel 1 (
    echo.
    echo [ERROR] Tauri dev failed to start.
    echo [HINT] Try:
    echo        npm install
    echo        npm run tauri dev
    pause
    exit /b 1
)

echo.
echo [INFO] If the dashboard shows missing packages, install backend\requirements-optional.txt
echo [INFO] Backend keeps running in a separate window until you close it.
echo.

endlocal
exit /b 0
