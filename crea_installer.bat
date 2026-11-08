@echo off
rem Costruisce l'installer di Eva: installer_output\Eva_Setup_2.2.exe
rem Serve (solo su questo PC, non per chi installa): Python con le librerie di requirements.txt,
rem pip install pyinstaller, e Inno Setup 6 (winget install JRSoftware.InnoSetup).
cd /d "%~dp0"

echo === 1/2 Impacchetto Eva con PyInstaller ===
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name Eva ^
  --distpath build_installer\dist --workpath build_installer\work --specpath build_installer ^
  --paths "%~dp0." ^
  --collect-all faster_whisper --collect-all ctranslate2 ^
  --collect-submodules pywinauto --collect-submodules pycaw --collect-submodules comtypes ^
  --hidden-import win32timezone ^
  --add-data "%~dp0risposte.json;." --add-data "%~dp0risposte_en.json;." ^
  --add-data "%~dp0risposte_es.json;." --add-data "%~dp0risposte_fr.json;." ^
  --add-data "%~dp0rubrica.esempio.json;." --add-data "%~dp0alias_utente.esempio.json;." --add-data "%~dp0correzioni.esempio.json;." ^
  dettatura.py
if errorlevel 1 goto errore

echo === 2/2 Creo il programma di installazione (Inno Setup) ===
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 non trovato. Installalo con: winget install JRSoftware.InnoSetup
    goto errore
)
"%ISCC%" eva.iss
if errorlevel 1 goto errore

echo.
echo Fatto: installer_output\Eva_Setup_2.2.exe
pause

exit /b 0

:errore
echo.
echo Costruzione fallita.
pause

exit /b 1
