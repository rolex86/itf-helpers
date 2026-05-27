@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "APP_URL=http://127.0.0.1:5000"

if not exist "%PYTHON_EXE%" (
  echo.
  echo Virtualni prostredi .venv nebylo nalezeno.
  echo Nejdriv spust instalaci zavislosti:
  echo   python -m venv .venv
  echo   .venv\Scripts\activate
  echo   python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

start "" "%APP_URL%"
"%PYTHON_EXE%" -m app.web.main

endlocal
