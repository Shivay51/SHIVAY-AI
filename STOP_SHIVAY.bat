@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0SHIVAY_CONTROL.ps1" -Action Stop
exit /b %errorlevel%
