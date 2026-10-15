@echo off
rem Double-click to stop the Research Agent: the app and the model server.
setlocal EnableExtensions
title Stop Research Agent
set "APP_PORT=8501"

echo Stopping the app on port %APP_PORT% ...
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%APP_PORT% .*LISTENING"') do (
    set "FOUND=1"
    taskkill /pid %%p /f >nul 2>nul
)
if not defined FOUND echo     The app was not running.

echo Stopping the model server ...
taskkill /im llama-server.exe /f >nul 2>nul
if errorlevel 1 echo     The model server was not running.

echo.
echo Done. This window closes in a few seconds.
timeout /t 4 >nul
