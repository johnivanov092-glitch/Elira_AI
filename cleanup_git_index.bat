@echo off
cd /d D:\AIWork\jarvis_work

echo Removing heavy folders from Git index only...
git rm -r --cached --ignore-unmatch node_modules
git rm -r --cached --ignore-unmatch frontend\node_modules
git rm -r --cached --ignore-unmatch backend\.venv
git rm -r --cached --ignore-unmatch .venv
git rm -r --cached --ignore-unmatch venv
git rm -r --cached --ignore-unmatch src-tauri\target
git rm -r --cached --ignore-unmatch target
git rm -r --cached --ignore-unmatch __pycache__
git rm -r --cached --ignore-unmatch frontend\.vite
git rm -r --cached --ignore-unmatch dist
git rm -r --cached --ignore-unmatch build
git rm -r --cached --ignore-unmatch frontend\dist
git rm -r --cached --ignore-unmatch frontend\build
git rm -r --cached --ignore-unmatch logs
git rm -r --cached --ignore-unmatch cache
git rm -r --cached --ignore-unmatch tmp
git rm -r --cached --ignore-unmatch models
git rm -r --cached --ignore-unmatch datasets

echo.
echo Adding clean state...
git add .gitignore
git add .

echo.
echo Committing cleanup...
git commit -m "cleanup: remove heavy generated files from git"

echo.
echo Repository stats:
git count-objects -vH

echo.
echo Done.
pause
