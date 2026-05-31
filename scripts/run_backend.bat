@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "BACKEND_DIR=%REPO_ROOT%\backend"
set "BACKEND_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "ELIRA_DATA_DIR=%REPO_ROOT%\data"
set "BACKEND_ENV=%BACKEND_DIR%\.env"
set "BACKEND_ENV_LOCAL=%BACKEND_DIR%\.env.local"

cd /d "%BACKEND_DIR%"

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

if not exist ".venv" (
  py -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\backend_preflight.ps1" -RepoRoot "%REPO_ROOT%" -BackendPython "%BACKEND_PY%" -Port 8000 -AutoStopEliraBackend
set "PREFLIGHT_EXIT=%ERRORLEVEL%"

if "%PREFLIGHT_EXIT%"=="20" (
  echo [ERROR] Port 8000 already belongs to another process.
  echo [HINT] Stop the conflicting backend and retry.
  endlocal
  pause
  exit /b 1
)

if "%PREFLIGHT_EXIT%"=="21" (
  echo [ERROR] Failed to stop the previous Elira backend automatically.
  echo [HINT] Close it manually and retry.
  endlocal
  pause
  exit /b 1
)

if "%PREFLIGHT_EXIT%"=="10" (
  echo [INFO] Reusing existing backend on 127.0.0.1:8000.
  exit /b 0
)

set ELIRA_DATA_DIR=%ELIRA_DATA_DIR%
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

endlocal
pause
