@echo off
setlocal

cd /d "%~dp0"
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"
set "ELIRA_DATA_DIR=%REPO_ROOT%\data"
set "BACKEND_PY=%REPO_ROOT%\backend\.venv\Scripts\python.exe"
set "BACKEND_ENV=%REPO_ROOT%\backend\.env"
set "BACKEND_ENV_LOCAL=%REPO_ROOT%\backend\.env.local"

echo.
echo [INFO] Startup order: backend ^> Tauri dev
echo [INFO] Runtime data dir: %ELIRA_DATA_DIR%
echo [INFO] Core deps: backend\requirements.txt and npm install
echo [INFO] Optional deps: backend\requirements-optional.txt
echo [INFO] Missing optional packages only disable vector memory and screenshot.
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

if "%PREFLIGHT_EXIT%"=="11" (
    echo [1/3] Restarting Elira backend on 127.0.0.1:8000...
    start /min "Elira Backend" cmd /c "set ELIRA_DATA_DIR=%ELIRA_DATA_DIR%&& cd /d \"%REPO_ROOT%\backend\" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
    timeout /t 3 /nobreak > nul
) else (
    if "%PREFLIGHT_EXIT%"=="0" (
    echo [1/3] Starting backend on 127.0.0.1:8000...
    start /min "Elira Backend" cmd /c "set ELIRA_DATA_DIR=%ELIRA_DATA_DIR%&& cd /d \"%REPO_ROOT%\backend\" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
    timeout /t 3 /nobreak > nul
) else (
        echo [1/3] Reusing existing backend on 127.0.0.1:8000...
    )
)

echo [2/3] Launching Tauri dev...
call npm.cmd run tauri dev

echo.
echo [INFO] If Dashboard reports missing optional packages, install backend\requirements-optional.txt
pause

endlocal
