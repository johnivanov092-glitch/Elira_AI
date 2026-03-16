@echo off
setlocal

cd /d "%~dp0..\backend"

if not exist ".venv" (
  py -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

endlocal
pause
