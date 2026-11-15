"""Profilo di Eva: come ti chiama e con quale stile parla (formale / amichevole).

Si salva in %APPDATA%\\Eva\\profilo_eva.json (cartella dati dell'utente, FUORI dal pacchetto e dal repository).
Si cambia dalla finestra "Impostazioni" (icona accanto all'orologio) oppure modificando il file a mano.
Il nome e lo stile entrano nel prompt di sistema di Groq (vedi istruzioni_extra) e nelle frasi fisse.
Niente chiavi qui dentro: la chiave Groq resta nella variabile d'ambiente GROQ_API_KEY.
"""
import json
import logging
import os
import threading
from pathlib import Path

STILI = ("amichevole", "formale")
NOME_STORICO = "@NOME@"         # segnaposto scritto nei testi originali: viene sostituito con il nome del profilo
_lock = threading.Lock()
_cache = None
_log = logging.getLogger("eva")


def cartella_dati():
    """%APPDATA%\\Eva (si crea da sola). Se APPDATA manca, la cartella utente."""
    base = os.environ.get("APPDATA") or str(Path.home())
    cartella = Path(base) / "Eva"
    try:
        cartella.mkdir(parents=True, exist_ok=True)
    except OSError:
        _log.exception("Non riesco a creare la cartella dati di Eva")
    return cartella


def _file():
    return cartella_dati() / "profilo_eva.json"


def carica(forza=False):
    global _cache
    with _lock:
        if _cache is not None and not forza:
            return dict(_cache)
        dati = {"nome": "", "stile": "amichevole", "informativa_vista": False}
        try:
            letti = json.loads(_file().read_text(encoding="utf-8"))
            if isinstance(letti, dict):
                dati.update({k: letti[k] for k in dati if k in letti})
        except FileNotFoundError:
            pass
        except Exception:
            _log.exception("Non riesco a leggere profilo_eva.json: uso i valori predefiniti")
        if dati["stile"] not in STILI:
            dati["stile"] = "amichevole"
        dati["nome"] = str(dati["nome"] or "").strip()[:40]
        _cache = dati
        return dict(dati)


def salva(**campi):
    """Aggiorna solo i campi indicati (nome, stile, informativa_vista). Restituisce True se scritto."""
    global _cache
    dati = carica()
    for k, v in campi.items():
        if k in dati:
            dati[k] = v
    if dati["stile"] not in STILI:
        dati["stile"] = "amichevole"
    dati["nome"] = str(dati["nome"] or "").strip()[:40]
    with _lock:
        _cache = dati
        try:
            _file().write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except OSError:
            _log.exception("Non riesco a salvare profilo_eva.json")
            return False


def nome():
    """Il nome scelto, oppure "" se non è stato impostato."""
    return carica()["nome"]


def stile():
    return carica()["stile"]


def primo_avvio():
    return not carica()["informativa_vista"]


def personalizza(testo, nome_predefinito=NOME_STORICO):
    """Sostituisce il segnaposto @NOME@ dei testi originali con il nome del profilo."""
    n = nome() or "amico"                   # senza nome scelto: "amico"
    if testo and nome_predefinito and n != nome_predefinito:
        return testo.replace(nome_predefinito, n)
    return testo


_STILE_TESTO = {
    "amichevole": "Style: friendly and warm, informal, like a close friend (use the informal 'you': tu / tu / tú).",
    "formale": "Style: formal and polite, respectful and measured (use the formal 'you': Lei / vous / usted), "
               "no slang, no over-familiar remarks.",
}


PERSONALITA = ("Personality: you are witty and playfully ironic, and you tease the user with affection, like a clever friend "
               "who genuinely likes them. Make a light joke or a gentle jab now and then, never in every answer and never "
               "more than one short quip at a time, and only when it fits. Clarity always comes first: answer the question, "
               "then add the quip. Stay warm and straightforward, with no jokes, when the topic is serious (health, money "
               "problems, bad news, mistakes with consequences) or when the user sounds stressed. Never be mean, never "
               "sarcastic about the user's abilities in a hurtful way, and never repeat the same joke or the same opening "
               "twice: vary your wording every time. Quips must sound like natural spoken language: short, simple, in the same "
               "informal register as the rest (always 'tu', never 'Lei' in Italian), never stiff or literary, and with no "
               "invented facts about the user's life or habits (never mention places, people or events you were not told about). "
               "Aim the quip at the situation or at yourself, in one short sentence; if no genuinely natural joke comes to "
               "mind, skip it: a plain, friendly answer is better than a forced joke.")


