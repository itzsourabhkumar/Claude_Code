@echo off
REM ---------------------------------------------------------------------------
REM Start the Claude Code token usage dashboard on http://127.0.0.1:8765
REM
REM A convenience wrapper: it uses the project virtualenv when one exists,
REM otherwise whatever Python is on PATH, and runs server.py - the real
REM implementation, identical on Windows, Linux and macOS. The server binds to
REM loopback only and needs no dependencies.
REM
REM   start_dashboard.bat              start on the configured port
REM   start_dashboard.bat --port 9000  start on another port
REM   start_dashboard.bat --no-browser do not open a browser window
REM
REM Linux and macOS: use ./start_dashboard.sh instead.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PYTHON="
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not defined PYTHON if exist "venv\Scripts\python.exe" set "PYTHON=venv\Scripts\python.exe"
if not defined PYTHON set "PYTHON=python"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Python was not found.
    echo   Install Python 3.11+ or create a virtualenv in .venv, then try again.
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting Claude Code Token Usage dashboard...
echo.
"%PYTHON%" server.py %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo   The dashboard exited with code %RC%.
    echo   See logs\server.log for details.
    echo.
    pause
)

endlocal & exit /b %RC%
