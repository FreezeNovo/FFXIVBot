@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1" %*
set "bot_exit=%errorlevel%"
if not "%bot_exit%"=="0" (
  echo.
  echo FFXIVBot stopped with error %bot_exit%. See the message above.
)
pause
exit /b %bot_exit%
