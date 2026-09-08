@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-MemoryHub.ps1" %*
exit /b %errorlevel%
