@echo off
REM в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ
REM Elira Build вЂ” СЃРѕР±РёСЂР°РµС‚ .exe СѓСЃС‚Р°РЅРѕРІС‰РёРє
REM Р—Р°РїСѓСЃРєР°Р№ РёР· РєРѕСЂРЅСЏ РїСЂРѕРµРєС‚Р°
REM в•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђв•ђ

echo.
echo   Elira Build - СЃР±РѕСЂРєР° СѓСЃС‚Р°РЅРѕРІС‰РёРєР°
echo   ==================================
echo.
echo   РўСЂРµР±РѕРІР°РЅРёСЏ:
echo     - Node.js 18+
echo     - Rust (rustup.rs)
echo     - npm install РІС‹РїРѕР»РЅРµРЅ
echo.

cd /d "%~dp0"

echo [1/3] РЈСЃС‚Р°РЅРѕРІРєР° Р·Р°РІРёСЃРёРјРѕСЃС‚РµР№...
call npm install
if errorlevel 1 ( echo [ERROR] npm install failed! & pause & exit /b 1 )
cd frontend && call npm install && cd ..
if errorlevel 1 ( echo [ERROR] frontend npm install failed! & pause & exit /b 1 )

echo [2/3] РЎР±РѕСЂРєР° Tauri (СЌС‚Рѕ Р·Р°Р№РјС‘С‚ 2-5 РјРёРЅСѓС‚)...
call npm run tauri build
if errorlevel 1 ( echo [ERROR] Tauri build failed! & pause & exit /b 1 )

echo.
echo [3/3] Р“РѕС‚РѕРІРѕ!
echo.
echo   РЈСЃС‚Р°РЅРѕРІС‰РёРє: src-tauri\target\release\bundle\nsis\
echo   РџРѕСЂС‚Р°С‚РёРІРЅС‹Р№: src-tauri\target\release\
echo.
echo   Р’РђР–РќРћ: .exe СЃРѕРґРµСЂР¶РёС‚ С‚РѕР»СЊРєРѕ С„СЂРѕРЅС‚РµРЅРґ.
echo   Р‘РµРєРµРЅРґ (Python + Ollama) РЅСѓР¶РЅРѕ Р·Р°РїСѓСЃРєР°С‚СЊ РѕС‚РґРµР»СЊРЅРѕ:
echo     cd backend
echo     .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
pause

