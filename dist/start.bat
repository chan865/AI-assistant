@echo off
set "APP=assistant7.py"
set "PORT=8501"
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"

if not exist "%PY%" (
    echo Setup has not been run yet. Double-click setup.bat first.
    pause
    exit /b 1
)

netstat -ano | findstr ":%PORT%" | findstr "LISTENING" > nul
if %errorlevel%==0 (
    start "" http://localhost:%PORT%
    exit /b 0
)

title AI Assistant
"%PY%" -m streamlit run %APP% --server.port %PORT% --server.showEmailPrompt false --browser.gatherUsageStats false
if errorlevel 1 pause