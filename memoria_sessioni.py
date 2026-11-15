"""Memoria tra una sessione e l'altra: Eva salva un riassunto breve quando si chiude e all'avvio lo ricorda.

(Non confonderlo con memoria.py, che è la memoria della conversazione in corso, solo in RAM.)
Il file eva_memoria_sessioni.json sta in %APPDATA%\\Eva, fuori dal programma e dal repository.
"""
import json
import random
import threading
from datetime import datetime

import profilo

# Quanti riassunti vecchi tenere al massimo (così il file resta leggero)
MAX_RIASSUNTI = 30
# Quante richieste della sessione entrano nel riassunto, e quanto può essere lunga ciascuna
MAX_RICHIESTE_RIASSUNTO = 5
MAX_CARATTERI_RICHIESTA = 80

_lock = threading.Lock()
_richieste = []            # frasi dette in questa sessione


def _file():
    return profilo.cartella_dati() / "eva_memoria_sessioni.json"


def carica_memoria():
    """Legge tutti i riassunti salvati. Se il file non esiste o è rovinato, restituisce una lista vuota."""
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            dati = json.load(f)
        return dati if isinstance(dati, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def salva_riassunto(testo):
    """Aggiunge un riassunto breve della sessione appena finita."""
    testo = testo.strip()
    if not testo:
        return
    memoria = carica_memoria()
    memoria.append({
        "data": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "riassunto": testo,
    })
    memoria = memoria[-MAX_RIASSUNTI:]
    with open(_file(), "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)


def ultimi_riassunti(n=3):
    """Restituisce gli ultimi n riassunti."""
    return carica_memoria()[-n:]


# ----------------------------------------------------------------------
# Riassunto della sessione in corso
# ----------------------------------------------------------------------
def registra_richiesta(frase):
    """Ricorda una frase detta dall'utente in questa sessione (per il riassunto finale)."""
    frase = " ".join((frase or "").split())
    if frase:
        with _lock:
            _richieste.append(frase)


def riassunto_sessione():
    """Frase breve con le ultime richieste della sessione; stringa vuota se non è successo niente."""
    with _lock:
        ultime = _richieste[-MAX_RICHIESTE_RIASSUNTO:]
    if not ultime:
        return ""
    corte = [r if len(r) <= MAX_CARATTERI_RICHIESTA else r[:MAX_CARATTERI_RICHIESTA - 1].rstrip() + "…"
             for r in ultime]
    return "Mi hai chiesto: " + "; ".join(corte) + "."


def salva_sessione():
    """Alla chiusura: salva il riassunto della sessione (una sola volta) e svuota la lista."""
    testo = riassunto_sessione()
    with _lock:
        _richieste.clear()
    if testo:
        salva_riassunto(testo)
    return testo


# ----------------------------------------------------------------------
# Avvio
# ----------------------------------------------------------------------
def saluto_con_memoria(nome=""):
    """Frase di saluto all'avvio con il riassunto dell'ultima sessione, o "" se non c'è niente da ricordare."""
    import vox_lingue
    recenti = ultimi_riassunti(1)
    if not recenti or vox_lingue.corrente != "it":      # i riassunti sono in italiano: nelle altre lingue vale il benvenuto normale
        return ""
    ultimo = recenti[-1]
    try:
        quando = datetime.strptime(ultimo["data"], "%Y-%m-%d %H:%M").strftime("%d/%m alle %H:%M")
    except (KeyError, ValueError):
        quando = ultimo.get("data", "")
    ciao = random.choice(["Eccomi, capo!", f"Rieccoci, {nome}!" if nome else "Rieccoci!"])
    return (f"{ciao} L'ultima volta, il {quando}, abbiamo fatto questo: {ultimo.get('riassunto', '')} "
            "Vuoi riprendere da dove eravamo rimasti?")


def contesto_per_ia(n=3):
    """Testo da aggiungere come contesto per un modello di intelligenza artificiale."""
    recenti = ultimi_riassunti(n)
    if not recenti:
        return ""
    righe = [f"- {r['data']}: {r['riassunto']}" for r in recenti]
    return "Riassunto delle sessioni precedenti:\n" + "\n".join(righe)
