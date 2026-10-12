@echo off
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0..\launcher.ps1" -Action Prepare
if errorlevel 1 echo If scripts are blocked, ask IT. This launcher does not bypass company policy.
pause
