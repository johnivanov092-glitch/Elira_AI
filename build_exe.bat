@echo off
REM ═══════════════════════════════════════════════════
REM Jarvis Build — собирает .exe установщик
REM Запускай из корня проекта
REM ═══════════════════════════════════════════════════

echo.
echo   Jarvis Build - сборка установщика
echo   ==================================
echo.
echo   Требования:
echo     - Node.js 18+
echo     - Rust (rustup.rs)
echo     - npm install выполнен
echo.

cd /d "%~dp0"

echo [1/3] Установка зависимостей...
call npm install
if errorlevel 1 ( echo [ERROR] npm install failed! & pause & exit /b 1 )
cd frontend && call npm install && cd ..
if errorlevel 1 ( echo [ERROR] frontend npm install failed! & pause & exit /b 1 )

echo [2/3] Сборка Tauri (это займёт 2-5 минут)...
call npm run tauri build
if errorlevel 1 ( echo [ERROR] Tauri build failed! & pause & exit /b 1 )

echo.
echo [3/3] Готово!
echo.
echo   Установщик: src-tauri\target\release\bundle\nsis\Jarvis_0.2.0_x64-setup.exe
echo   Портативный: src-tauri\target\release\Jarvis.exe
echo.
echo   ВАЖНО: .exe содержит только фронтенд.
echo   Бекенд (Python + Ollama) нужно запускать отдельно:
echo     cd backend
echo     .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
pause
