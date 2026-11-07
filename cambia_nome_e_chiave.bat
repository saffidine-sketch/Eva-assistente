@echo off
rem Riapre la schermata iniziale di Eva per cambiare il tuo nome o la chiave di Groq.
rem Chiudi Eva prima (icona accanto all'orologio -> Esci); poi riavviala con avvia_eva.bat.
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
start "" "%PYW%" "%~dp0dettatura.py" --impostazioni
