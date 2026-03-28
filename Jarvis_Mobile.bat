@echo off
title Jarvis Mobile Access
color 0A
echo.
echo   ╔══════════════════════════════════════════╗
echo   ║   JARVIS - Mobile / LAN Access Mode      ║
echo   ╚══════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: Получаем IP адрес
echo [INFO] Определяю IP адрес...
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R "IPv4"') do (
    set IP=%%a
)
set IP=%IP: =%

echo.
echo   Твой IP: %IP%
echo.
echo   Для доступа с телефона:
echo     1. Телефон и ПК должны быть в одной Wi-Fi сети
echo     2. Открой на телефоне: http://%IP%:5173
echo.
echo   Запускаю бекенд на 0.0.0.0 (доступен из сети)...
echo.

:: Запуск бекенда на 0.0.0.0
start /min "Jarvis Backend (LAN)" cmd /c "cd backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 3 /nobreak > nul

:: Запуск фронтенда на 0.0.0.0 с правильным API URL
echo [INFO] Запускаю фронтенд...
start "" cmd /c "set VITE_API_BASE_URL=http://%IP%:8000 && set VITE_HOST=0.0.0.0 && npm --prefix frontend run dev"

echo.
echo   ✓ Jarvis доступен по сети: http://%IP%:5173
echo   ✓ API доступен: http://%IP%:8000/docs
echo.
echo   Для остановки закройте это окно.
echo.
pause
