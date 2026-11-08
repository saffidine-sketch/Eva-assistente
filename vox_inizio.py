"""Schermata iniziale di Eva: il nome di chi la usa e la chiave gratuita di Groq.

Si apre da sola al primo avvio (o quando manca il nome o la chiave) e si riapre con cambia_nome_e_chiave.bat.

  - Il NOME si salva in impostazioni_eva.json (chiave "nome_utente"): non è un segreto.
  - La CHIAVE di Groq NON si scrive mai in un file di Eva. Si salva come variabile d'ambiente dell'utente Windows
    (GROQ_API_KEY), la stessa cosa che fa il comando "setx GROQ_API_KEY ...". Non compare nemmeno nei registri (log).
"""
import ctypes
import json
import os
from pathlib import Path

import eva_percorsi as percorsi
FILE_IMPOSTAZIONI = percorsi.dati("impostazioni_eva.json")
VARIABILE = "GROQ_API_KEY"
_REGISTRO = "Environment"                     # HKEY_CURRENT_USER\Environment: le variabili d'ambiente dell'utente
LINK_CHIAVI = "https://console.groq.com/keys"
NOME_MAX = 30


# ----------------------------------------------------------------------
# Nome (impostazioni_eva.json)
# ----------------------------------------------------------------------
def _leggi():
    try:
        d = json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def nome_salvato():
    return str(_leggi().get("nome_utente", "") or "").strip()


def pulisci_nome(testo):
    """Il nome come lo dirà Eva: una riga, senza spazi in eccesso, al massimo NOME_MAX caratteri."""
    return " ".join(str(testo or "").split())[:NOME_MAX].strip()


def salva_nome(nome):
    d = _leggi()
    d["nome_utente"] = nome
    FILE_IMPOSTAZIONI.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# Chiave di Groq (variabile d'ambiente dell'utente, mai un file)
# ----------------------------------------------------------------------
def chiave_valida(chiave):
    """Controllo di forma (niente chiamate in rete): le chiavi di Groq iniziano con gsk_ e non hanno spazi."""
    return bool(chiave) and chiave.startswith("gsk_") and len(chiave) >= 20 and not any(c.isspace() for c in chiave)


def _chiave_dal_registro():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRO) as k:
            return str(winreg.QueryValueEx(k, VARIABILE)[0] or "").strip()
    except OSError:
        return ""


def chiave_presente():
    """True se la chiave c'è. Se esiste solo nel registro di Windows (es. appena salvata) la porta in questo processo."""
    if os.environ.get(VARIABILE, "").strip():
        return True
    k = _chiave_dal_registro()
    if k:
        os.environ[VARIABILE] = k
        return True
    return False


def salva_chiave(chiave):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REGISTRO, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VARIABILE, 0, winreg.REG_SZ, chiave)
    os.environ[VARIABILE] = chiave                 # vale subito, senza riaprire Eva
    try:                                           # avvisa Windows: i nuovi programmi vedono la variabile
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass


# ----------------------------------------------------------------------
# Finestra
# ----------------------------------------------------------------------
def _crea_finestra(nome_iniziale, ha_chiave):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)

    class Schermata(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Eva - Benvenuto")
            self.setMinimumWidth(460)
            self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
            self.nome = ""
            self.chiave = ""                      # vuota = lascia quella già salvata
            vb = QVBoxLayout(self)
            vb.setSpacing(8)

            titolo = QLabel("<h2>Ciao, sono Eva</h2>")
            vb.addWidget(titolo)
            vb.addWidget(QLabel("Prima di cominciare mi servono due cose."))

            vb.addWidget(QLabel("<b>1. Come ti chiamo?</b>"))
            self.campo_nome = QLineEdit(nome_iniziale)
            self.campo_nome.setPlaceholderText("Il tuo nome")
            self.campo_nome.setMaxLength(NOME_MAX)
            vb.addWidget(self.campo_nome)

            vb.addWidget(QLabel("<b>2. La tua chiave gratuita di Groq</b>"))
            guida = QLabel(f'Serve per farmi capire e rispondere. Creala qui, è gratis: '
                           f'<a href="{LINK_CHIAVI}">console.groq.com/keys</a><br>'
                           'Premi <i>Create API Key</i>, copiala (Groq la mostra una volta sola) e incollala qui sotto.')
            guida.setOpenExternalLinks(True)
            guida.setWordWrap(True)
            vb.addWidget(guida)
            self.campo_chiave = QLineEdit()
            self.campo_chiave.setEchoMode(QLineEdit.EchoMode.Password)
            self.campo_chiave.setPlaceholderText("già salvata: lascia vuoto per non cambiarla" if ha_chiave else "gsk_...")
            vb.addWidget(self.campo_chiave)
            mostra = QCheckBox("Mostra la chiave")
            mostra.toggled.connect(lambda on: self.campo_chiave.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
            vb.addWidget(mostra)
            nota = QLabel("La chiave non viene scritta in nessun file di Eva: resta nelle variabili d'ambiente "
                          "del tuo utente Windows.")
            nota.setWordWrap(True)
            nota.setStyleSheet("color: gray;")
            vb.addWidget(nota)

            self.errore = QLabel("")
            self.errore.setStyleSheet("color: #c0392b;")
            self.errore.setWordWrap(True)
            vb.addWidget(self.errore)

            riga = QHBoxLayout()
            riga.addStretch(1)
            esci = QPushButton("Esci")
            esci.clicked.connect(self.reject)
            self.avvia = QPushButton("Avvia Eva")
            self.avvia.setDefault(True)
            self.avvia.clicked.connect(self.conferma)
            riga.addWidget(esci)
            riga.addWidget(self.avvia)
            vb.addLayout(riga)

        def conferma(self):
            nome = pulisci_nome(self.campo_nome.text())
            chiave = self.campo_chiave.text().strip()
            if not nome:
                self.errore.setText("Scrivi il tuo nome.")
                return
            if chiave and not chiave_valida(chiave):
                self.errore.setText("Questa non sembra una chiave di Groq: inizia con gsk_ e non ha spazi.")
                return
            if not chiave and not ha_chiave:
                self.errore.setText("Incolla la tua chiave di Groq (si crea gratis dal link qui sopra).")
                return
            self.nome, self.chiave = nome, chiave
            self.accept()

    return Schermata()


def assicura(imp, log, forza=False):
    """Controlla che ci siano nome e chiave; se manca qualcosa (o forza=True) mostra la schermata iniziale.
    Mette il nome in imp["NOME_UTENTE"]. Restituisce False se chi usa Eva chiude la finestra senza finire."""
    nome = nome_salvato()
    ha_chiave = chiave_presente()
    if nome and ha_chiave and not forza:
        imp["NOME_UTENTE"] = nome
        return True
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        log("Manca PySide6, non posso mostrare la schermata iniziale: pip install -r requirements.txt")
        return False
    QApplication.instance() or QApplication([])
    finestra = _crea_finestra(nome, ha_chiave)
    if not finestra.exec():
        log("Schermata iniziale chiusa senza finire: Eva non parte.")
        return False
    try:
        salva_nome(finestra.nome)
        if finestra.chiave:
            salva_chiave(finestra.chiave)
    except Exception:
        log.errore("Non riesco a salvare il nome o la chiave")           # (il messaggio non contiene mai la chiave)
        return False
    imp["NOME_UTENTE"] = finestra.nome
    log(f"Impostazioni iniziali salvate: nome '{finestra.nome}'" + (", chiave di Groq aggiornata." if finestra.chiave else "."))
    return True
