@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "UI_PORT=8765"
set "XIANYU_IM_URL=https://www.goofish.com/im?spm"

cd /d "%PROJECT_DIR%"

if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="UI_PORT" set "UI_PORT=%%B"
  )
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%UI_PORT% .*LISTENING"') do (
  taskkill /PID %%P /F >nul 2>&1
)

if not exist "%PYTHON_EXE%" (
  echo [INFO] Creating virtual environment...
  py -3.11 -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )

  echo [INFO] Installing dependencies...
  "%PYTHON_EXE%" -m pip install --upgrade pip
  if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    pause
    exit /b 1
  )

  "%PYTHON_EXE%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
  )
)

echo [INFO] Starting Web UI mode...
set "UI_URL=http://127.0.0.1:%UI_PORT%"
call :OpenUrl "%UI_URL%"
call :OpenUrl "%XIANYU_IM_URL%"
"%PYTHON_EXE%" app_ui.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo [ERROR] Program exited with code %EXIT_CODE%.
)

pause
exit /b %EXIT_CODE%

:OpenUrl
set "TARGET_URL=%~1"
start "" "%TARGET_URL%" >nul 2>&1
if errorlevel 1 (
  where msedge >nul 2>&1
  if not errorlevel 1 (
    start "" msedge "%TARGET_URL%" >nul 2>&1
    exit /b 0
  )
  where chrome >nul 2>&1
  if not errorlevel 1 (
    start "" chrome "%TARGET_URL%" >nul 2>&1
    exit /b 0
  )
  echo [WARN] Could not auto-open: %TARGET_URL%
)
exit /b 0
