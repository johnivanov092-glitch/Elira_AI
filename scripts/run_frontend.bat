@echo off
cd /d D:\AIWork\jarvis_work\frontend

if not exist node_modules (
  call npm install
)

call npm run dev
pause
