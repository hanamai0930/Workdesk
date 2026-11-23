@echo off
setlocal
chcp 65001 >nul
set "PYTHON=%ProgramFiles%\Python312\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -X utf8 "%~dp0tools\sync_github.py" %*
set "SYNC_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %SYNC_RESULT%
