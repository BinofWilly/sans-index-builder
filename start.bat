@echo off
rem Double-click to start SANS Index Builder (opens your browser).
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 sans_index.py %*
) else (
  python sans_index.py %*
)
if errorlevel 1 (
  echo.
  echo Could not start. Install Python 3 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then try again.
  pause
)
