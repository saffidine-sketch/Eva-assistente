@echo off
rem Toglie l'avvio automatico di Eva (il programma resta dov'e').
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0eva_avvio_automatico.ps1" disattiva
echo.
pause
