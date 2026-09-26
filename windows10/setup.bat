@echo off
setlocal
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

set "PYTHON_EXE="

if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python314\python.exe"
    goto :python_found
)

if exist "%ProgramFiles%\Python314\python.exe" (
    set "PYTHON_EXE=%ProgramFiles%\Python314\python.exe"
    goto :python_found
)

python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%I"
    goto :python_found
)

py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1
if not errorlevel 1 (
    for /f "delims=" %%I in ('py -3.14 -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%I"
    goto :python_found
)

if not exist "windows10_bundle\python-3.14.6-amd64.exe" (
    echo [ERROR] The bundled Python 3.14.6 installer was not found.
    pause
    exit /b 1
)

echo [1/6] Installing Python 3.14.6 for the current user...
"windows10_bundle\python-3.14.6-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0 Shortcuts=0
if errorlevel 1 goto :python_install_failed

if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python314\python.exe"
    goto :python_found
)

echo [ERROR] Python installation completed, but Python 3.14 could not be located.
echo Restart Windows, then run this setup file again.
pause
exit /b 1

:python_found
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This Python is not Python 3.14: %PYTHON_EXE%
    pause
    exit /b 1
)
echo Using Python: %PYTHON_EXE%

echo [2/6] Creating the application environment...
"%PYTHON_EXE%" -m venv .venv
if errorlevel 1 goto :failed

echo [3/6] Installing dependencies from the internet...
".venv\Scripts\python.exe" -m pip install -r "windows10\requirements.txt"
if errorlevel 1 goto :failed

echo [4/6] Checking dependency consistency...
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto :failed

echo [5/6] Loading the application packages...
".venv\Scripts\python.exe" -c "import cv2,numpy,matplotlib,PIL,cryptography"
if errorlevel 1 goto :failed

echo [6/6] Generating the HTTPS certificate...
".venv\Scripts\python.exe" generate_https_cert.py
if errorlevel 1 goto :failed

echo.
echo Setup completed successfully.
echo Double-click start_app.bat to open the website.
pause
exit /b 0

:python_install_failed
echo.
echo [ERROR] Python installation failed.
echo Run windows10_bundle\python-3.14.6-amd64.exe manually,
echo then run windows10\setup.bat again.
pause
exit /b 1

:failed
echo.
echo [ERROR] Setup failed. Take a photo of this window for troubleshooting.
pause
exit /b 1
