@echo off
rem Avvia Eva senza finestra nera (pythonw). Gli errori vanno in eva.log.
cd /d "%~dp0"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" (
    set "PYW="
    for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
)
if not defined PYW (
    echo pythonw non trovato. Controlla che Python sia installato.
    pause
    exit /b 1
)
start "" "%PYW%" "%~dp0dettatura.py"
