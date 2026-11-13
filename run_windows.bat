@echo off
rem SmartRoad - Windows test launcher (idempotent: safe to run repeatedly)
rem   run_windows.bat                      webcam + SIMULATED GPS (test mode)
rem   run_windows.bat --demo               full simulated demonstration (no hardware)
rem   run_windows.bat --camera video --source C:\path\road.mp4
rem   run_windows.bat --gps serial --gps-device COM3     (real USB GPS)
setlocal
cd /d "%~dp0"

rem ---- 1. Python ----------------------------------------------------------
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY ( where py >nul 2>&1 && set "PY=py -3" )
if not defined PY (
  echo [ERROR] Python 3.9+ was not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
  exit /b 1
)

rem ---- 2. Virtual environment ----------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo [SmartRoad] Creating virtual environment ...
  %PY% -m venv .venv || ( echo [ERROR] Could not create .venv & exit /b 1 )
)
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. Dependencies (only when requirements changed) --------------------
fc /b requirements-windows.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
  echo [SmartRoad] Installing dependencies ^(first run takes a few minutes^) ...
  "%VPY%" -m pip install --upgrade pip >nul 2>&1
  "%VPY%" -m pip install -r requirements-windows.txt || ( echo [ERROR] pip install failed & exit /b 1 )
  copy /y requirements-windows.txt .venv\requirements.installed >nul
  copy /y requirements.txt .venv\requirements.base.installed >nul
)

rem ---- 4. Demo data + folders ----------------------------------------------
if not exist data\demo_route.json "%VPY%" scripts\make_demo_data.py
if not exist logs mkdir logs
if not exist data\hazards mkdir data\hazards

rem ---- 4b. AI model (downloaded once; failure is not fatal)
if not exist models\pothole.onnx "%VPY%" scripts\install_model.py

rem ---- 5. Database ---------------------------------------------------------
"%VPY%" -m app.main --init-db || ( echo [ERROR] database initialisation failed & exit /b 1 )

rem ---- 6. Launch -----------------------------------------------------------
echo [SmartRoad] Starting ... the UI opens in your browser. Press Ctrl+C here to stop.
"%VPY%" -m app.main --gps simulated --open-browser %*
set RC=%errorlevel%
if not "%RC%"=="0" echo [SmartRoad] exited with code %RC%. See logs\smartroad.log
endlocal & exit /b %RC%
