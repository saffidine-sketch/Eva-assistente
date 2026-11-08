"""Dove stanno i file di Eva.

  - Da sorgente (python dettatura.py): tutto accanto al codice, come sempre.
  - Installata (Eva.exe): il programma sta in Programs\\Eva (si sostituisce a ogni aggiornamento);
    impostazioni, rubrica, registri e promemoria stanno in %APPDATA%\\Eva e non si perdono mai.

  dati(nome)     file che Eva legge E scrive (impostazioni, registri, rubrica...)
  risorsa(nome)  file inclusi nel programma, in sola lettura (risposte.json...)
Alla prima volta, un file di dati che manca viene copiato dalla versione di serie inclusa nel programma.
"""
import os
import shutil
import sys
from pathlib import Path

INSTALLATA = bool(getattr(sys, "frozen", False))
CARTELLA_PROGRAMMA = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if INSTALLATA:
    CARTELLA_DATI = Path(os.environ.get("APPDATA") or Path.home()) / "Eva"
    try:
        CARTELLA_DATI.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
else:
    CARTELLA_DATI = CARTELLA_PROGRAMMA


def risorsa(nome):
    return CARTELLA_PROGRAMMA / nome


def dati(nome):
    p = CARTELLA_DATI / nome
    if not p.exists():
        # versione di serie: lo stesso nome, oppure <nome>.esempio.json (rubrica, alias_utente)
        candidati = [CARTELLA_PROGRAMMA / nome,
                     CARTELLA_PROGRAMMA / (Path(nome).stem + ".esempio" + Path(nome).suffix)]
        for di_serie in candidati:
            if di_serie.is_file() and di_serie != p:
                try:
                    shutil.copyfile(di_serie, p)
                except OSError:
                    pass
                break
    return p
