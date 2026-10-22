@echo off
setlocal
cd /d "%~dp0"

if not exist "vendor\ffmpeg.exe" (
    echo [ERROR] vendor\ffmpeg.exe is missing.
    echo Please use the packaged EXE or restore the vendor folder.
    pause
    exit /b 1
)

if exist ".venv\Scripts\pythonw.exe" goto run

echo First run: creating Python environment and installing dependencies...
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto fail

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail

:run
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:fail
echo.
echo [ERROR] Setup failed. Check that Python 3.11+ and internet access are available.
pause
exit /b 1
