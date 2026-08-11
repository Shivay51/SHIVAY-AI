@echo off
setlocal
cd /d "%~dp0"
echo Updating the current Git branch...
git pull --ff-only || goto :fail
call VERIFY_ANGEL_SETUP.bat
exit /b %errorlevel%
:fail
echo.
echo UPDATE: FAIL
echo Resolve the Git message above before retrying.
pause
exit /b 1
