@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM --- vyber python (preferuj .venv) ---
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo Using Python:
"%PY%" --version
echo.

REM ==========================
REM  AUTO VERSION INCREMENT
REM ==========================
set "VERSION_FILE=%CD%\version.txt"
if not exist "%VERSION_FILE%" (
  echo 1.0.0> "%VERSION_FILE%"
)

set /p VER=<"%VERSION_FILE%"
for /f "tokens=1-3 delims=." %%a in ("%VER%") do (
  set "MAJOR=%%a"
  set "MINOR=%%b"
  set "PATCH=%%c"
)

if not defined MAJOR set "MAJOR=1"
if not defined MINOR set "MINOR=0"
if not defined PATCH set "PATCH=0"

set /a PATCH+=1
set "NEWVER=%MAJOR%.%MINOR%.%PATCH%"
echo %NEWVER%> "%VERSION_FILE%"

REM ✅ konstantní název exe (aby fungovaly zástupci)
set "APPNAME=ShopID_HTML_Generator"

REM !!! FIX: escapovani '>' aby se nevytvarel soubor pojmenovany verzi
echo Version bumped: %VER%  -^>  %NEWVER%
echo Output filename: %APPNAME%.exe
echo.

REM ==========================
REM  CREATE PYINSTALLER VERSION FILE (via Python)
REM ==========================
set "VERFILE=%CD%\pyi_version_file.txt"

"%PY%" -c "import os; p=os.environ['VERFILE']; major=os.environ['MAJOR']; minor=os.environ['MINOR']; patch=os.environ['PATCH']; newver=os.environ['NEWVER']; app=os.environ['APPNAME']; txt=f'''# UTF-8\nVSVersionInfo(\n  ffi=FixedFileInfo(\n    filevers=({major}, {minor}, {patch}, 0),\n    prodvers=({major}, {minor}, {patch}, 0),\n    mask=0x3f,\n    flags=0x0,\n    OS=0x40004,\n    fileType=0x1,\n    subtype=0x0,\n    date=(0, 0)\n  ),\n  kids=[\n    StringFileInfo(\n      [\n        StringTable(\n          '040904B0',\n          [\n            StringStruct('CompanyName', 'ITFutuRe s.r.o.'),\n            StringStruct('FileDescription', 'ShopID HTML Generator'),\n            StringStruct('FileVersion', '{newver}'),\n            StringStruct('InternalName', '{app}'),\n            StringStruct('OriginalFilename', '{app}.exe'),\n            StringStruct('ProductName', 'ShopID HTML Generator'),\n            StringStruct('ProductVersion', '{newver}')\n          ]\n        )\n      ]\n    ),\n    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n  ]\n)\n'''; os.makedirs(os.path.dirname(p), exist_ok=True); open(p,'w',encoding='utf-8',newline='\n').write(txt); print('Wrote', p)" ^
  1>nul 2>nul

if not exist "%VERFILE%" (
  echo ============================
  echo ERROR: Version file not created:
  echo %VERFILE%
  echo Tip: zkus zavrit editor/OneDrive sync nebo zkontroluj Defender Controlled Folder Access
  echo ============================
  pause
  exit /b 1
)

REM ==========================
REM  ensure pyinstaller
REM ==========================
"%PY%" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
  echo PyInstaller not found, installing...
  "%PY%" -m pip install pyinstaller
)

REM ==========================
REM  CLEAN
REM ==========================
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
del /q *.spec 2>nul

echo Building EXE (version %NEWVER%)...
echo (log: build_log.txt)
echo.

REM ==========================
REM  BUILD
REM ==========================
"%PY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --name "%APPNAME%" ^
  --version-file "%VERFILE%" ^
  --add-data "app.py;." ^
  --add-data "launcher.py;." ^
  --add-data "converter.py;." ^
  --add-data "techparams_converter.py;." ^
  --add-data "spin_converter.py;." ^
  --add-data "template_new.j2;." ^
  --add-data "template_tp.j2;." ^
  --collect-all streamlit ^
  --copy-metadata streamlit ^
  --hidden-import streamlit.web.cli ^
  --hidden-import streamlit.runtime.scriptrunner.script_runner ^
  --hidden-import tornado ^
  launcher.py > build_log.txt 2>&1

echo.
if errorlevel 1 (
  echo ============================
  echo BUILD FAILED
  echo See build_log.txt
  echo ============================
  pause
  exit /b 1
)

echo ============================
echo DONE: dist\%APPNAME%.exe
echo Version: %NEWVER%
echo ============================
dir /b dist
echo.
pause
