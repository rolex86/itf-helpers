@echo off
setlocal

cd /d "%~dp0"

call "%~dp0setup.bat"
if errorlevel 1 (
  echo.
  echo Setup selhal, aplikace nebude spustena.
  pause
  exit /b 1
)

echo.
call "%~dp0run.bat"
set "RC=%errorlevel%"
pause
exit /b %RC%
