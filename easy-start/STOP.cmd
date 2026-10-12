@echo off
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0launcher.ps1" -Action Stop
if errorlevel 1 echo If scripts are blocked, ask IT. This launcher does not bypass company policy.
pause