def istruzioni_extra():
    """Righe da aggiungere al prompt di sistema: nome, stile, chiarimenti e dissenso garbato."""
    n = nome()
    righe = []
    if n:
        righe.append(f"The user wants to be called {n}. Use that name now and then, not in every sentence.")
    righe.append(_STILE_TESTO[stile()])
    if stile() == "amichevole":              # lo stile formale resta serio: niente battute
        righe.append(PERSONALITA)
    righe.append("If a request is ambiguous or you are not sure what the user means, ask one short clarifying "
                 "question instead of guessing. If you disagree with something the user says or asks, or you think "
                 "it is a mistake, say so politely and briefly, give your reason, and then respect their decision. "
                 "Never invent facts: if you do not know, say so.")
    return "\n" + " ".join(righe)


# ----------------------------------------------------------------------
# Informativa sulla privacy (mostrata al primo avvio e dal menu "Privacy")
# ----------------------------------------------------------------------
INFORMATIVA = (
    "Cosa resta sul tuo PC\n"
    "- La voce: il microfono viene trascritto in locale (Whisper). L'audio non lascia mai il computer.\n"
    "- Il tuo profilo (nome e stile) e la memoria di Eva (appunti e cose da ricordare), in %APPDATA%\\Eva, fuori dal programma.\n"
    "- Le impostazioni, nella cartella di Eva.\n"
    "- L'elenco delle tue cartelle (indice), i promemoria, la rubrica e i registri (eva.log).\n"
    "- La memoria della conversazione in corso: solo in RAM, sparisce quando Eva si chiude.\n"
    "- La tua chiave Groq: nella variabile d'ambiente GROQ_API_KEY del tuo utente Windows, mai nei file di Eva.\n"
    "\n"
    "Cosa viene inviato a Groq (servizio gratuito, groq.com)\n"
    "- Il TESTO di quello che dici (già trascritto) e le ultime frasi della conversazione.\n"
    "- Le istruzioni di Eva, che contengono il nome, lo stile e i tuoi appunti più recenti (fino a 20 voci, 1500 caratteri). "
    "Non scrivere negli appunti password o dati che non vuoi mandare a Groq.\n"
    "- Non vengono inviati: l'audio, i tuoi file e l'indice delle cartelle. Se nomini una cartella a voce, "
    "il nome fa parte del testo che dici e quindi va a Groq.\n"
    "- Meteo: con la ricerca attiva (METEO_CON_RICERCA = True) la domanda sul meteo passa dalla ricerca su internet, come "
    "risultati e notizie. Con METEO_CON_RICERCA = False solo la città va a Open-Meteo (non a Groq).\n"
    "- Ricerca su internet (se attiva): quando Eva non sa una cosa, Groq decide di cercare. La domanda di ricerca, "
    "scritta da Groq, va ai motori di ricerca interrogati da ddgs (per esempio DuckDuckGo, Yahoo, Brave, Wikipedia); titoli e riassunti dei risultati tornano a Groq. "
    "Il prompt gli vieta di metterci nome e appunti, ma la domanda resta basata su quello che dici. "
    "Si spegne con AGENTE_RICERCA = False in dettatura.py.\n"
    "- Modalità vista (solo quando dici «Eva, guarda» o «cosa vedi»): lo screenshot di TUTTO lo schermo (tutti i monitor), "
    "ridotto, viene inviato a Groq insieme alla tua domanda. Non viene salvato su disco. Usala solo quando sullo schermo "
    "non c'è niente di riservato (password, banca, messaggi privati).\n"
    "\n"
    "Puoi rileggere questo testo dal menu di Eva (icona accanto all'orologio) > Privacy."
)
