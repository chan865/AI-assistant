@echo off
set "PORT=8501"
set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a > nul 2>&1
    set "FOUND=1"
)
if "%FOUND%"=="1" (echo AI assistant stopped.) else (echo Assistant is not running.)
timeout /t 2 > nul