@echo off
rem ============================================================================
rem  SmartRoad - ONE-CLICK Windows installer (safe to run again any time)
rem  Installs Python (if missing), virtual environment, all dependencies,
rem  the database, the demo data, the AI pothole model, and a desktop shortcut.
rem ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo.
echo  === SmartRoad one-click installer ===
echo.

rem ---- 1. Python ----------------------------------------------------------
set "PY="
where python >nul 2>&1 && python -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY ( where py >nul 2>&1 && py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PY=py -3" )
if not defined PY (
  echo [1/6] Python 3.9+ not found - trying to install it with winget ...
  where winget >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] winget is not available. Install Python 3.12 from https://www.python.org/downloads/
    echo         ^(tick "Add python.exe to PATH"^) and run this installer again.
    pause & exit /b 1
  )
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  rem the new python is not on PATH of this window yet - look in the usual places
  for %%P in ("%LocalAppData%\Programs\Python\Python312\python.exe" "%ProgramFiles%\Python312\python.exe") do if exist %%P set "PY=%%~P"
  if not defined PY (
    echo [ERROR] Python was installed but this window cannot see it. Close this window and run install_windows.bat again.
    pause & exit /b 1
  )
)
echo [1/6] Python OK ^(%PY%^)

rem ---- 2. Virtual environment ----------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [2/6] Creating virtual environment ...
  %PY% -m venv .venv || ( echo [ERROR] could not create .venv & pause & exit /b 1 )
) else echo [2/6] Virtual environment exists
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. Dependencies ------------------------------------------------------
fc /b requirements-windows.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
  echo [3/6] Installing dependencies ^(a few minutes on first run^) ...
  "%VPY%" -m pip install --upgrade pip >nul 2>&1
  "%VPY%" -m pip install -r requirements-windows.txt || ( echo [ERROR] pip install failed - check your internet connection & pause & exit /b 1 )
  copy /y requirements-windows.txt .venv\requirements.installed >nul
) else echo [3/6] Dependencies already installed

rem ---- 4. Folders, demo data, database -------------------------------------
if not exist logs mkdir logs
if not exist data\hazards mkdir data\hazards
if not exist data\tiles mkdir data\tiles
if not exist data\demo_route.json "%VPY%" scripts\make_demo_data.py
"%VPY%" -m app.main --init-db >nul && echo [4/6] Database ready || echo [WARNING] database initialisation failed

rem ---- 5. AI model ----------------------------------------------------------
echo [5/6] AI pothole model ...
"%VPY%" scripts\install_model.py

rem ---- 6. Desktop shortcut --------------------------------------------------
echo [6/6] Creating desktop shortcut ...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\SmartRoad.lnk'); $s.TargetPath='%~dp0run_windows.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\shell32.dll,44'; $s.Save()" >nul 2>&1 && echo      shortcut "SmartRoad" added to your Desktop || echo      ^(shortcut skipped^)

echo.
echo  === Installation complete ===
echo   Double-click "SmartRoad" on the Desktop, or run:
echo     run_windows.bat            webcam + simulated GPS
echo     run_windows.bat --demo     full demo, no hardware needed
echo   Switch between LIVE / TEST / VIDEO / DEMO inside the app (top bar).
echo.
choice /c YN /m "Start SmartRoad in DEMO mode now"
if errorlevel 2 goto :end
call run_windows.bat --demo
:end
endlocal
