"""Registro leggibile di Eva: eva_log.txt (nella cartella del progetto).

Una riga per ogni frase riconosciuta:
    data e ora | FRASE | TRATTATA COME (comando / chat con Groq / ...) | RISPOSTA DI GROQ
e una riga "COMANDO NON RICONOSCIUTO" quando un comando (apri, copia...) non viene capito.
Scrivere nel registro non deve mai far fallire Eva: ogni errore viene ignorato.
"""
import datetime as dt
import threading
from pathlib import Path

FILE_REGISTRO = Path(__file__).with_name("eva_log.txt")
MAX_BYTE = 1_000_000            # oltre 1 MB il vecchio file diventa eva_log.old.txt (se ne tiene uno)
_blocco = threading.Lock()


def _una_riga(testo, massimo=800):
    testo = " ".join(str(testo).split())
    return testo if len(testo) <= massimo else testo[:massimo] + "…"


def scrivi(riga):
    """Aggiunge una riga con data e ora."""
    adesso = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _blocco:
            if FILE_REGISTRO.exists() and FILE_REGISTRO.stat().st_size > MAX_BYTE:
                FILE_REGISTRO.replace(FILE_REGISTRO.with_name("eva_log.old.txt"))
            with open(FILE_REGISTRO, "a", encoding="utf-8") as f:
                f.write(f"{adesso} | {riga}\n")
    except Exception:
        pass


def frase(testo, trattata_come, risposta_groq=""):
    scrivi(f"FRASE: {_una_riga(testo)} | TRATTATA COME: {trattata_come} | "
           f"RISPOSTA GROQ: {_una_riga(risposta_groq) if risposta_groq else '(nessuna: Groq non è stato interpellato)'}")


def non_riconosciuto(testo, motivo):
    scrivi(f"COMANDO NON RICONOSCIUTO: {_una_riga(testo)} | {_una_riga(motivo)}")
