@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-native.ps1"
set "install_exit=%errorlevel%"
if not "%install_exit%"=="0" pause
exit /b %install_exit%
