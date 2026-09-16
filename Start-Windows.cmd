@echo off
setlocal
where wsl.exe >nul 2>&1
if errorlevel 1 (
 echo Windows requires WSL2 with Ubuntu 24.04. See INSTALL.md.
 pause
 exit /b 1
)
for /f "delims=" %%I in ('wsl.exe -d Ubuntu-24.04 wslpath -u "%~dp0."') do set "STL_WSL_PATH=%%I"
if not defined STL_WSL_PATH (
 echo Install Ubuntu with: wsl --install -d Ubuntu-24.04
 pause
 exit /b 1
)
wsl.exe -d Ubuntu-24.04 --cd "%STL_WSL_PATH%" bash ./start.sh
pause
