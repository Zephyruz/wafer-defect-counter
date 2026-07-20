@echo off
setlocal
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

set "PYTHON_EXE="

py -3.8 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3.8 -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%I"
    goto :python_found
)

python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 8) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%I"
    goto :python_found
)

if exist "%LocalAppData%\Programs\Python\Python38\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python38\python.exe"
    goto :python_found
)

if exist "%ProgramFiles%\Python38\python.exe" (
    set "PYTHON_EXE=%ProgramFiles%\Python38\python.exe"
    goto :python_found
)

echo [ERROR] Python 3.8 was not found.
echo Checked the Python launcher, PATH, per-user install, and all-user install.
echo Run these commands and take a photo of the results:
echo     python --version
echo     py -3.8 --version
echo     where python
echo     where py
pause
exit /b 1

:python_found
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This Python is not Python 3.8: %PYTHON_EXE%
    pause
    exit /b 1
)
echo Using Python: %PYTHON_EXE%

if not exist "win7_offline_bundle\offline_packages" (
    echo [ERROR] The Win7 offline package folder was not found.
    pause
    exit /b 1
)

echo [1/5] Creating a clean Windows 7 Python environment...
"%PYTHON_EXE%" -m venv .venv_win7
if errorlevel 1 goto :failed

echo [2/5] Installing offline packages...
".venv_win7\Scripts\python.exe" -m pip install --no-index --find-links="win7_offline_bundle\offline_packages" -r "win7\requirements.txt"
if errorlevel 1 goto :failed

echo [3/5] Checking dependency consistency...
".venv_win7\Scripts\python.exe" -m pip check
if errorlevel 1 goto :failed

echo [4/5] Loading the application packages...
".venv_win7\Scripts\python.exe" -c "import cv2,numpy,matplotlib,PIL,cryptography"
if errorlevel 1 goto :failed

echo [5/5] Generating the HTTPS certificate...
".venv_win7\Scripts\python.exe" generate_https_cert.py
if errorlevel 1 goto :failed

echo.
echo Setup completed successfully.
echo Double-click start_app.bat to open the website.
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed. Take a photo of this window for troubleshooting.
pause
exit /b 1
