@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
if exist ".venv_win7\Scripts\python.exe" set "PYTHON_EXE=.venv_win7\Scripts\python.exe"
if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

if not defined PYTHON_EXE (
    echo [ERROR] Python environment not found.
    echo Run win7\setup.bat first on Windows 7.
    pause
    exit /b 1
)

start "Wafer Counter Service" /min "%PYTHON_EXE%" -u "web_app.py"
timeout /t 3 /nobreak >nul
start "" "https://127.0.0.1:8765"

endlocal
