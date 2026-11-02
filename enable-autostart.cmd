@echo off
cd /d "%~dp0"
where pwsh.exe >nul 2>nul
if not errorlevel 1 (
    pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autostart.ps1" -Install
) else if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" (
    "%ProgramFiles%\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autostart.ps1" -Install
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autostart.ps1" -Install
)
pause
