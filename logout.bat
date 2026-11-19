@echo off
chcp 65001 >nul
title Colab Cloud Gaming - Google Colab Account Logout
cd /d "%~dp0"

echo =====================================================================
echo                Google Colab Account Logout Tool
echo    (Clear cached OAuth credentials and switch Google accounts)
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

python "%~dp0logout_colab.py" %*

echo.
pause
