@echo off
setlocal
cd /d "%~dp0"
echo [1/3] Installing project requirements...
python -m pip install -r requirements.txt || goto :fail
echo [2/3] Compiling Python files...
python -m compileall -q . || goto :fail
echo [3/3] Checking Angel environment configuration...
python verify_angel_setup.py
if errorlevel 1 goto :fail
echo.
echo ANGEL SETUP: PASS
echo No secrets were displayed or written.
pause
exit /b 0
:fail
echo.
echo ANGEL SETUP: FAIL
echo Read the failure line above; do not share .env secrets.
pause
exit /b 1
