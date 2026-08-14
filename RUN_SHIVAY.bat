@echo off
title SHIVAY AI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SHIVAY_CONTROL.ps1" -Action Run
echo.
echo SHIVAY AI has stopped. Press any key to close this window.
pause >nul
