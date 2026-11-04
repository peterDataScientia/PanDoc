@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_computer_d_autostart.ps1"
if errorlevel 1 (
  echo.
  echo Auto-start task installation failed. Run this CMD under your normal Windows account.
  exit /b 1
)
echo.
echo Windows on-logon task installed. Existing running worker does not need to be stopped.
