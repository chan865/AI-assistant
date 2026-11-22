@echo off
cd /d "%~dp0"
set "APP=assistant8.py"

python --version > nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python was not found. Install Python 3.10 or newer and check "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist "venv_build\Scripts\python.exe" (
  echo Creating build environment...
  python -m venv venv_build
)

echo Installing build tools. This takes a few minutes...
"venv_build\Scripts\python.exe" -m pip install -U pip
"venv_build\Scripts\python.exe" -m pip install -U google-genai pyinstaller
if errorlevel 1 goto fail

echo Building AI.exe ...
"venv_build\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name AI --collect-all google.genai --distpath exe_out --workpath build_tmp --specpath build_tmp %APP%
if errorlevel 1 goto fail

echo.
echo Done: exe_out\AI.exe
explorer exe_out
pause
exit /b 0

:fail
echo [ERROR] Build failed. Read the messages above.
pause
exit /b 1