@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python environment not found.
    echo Please create .venv and install dependencies first.
    pause
    exit /b 1
)

start "Wafer Counter Service" /min ".venv\Scripts\python.exe" -u "web_app.py"
timeout /t 3 /nobreak >nul
start "" "https://127.0.0.1:8765"

endlocal
