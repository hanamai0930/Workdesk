@echo off
setlocal
set "WORKDESK_ROOT=%~dp0"
set "DEV_PYTHON=%ProgramFiles%\Python312\pythonw.exe"
if not exist "%DEV_PYTHON%" (
  for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined DEV_PYTHON_FOUND set "DEV_PYTHON_FOUND=%%I"
  if defined DEV_PYTHON_FOUND set "DEV_PYTHON=%DEV_PYTHON_FOUND%"
)
if not exist "%DEV_PYTHON%" (
  echo Python 3.12 with pythonw.exe was not found.
  pause
  exit /b 1
)
start "" "%DEV_PYTHON%" -B -X utf8 "%WORKDESK_ROOT%tools\dev_instance.py" start --notify
exit /b 0
