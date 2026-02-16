@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

py -V >nul 2>&1
if errorlevel 1 (
  echo.
  echo CHYBA: "py" launcher neni dostupny.
  echo Nainstaluj Python 3.12 x64 z python.org a zapni "Add python.exe to PATH".
  exit /b 1
)

set "PY_CMD="
set "PY_EXE="
call :detect_python

if not defined PY_EXE (
  echo.
  echo Nebyl nalezen kompatibilni Python 3.9-3.12.
  echo Pokousim se doinstalovat Python 3.12-64 pomoci "py install 3.12-64"...
  py install 3.12-64
  if errorlevel 1 (
    echo.
    echo CHYBA: Automaticka instalace Pythonu 3.12-64 selhala.
    echo Nainstaluj Python 3.12 x64 rucne z python.org nebo spust "py install 3.12-64".
    echo.
    echo Nalezene interpretery:
    py -0p 2>nul
    exit /b 1
  )
  call :detect_python
)

if not defined PY_EXE (
  echo.
  echo CHYBA: Python 3.12-64 byl instalovan, ale nebyl nalezen kompatibilni interpreter.
  echo.
  echo Nalezene interpretery:
  py -0p 2>nul
  exit /b 1
)

echo Pouzivam interpreter: !PY_CMD!
echo Python exe: !PY_EXE!

if exist ".venv\Scripts\python.exe" (
  call ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo Existujici .venv je vytvorene na nekompatibilnim Pythonu. Obnovuji...
    rmdir /s /q ".venv"
    if exist ".venv\Scripts\python.exe" (
      echo CHYBA: Nepodarilo se smazat puvodni .venv.
      exit /b 1
    )
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Vytvarim .venv...
  call "!PY_EXE!" -m venv .venv
  if errorlevel 1 (
    echo CHYBA: Nepodarilo se vytvorit virtualni prostredi.
    exit /b 1
  )
  if not exist ".venv\Scripts\python.exe" (
    echo CHYBA: Vytvoreni .venv skoncilo, ale .venv\Scripts\python.exe neexistuje.
    exit /b 1
  )
)

echo Instaluji/overuji zavislosti...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo CHYBA: Nepodarilo se aktualizovat pip.
  exit /b 1
)

call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo CHYBA: Instalace zavislosti selhala.
  exit /b 1
)

echo Setup dokonceny.
exit /b 0

:detect_python
set "PY_CMD="
set "PY_EXE="
for %%V in (3.12-64 3.12 3.11-64 3.11 3.10-64 3.10 3.9-64 3.9) do (
  if not defined PY_EXE (
    set "CAND_EXE="
    for /f "usebackq delims=" %%P in (`py -%%V -c "import os,sys; print(os.path.abspath(sys.executable))" 2^>nul`) do (
      set "CAND_EXE=%%P"
    )
    if defined CAND_EXE if exist "!CAND_EXE!" (
      set "PY_CMD=py -%%V"
      set "PY_EXE=!CAND_EXE!"
    )
  )
)
exit /b 0
