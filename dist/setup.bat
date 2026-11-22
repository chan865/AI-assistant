@echo off
cd /d "%~dp0"
echo ===== AI setup =====

python --version > nul 2>&1
if errorlevel 1 goto nopython
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto nopython

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
)
if not exist "venv\Scripts\python.exe" goto fail

echo Installing packages. This takes 1-3 minutes...
"venv\Scripts\python.exe" -m pip install -U pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo Setup finished. Double-click start.bat to run AI.
pause
exit /b 0

:nopython
echo [ERROR] Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/
echo and check "Add python.exe to PATH" on the first screen. Then run setup.bat again.
pause
exit /b 1

:fail
echo [ERROR] Setup failed. Check your internet connection and run setup.bat again.
pause
exit /b 1