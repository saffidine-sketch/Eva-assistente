"""Meteo di Eva. Il codice cerca le previsioni su Open-Meteo (gratis, senza chiave); Groq NON cerca niente:
riceve i dati già pronti e li dice in una frase breve (se Groq non risponde, la frase la compone il codice).

  - riconosci(frase)     -> None oppure {"citta": "Milano" | None, "giorno": 0 (oggi) | 1 (domani)}
  - cerca(citta, giorno) -> (stato, dati)   stato: "ok" | "offline" | "non_trovata" | "errore"
  - citta_salvata() / salva_citta(c): città predefinita in impostazioni_eva.json (chiave "citta_meteo")
  - frase_per_voce(dati) -> frase da dire (Groq, con ripiego locale)
"""
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import eva_percorsi as percorsi
import vox_lingue as lingue

FILE_IMPOSTAZIONI = percorsi.dati("impostazioni_eva.json")
TIMEOUT = 6
_log = logging.getLogger("eva")

CIELO = {0: "sereno", 1: "prevalentemente sereno", 2: "parzialmente nuvoloso", 3: "coperto",
         45: "nebbia", 48: "nebbia", 51: "pioggerella", 53: "pioggerella", 55: "pioggerella",
         56: "pioggerella gelata", 57: "pioggerella gelata", 61: "pioggia leggera", 63: "pioggia", 65: "pioggia forte",
         66: "pioggia gelata", 67: "pioggia gelata", 71: "neve leggera", 73: "neve", 75: "neve forte", 77: "nevischio",
         80: "rovesci", 81: "rovesci", 82: "rovesci forti", 85: "rovesci di neve", 86: "rovesci di neve",
         95: "temporale", 96: "temporale con grandine", 99: "temporale con grandine"}

PAROLE_METEO = ("meteo", "previsioni", "piove", "pioverà", "piovera", "nuvoloso", "soleggiato")
FINE_CITTA = ("oggi", "domani", "adesso", "ora", "stasera", "stamattina", "stamani", "stanotte", "dopodomani", "per")
NON_CITTA = {"che", "come", "quale", "cosa", "il", "lo", "la", "mio", "tuo", "qui", "li", "là", "casa", "te", "me"}


def _riconosci_altra_lingua(frase, lingua):
    """fr, es, en: stessa idea dell'italiano, con le parole di quella lingua."""
    M = lingue.METEO[lingua]
    t = lingue.norm(frase)
    if not t or not re.search(M["chiave"], t):
        return None
    parole = t.split()
    giorno = 1 if M["domani"] in parole else 0
    citta = None
    m = re.search(M["prep"], t)
    if m:
        pezzi = []
        for p in m.group(1).split():
            if p in M["fine"]:
                break
            pezzi.append(p)
        while pezzi and pezzi[0] in M["non_citta"]:
            pezzi.pop(0)
        if pezzi and not (len(pezzi) == 1 and pezzi[0] in M["non_citta"]):
            citta = " ".join(w.capitalize() for w in pezzi)
    return {"citta": citta, "giorno": giorno}


def riconosci(frase):
    """Se la frase chiede il meteo restituisce {"citta", "giorno"}, altrimenti None."""
    if lingue.corrente != "it":
        return _riconosci_altra_lingua(frase, lingue.corrente)
    t = re.sub(r"[^\w\s'àèéìòù]", " ", frase.lower()).replace("'", " ")
    parole = t.split()
    if not parole:
        return None
    meteo = any(p in t for p in PAROLE_METEO) or re.search(r"\b(che )?tempo (fa|farà|fara|c e)\b", t) \
        or re.search(r"\bcom e il tempo\b", t) or re.search(r"\bcome e il tempo\b", t)
    if not meteo:
        return None
    giorno = 1 if "domani" in parole else 0
    citta = None
    m = re.search(r"\b(?:a|di|per|su|in|da)\s+([a-zàèéìòù]+(?:\s+[a-zàèéìòù]+){0,3})", t)
    if m:
        pezzi = []
        for p in m.group(1).split():
            if p in FINE_CITTA:
                break
            pezzi.append(p)
        while pezzi and pezzi[0] in NON_CITTA:
            pezzi.pop(0)
        if pezzi and not (len(pezzi) == 1 and pezzi[0] in NON_CITTA):
            citta = " ".join(w.capitalize() for w in pezzi)
    return {"citta": citta, "giorno": giorno}


