@echo off
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0support\Start.ps1"
if errorlevel 1 pause
