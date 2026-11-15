"""Trascrizione incerta: se Eva non ha sentito bene una parola importante, chiede di ripetere invece di indovinare.

Whisper dà una probabilità per ogni parola (word_timestamps=True). Una parola e' SOSPETTA se:
  1. ha una probabilita' bassa (sotto PROB_MIN) ed e' una parola vera (almeno 3 lettere, non il nome "Eva");
  2. oppure assomiglia a un NOME NOTO (nomi_noti.json: squadre, ecc.) senza essere quel nome ("Linter" invece di
     "Inter") e non e' del tutto sicura (probabilita' sotto PROB_NOMI). Le parole vere che somigliano a un nome
     ("milano", "intero") stanno nell'elenco "valide" di nomi_noti.json e non contano.
La domanda "puo' ripetere?" si fa solo per frasi IMPORTANTI (ricerche, ordini con un nome): una chiacchiera con una
parola storpiata passa. Eva NON propone cosa credeva di aver sentito: chiede e basta.

Il file nomi_noti.json si puo' ampliare a mano ("nomi": cio' che Eva deve sentire bene; "valide": parole comuni da
non scambiare per un nome). Non contiene dati personali.
"""
import difflib
import json
import logging
import re
import unicodedata
from pathlib import Path

PROB_MIN = 0.45             # sotto questa probabilita' una parola e' sospetta (impostazione PAROLA_PROB_MIN)
PROB_NOMI = 0.85            # un quasi-nome e' sospetto se la probabilita' e' sotto questa
SOMIGLIANZA_MIN = 0.8
import eva_percorsi as percorsi
FILE_NOMI = percorsi.dati("nomi_noti.json")      # copia modificabile in %APPDATA%\Eva (la prima volta parte da quella di serie)
_log = logging.getLogger("eva")
_cache = None
# parole grammaticali: Whisper le sente con probabilita' bassa anche quando vanno bene ("chi", "che", "a"): non contano
FUNZIONALI = {"che", "chi", "per", "con", "non", "una", "uno", "del", "dei", "gli", "nel", "sul", "tra", "fra", "come",
              "cosa", "sono", "hai", "ho", "ha", "mi", "ti", "ci", "vi", "lo", "la", "le", "il", "un", "di", "da", "in",
              "su", "ma", "se", "ed", "dal", "dai", "nei", "alle", "alla", "allo", "agli", "della", "delle", "dello",
              "degli", "sui", "poi", "piu", "anche", "ancora", "dove", "quando", "qual", "quale", "quali", "oggi", "ieri",
              "the", "and", "for", "you", "what", "who", "how", "les", "des", "une", "est", "que", "qui", "los", "las",
              "una", "que", "por", "con"}


def _piega(t):
    t = unicodedata.normalize("NFD", str(t).lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def _carica():
    global _cache
    if _cache is None:
        nomi, valide = [], set()
        try:
            d = json.loads(FILE_NOMI.read_text(encoding="utf-8"))
            nomi = [_piega(n) for n in d.get("nomi", []) if len(_piega(n)) >= 4]
            valide = {_piega(v) for v in d.get("valide", [])}
        except FileNotFoundError:
            pass
        except Exception:
            _log.exception("Non riesco a leggere nomi_noti.json: controllo solo le probabilita' delle parole")
        _cache = (nomi, valide)
    return _cache


def ricarica():
    global _cache
    _cache = None


def _quasi_nome(parola, nomi, valide):
    """Il nome noto a cui la parola somiglia (senza esserlo) o None."""
    w = _piega(parola)
    if len(w) < 4 or w in valide or w in nomi:
        return None
    for n in nomi:
        if abs(len(w) - len(n)) > 2:
            continue
        if difflib.SequenceMatcher(None, w, n).ratio() >= SOMIGLIANZA_MIN or (
                len(n) >= 5 and (w.endswith(n) or w.startswith(n))):
            return n
    return None


def parola_sospetta(parole, prob_min=PROB_MIN, prob_nomi=PROB_NOMI, ignora=("eva",)):
    """parole: [(testo, probabilita)]. Restituisce (parola, motivo, tipo) della piu' sospetta, oppure None.
    tipo: "nome" (somiglia a un nome noto: si chiede sempre) o "prob" (probabilita' bassa: si chiede se la frase e' importante)."""
    nomi, valide = _carica()
    migliore, peggio = None, 2.0
    for testo, prob in parole or []:
        w = _piega(testo)
        if len(w) < 3 or w.isdigit() or w in ignora or prob is None:
            continue
        motivo, tipo = None, None
        quasi = _quasi_nome(testo, nomi, valide) if prob < prob_nomi else None
        if quasi:
            motivo, tipo = f"somiglia a «{quasi}» ma non e' sicura ({prob:.2f})", "nome"
        elif prob < prob_min and w not in FUNZIONALI:
            motivo, tipo = f"probabilita' bassa ({prob:.2f})", "prob"
        if motivo and (tipo == "nome" or prob < peggio):
            if tipo == "nome" and migliore and migliore[2] == "nome" and prob >= peggio:
                continue
            migliore, peggio = (str(testo).strip(" ,.;:!?«»\"'"), motivo, tipo), prob
    return migliore


_DOMANDA = {
    "it": "Non ho capito bene la parola «{p}». Puoi ripetere?",
    "fr": "Je n'ai pas bien compris le mot « {p} ». Tu peux répéter ?",
    "es": "No he entendido bien la palabra «{p}». ¿Puedes repetir?",
    "pt": "Não entendi bem a palavra «{p}». Pode repetir?",
    "en": "I didn't catch the word \"{p}\" well. Can you repeat that?",
}


def domanda(parola, lingua="it"):
    return _DOMANDA.get(lingua, _DOMANDA["it"]).format(p=parola)
