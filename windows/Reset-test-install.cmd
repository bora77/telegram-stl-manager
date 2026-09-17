@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\Uninstall.ps1" -KeepInstaller
if errorlevel 1 pause
