@echo off
setlocal
set "WORKDESK_ROOT=%~dp0"
set "DEV_PYTHON=%ProgramFiles%\Python312\python.exe"
if not exist "%DEV_PYTHON%" set "DEV_PYTHON=python.exe"
"%DEV_PYTHON%" -B -X utf8 "%WORKDESK_ROOT%tools\dev_instance.py" stop
set "DEV_RESULT=%ERRORLEVEL%"
pause
exit /b %DEV_RESULT%
