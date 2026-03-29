@echo off
title Elira AI
color 0A
echo.
echo   ╔══════════════════════════════════════╗
echo   ║         ELIRA AI - Starting          ║
echo   ╚══════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: Проверка venv
if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERROR] Python venv не найден: backend\.venv\Scripts\python.exe
    echo   Создай: cd backend ^&^& python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

:: Запуск бекенда
echo [1/2] Starting backend...
start /min "Elira Backend" cmd /c "cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

:: Ждём 3 секунды пока бекенд запустится
timeout /t 3 /nobreak > nul

:: Запуск Tauri (фронтенд + окно)
echo [2/2] Starting Elira window...
start "" cmd /c "npm run tauri dev"

echo.
echo   Elira запущена! Окно откроется через несколько секунд.
echo   Для остановки закройте окно Elira.
echo.
timeout /t 5 /nobreak > nul
exit