def _leggi_impostazioni():
    try:
        d = json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def citta_salvata():
    c = _leggi_impostazioni().get("citta_meteo")
    return c if isinstance(c, str) and c.strip() else None


def salva_citta(citta):
    """Salva la città predefinita senza toccare le altre impostazioni (es. la posizione della sfera)."""
    d = _leggi_impostazioni()
    d["citta_meteo"] = citta
    try:
        FILE_IMPOSTAZIONI.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        _log.exception("Non riesco a salvare la città del meteo in impostazioni_eva.json")
        return False


def _json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Eva-assistente"}),
                                timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def cerca(citta, giorno=0):
    """Cerca le previsioni. Restituisce (stato, dati). La lingua è quella di Eva (nomi dei luoghi e del cielo)."""
    lingua = lingue.corrente
    try:
        g = _json("https://geocoding-api.open-meteo.com/v1/search?" +
                  urllib.parse.urlencode({"name": citta, "count": 1, "language": lingua, "format": "json"}))
        if not g.get("results"):
            return "non_trovata", None
        luogo = g["results"][0]
        p = _json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": luogo["latitude"], "longitude": luogo["longitude"], "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,weather_code,precipitation,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum"}))
        d = p["daily"]
        i = 1 if giorno == 1 else 0
        dati = {"citta": luogo.get("name", citta), "giorno": "domani" if i else "oggi",
                "cielo": _cielo(d["weather_code"][i], lingua),
                "temp_max": round(d["temperature_2m_max"][i]), "temp_min": round(d["temperature_2m_min"][i]),
                "prob_pioggia": d["precipitation_probability_max"][i], "mm_pioggia": d["precipitation_sum"][i]}
        if not i:
            c = p["current"]
            dati["temp_ora"] = round(c["temperature_2m"])
            dati["cielo"] = _cielo(c["weather_code"], lingua) or dati["cielo"]
            dati["vento_kmh"] = round(c["wind_speed_10m"])
        return "ok", dati
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        _log.exception("Meteo: internet non risponde")
        return "offline", None
    except Exception:
        _log.exception("Meteo: errore nel leggere le previsioni")
        return "errore", None


def _cielo(codice, lingua):
    if lingua == "it":
        return CIELO.get(int(codice), "variabile")
    return lingue.cielo(codice, lingua) or "—"


def frase_locale(dati):
    """Frase di ripiego, composta dal codice (se Groq non risponde)."""
    if lingue.corrente != "it":
        return lingue.frase_meteo_locale(dati)
    quando = "oggi" if dati["giorno"] == "oggi" else "domani"
    s = f"A {dati['citta']} {quando}: {dati['cielo']}, "
    if "temp_ora" in dati:
        s += f"adesso {dati['temp_ora']} gradi, massima {dati['temp_max']}. "
    else:
        s += f"tra {dati['temp_min']} e {dati['temp_max']} gradi. "
    pr = dati.get("prob_pioggia")
    s += "Pioggia improbabile." if pr is None or pr < 20 else f"Probabilità di pioggia {pr} per cento."
    return s


def frase_per_voce(dati, modello_groq=None):
    """Passa i dati a Groq, che li dice in una frase breve. Se non risponde, usa la frase locale."""
    if modello_groq is not None:
        try:
            testo = modello_groq(lingue.METEO_ISTRUZIONE[lingue.corrente] + json.dumps(dati, ensure_ascii=False))
            if testo:
                return testo
        except Exception:
            _log.exception("Meteo: Groq non ha risposto, uso la frase del codice")
    return frase_locale(dati)
