@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1
if defined GTA_PYTHON goto configured
if exist ".venv\Scripts\python.exe" goto venv
where py >nul 2>nul
if not errorlevel 1 goto launcher
where python >nul 2>nul
if not errorlevel 1 goto python
echo [setup] Python 3.11+ x64 was not found. Install it from https://www.python.org/downloads/windows/
echo [setup] Also install Node.js 20+ LTS from https://nodejs.org and reopen this launcher.
set "result=1"
goto finished
:configured
"%GTA_PYTHON%" "%~dp0run.dev" %*
set "result=%errorlevel%"
goto finished
:venv
"%~dp0.venv\Scripts\python.exe" "%~dp0run.dev" %*
set "result=%errorlevel%"
goto finished
:launcher
py -3 "%~dp0run.dev" %*
set "result=%errorlevel%"
goto finished
:python
python "%~dp0run.dev" %*
set "result=%errorlevel%"
:finished
popd
if "%result%"=="0" exit /b 0
echo.
echo [setup] Startup failed. The error above explains what to fix.
if not "%GTA_NO_PAUSE%"=="1" pause
exit /b %result%
