@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please run the normal launcher first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
