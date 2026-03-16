@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "INSTANCE=%~1"
set "XIANYU_IM_URL=https://www.goofish.com/im?spm"
set "DEFAULT_UI_PORT=8765"
set "DEFAULT_BRIDGE_PORT=18765"
set "DEFAULT_PROJECT_ID=xianyu_edge"
set "BROWSER_CMD=msedge"
set "BROWSER_NAME=Edge"
set "BROWSER_EXE="

if "%INSTANCE%"=="" (
  echo [ERROR] Missing browser instance name. Use: edge or chrome
  pause
  exit /b 1
)

if /I "%INSTANCE%"=="chrome" (
  set "DEFAULT_UI_PORT=8766"
  set "DEFAULT_BRIDGE_PORT=18766"
  set "DEFAULT_PROJECT_ID=xianyu_chrome"
  set "BROWSER_CMD=chrome"
  set "BROWSER_NAME=Chrome"
)

set "ENV_FILE=%PROJECT_DIR%.env.%INSTANCE%"
set "ENV_EXAMPLE=%PROJECT_DIR%.env.%INSTANCE%.example"
set "FALLBACK_ENV=%PROJECT_DIR%.env"
set "FALLBACK_EXAMPLE=%PROJECT_DIR%.env.example"
set "DATA_DIR=data\%INSTANCE%"
set "UI_URL=http://127.0.0.1:%DEFAULT_UI_PORT%"

cd /d "%PROJECT_DIR%"

if not exist "%ENV_FILE%" (
  if exist "%FALLBACK_ENV%" (
    copy /Y "%FALLBACK_ENV%" "%ENV_FILE%" >nul
  ) else if exist "%ENV_EXAMPLE%" (
    copy /Y "%ENV_EXAMPLE%" "%ENV_FILE%" >nul
  ) else if exist "%FALLBACK_EXAMPLE%" (
    copy /Y "%FALLBACK_EXAMPLE%" "%ENV_FILE%" >nul
  )
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%DEFAULT_UI_PORT% .*LISTENING"') do (
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

set "XIANYU_INSTANCE=%INSTANCE%"
set "XIANYU_BROWSER_NAME=%BROWSER_NAME%"
set "XIANYU_ENV_FILE=%ENV_FILE%"
set "XIANYU_DATA_DIR=%DATA_DIR%"
set "COOKIE_SOURCE=plugin"
set "COOKIE_BRIDGE_HOST=127.0.0.1"
set "COOKIE_BRIDGE_PORT=%DEFAULT_BRIDGE_PORT%"
set "COOKIE_PROJECT_ID=%DEFAULT_PROJECT_ID%"
set "COOKIE_BRIDGE_TOKEN="
set "UI_HOST=127.0.0.1"
set "UI_PORT=%DEFAULT_UI_PORT%"

:ResolveBrowserExe
if /I "%INSTANCE%"=="chrome" (
  if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
  if not defined BROWSER_EXE if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
  if not defined BROWSER_EXE if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
) else (
  if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" set "BROWSER_EXE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
  if not defined BROWSER_EXE if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" set "BROWSER_EXE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
  if not defined BROWSER_EXE if exist "%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_EXE=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"
)

if not defined BROWSER_EXE (
  for /f "delims=" %%I in ('where %BROWSER_CMD% 2^>nul') do if not defined BROWSER_EXE set "BROWSER_EXE=%%I"
)

if not defined BROWSER_EXE (
  echo [ERROR] %BROWSER_NAME% is not installed.
  pause
  exit /b 1
)

echo [INFO] Opening %BROWSER_NAME% with existing profile...
start "" "%BROWSER_EXE%" "%UI_URL%"
start "" "%BROWSER_EXE%" "%XIANYU_IM_URL%"

echo [INFO] Starting %BROWSER_NAME% runtime...
"%PYTHON_EXE%" app_ui.py
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo [ERROR] Program exited with code %EXIT_CODE%.
)

pause
exit /b %EXIT_CODE%
