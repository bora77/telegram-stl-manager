@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\Install.ps1"
if errorlevel 1 pause
