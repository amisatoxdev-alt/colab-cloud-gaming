@echo off
chcp 65001 >nul
title Colab Cloud Gaming - Production Launcher
cd /d "%~dp0"

echo =====================================================================
echo    Colab Cloud Gaming (Ultra Low Latency + Anti-Lag + Anti-Flag)
echo    Native 1366x768 ^| NVENC P1 CBR ^| Openbox ^| Steam ^| PulseAudio 2ms
echo =====================================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python was not found in your system PATH.
    echo Please install Python 3.10+ and make sure to check "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python runner.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Launcher process exited with error code %ERRORLEVEL%.
)
echo.
pause
