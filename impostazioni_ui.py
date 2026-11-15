"""Finestra "Impostazioni" di Eva (nome e stile) e finestra "Privacy". Solo PySide6; i dati stanno in profilo.py."""
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QVBoxLayout)

import profilo

_STILI_NOMI = {"amichevole": "Amichevole (ti dà del tu, calda)", "formale": "Formale (educata, ti dà del Lei)"}


def mostra_privacy(genitore=None, primo_avvio=False):
    """Mostra cosa resta sul PC e cosa va a Groq. Al primo avvio chiede anche nome e stile."""
    d = QDialog(genitore)
    d.setWindowTitle("Eva - Benvenuto e privacy" if primo_avvio else "Eva - Privacy")
    d.resize(560, 520)
    lay = QVBoxLayout(d)
    testo = QPlainTextEdit(profilo.INFORMATIVA)
    testo.setReadOnly(True)
    lay.addWidget(testo)
    campo_nome = combo = None
    if primo_avvio:
        form = QFormLayout()
        campo_nome = QLineEdit(profilo.nome())
        campo_nome.setPlaceholderText("Come vuoi che ti chiami? (facoltativo)")
        combo = QComboBox()
        for k in profilo.STILI:
            combo.addItem(_STILI_NOMI[k], k)
        form.addRow("Il tuo nome:", campo_nome)
        form.addRow("Stile:", combo)
        lay.addLayout(form)
    pulsanti = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    pulsanti.button(QDialogButtonBox.StandardButton.Ok).setText("Ho capito, avanti" if primo_avvio else "Chiudi")
    pulsanti.accepted.connect(d.accept)
    lay.addWidget(pulsanti)
    d.exec()
    if primo_avvio:
        profilo.salva(nome=campo_nome.text(), stile=combo.currentData(), informativa_vista=True)


def mostra_impostazioni(genitore=None):
    """Nome con cui Eva ti chiama e stile. Si applicano subito (il prompt è ricostruito a ogni risposta)."""
    d = QDialog(genitore)
    d.setWindowTitle("Eva - Impostazioni")
    lay = QVBoxLayout(d)
    form = QFormLayout()
    campo_nome = QLineEdit(profilo.nome())
    campo_nome.setPlaceholderText("Come vuoi che ti chiami")
    combo = QComboBox()
    for k in profilo.STILI:
        combo.addItem(_STILI_NOMI[k], k)
    combo.setCurrentIndex(max(0, combo.findData(profilo.stile())))
    form.addRow("Il tuo nome:", campo_nome)
    form.addRow("Stile:", combo)
    lay.addLayout(form)
    lay.addWidget(QLabel("Salvato in: " + str(profilo.cartella_dati() / "profilo_eva.json")))
    pulsanti = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    pulsanti.accepted.connect(d.accept)
    pulsanti.rejected.connect(d.reject)
    lay.addWidget(pulsanti)
    if d.exec():
        profilo.salva(nome=campo_nome.text(), stile=combo.currentData())
        return True
    return False
