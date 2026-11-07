@echo off
rem Fa partire Eva ogni volta che accendi il computer.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0eva_avvio_automatico.ps1" attiva
echo.
pause
