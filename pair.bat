@echo off
chcp 65001 >nul
title Moonlight PIN Pair Tool
cd /d "%~dp0"

echo =====================================================================
echo                    Moonlight Device PIN Pairing
echo =====================================================================
echo.

python "%~dp0pair.py" %*

echo.
pause
