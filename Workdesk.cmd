@echo off
setlocal
set "WORKDESK_ROOT=%~dp0"
set "PYTHONW=%ProgramFiles%\Python312\pythonw.exe"
if not exist "%PYTHONW%" (
  for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do if not defined PYTHONW_FOUND set "PYTHONW_FOUND=%%I"
  if defined PYTHONW_FOUND set "PYTHONW=%PYTHONW_FOUND%"
)
if not exist "%PYTHONW%" (
  echo Python 3.12 with pythonw.exe was not found. Workdesk could not start.
  pause
  exit /b 1
)
start "" "%PYTHONW%" "%WORKDESK_ROOT%workdesk-ui\server.py" --open
exit /b 0
