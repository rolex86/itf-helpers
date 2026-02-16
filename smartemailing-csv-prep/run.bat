@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo CHYBA: .venv neexistuje. Spust nejdriv setup.bat nebo start.bat.
  exit /b 1
)

call ".venv\Scripts\python.exe" -m streamlit --version >nul 2>&1
if errorlevel 1 (
  echo CHYBA: Modul streamlit neni nainstalovany v .venv.
  echo Spust setup.bat pro instalaci zavislosti.
  exit /b 1
)

echo.
echo Spoustim aplikaci...
echo http://localhost:8501
echo.

call ".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501
exit /b %errorlevel%
