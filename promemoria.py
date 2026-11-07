"""Promemoria a voce di Eva (italiano, francese, spagnolo, inglese).

  "ricordami di chiamare Jorge alle cinque"        "remind me in ten minutes to turn off the oven"
  "ricordami tra dieci minuti di spegnere il forno" "rappelle-moi demain à neuf heures d'acheter du pain"
  "ricordami domani alle nove di comprare il pane"  "recuérdame mañana a las nueve comprar pan"

Eva capisce COSA ricordare e QUANDO, lo ripete a voce e lo salva in promemoria.json (accanto al programma): i
promemoria restano anche se il PC si spegne. All'ora giusta Eva avvisa a voce ("<nome>, ti ricordo di chiamare Jorge") e
lo mostra sulla sfera; se era spenta, avvisa appena si riaccende. Se l'ora o il giorno non sono chiari, CHIEDE
(non indovina). Comandi: "che promemoria ho?" e "cancella il promemoria" (con conferma a voce).

Questo modulo NON parla e non apre niente: riconosce, calcola le date e legge/scrive promemoria.json. A parlare e a
mostrare la sfera pensa vox_core.py.
"""
import datetime as dt
import json
import os
import re
import threading
import unicodedata
import uuid
from pathlib import Path

import vox_lingue as lingue

FILE = Path(__file__).with_name("promemoria.json")
_blocco = threading.RLock()
LINGUE = ("it", "fr", "es", "en")


# ======================================================================
# Testo "piegato": minuscolo, senza accenti né punteggiatura, STESSA LUNGHEZZA dell'originale
# (così le posizioni trovate nel testo piegato valgono anche nel testo originale, con nomi e accenti)
# ======================================================================
def piega(testo):
    out = []
    for ch in testo:
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn") or " "
        base = base.lower()
        if len(base) != 1:
            base = " "
        out.append(base if (base.isalnum() or base == ":") else " ")
    return "".join(out)


# ======================================================================
# Numeri a parole
# ======================================================================
_UNITA = {
    "it": "zero uno due tre quattro cinque sei sette otto nove".split(),
    "fr": "zero un deux trois quatre cinq six sept huit neuf".split(),
    "es": "cero uno dos tres cuatro cinco seis siete ocho nueve".split(),
    "en": "zero one two three four five six seven eight nine".split(),
}
_TEENS = {
    "it": "dieci undici dodici tredici quattordici quindici sedici diciassette diciotto diciannove".split(),
    "fr": ["dix", "onze", "douze", "treize", "quatorze", "quinze", "seize", "dix sept", "dix huit", "dix neuf"],
    "es": "diez once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve".split(),
    "en": "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
}
_DECINE = {
    "it": {"venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50, "sessanta": 60, "settanta": 70, "ottanta": 80, "novanta": 90},
    "fr": {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60},
    "es": {"veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90},
    "en": {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90},
}
_EXTRA = {"it": {"un": 1, "una": 1}, "fr": {"une": 1}, "es": {"un": 1, "una": 1},
          "en": {"a": 1, "an": 1}}


def _tabella_numeri(lg):
    t = {}
    for i, w in enumerate(_UNITA[lg]):
        t[w] = i
    for i, w in enumerate(_TEENS[lg]):
        t[w] = 10 + i
    for w, v in _DECINE[lg].items():
        t[w] = v
        for u in range(1, 10):
            uw = _UNITA[lg][u]
            if lg == "it":
                base = w[:-1] if u in (1, 8) else w
                t[base + uw] = v + u
            elif lg == "fr":
                if u == 1:
                    t[w + " et un"] = v + u
                if v < 70:
                    t[w + " " + uw] = v + u
            elif lg == "es":
                if v == 20:
                    t["veinti" + uw] = v + u
                else:
                    t[w + " y " + uw] = v + u
            else:
                t[w + " " + uw] = v + u
    t["veintidos"] = 22  # "veintidós" piegato
    return t


_NUMERI = {lg: _tabella_numeri(lg) for lg in LINGUE}
_NUM_RE = {}
for _lg in LINGUE:
    _alt = sorted((p for p in _NUMERI[_lg] if p not in ("un", "una", "une", "a", "an")), key=len, reverse=True)
    _NUM_RE[_lg] = r"(?:\d{1,3}|" + "|".join(re.escape(p) for p in _alt) + ")"


def valore(token, lg):
    token = re.sub(r"\s+", " ", token.strip())
    if token.isdigit():
        return int(token)
    return _NUMERI[lg].get(token)


# ======================================================================
# Grammatica di ogni lingua (sul testo piegato)
# ======================================================================
_UN = {"it": r"(?:un|uno|una)", "fr": r"(?:un|une)", "es": r"(?:un|uno|una)", "en": r"(?:a|an|one)"}
CONFIG = {
    "it": {
        "trigger": r"\b(?:ricordami|ricordarmi|ricorda mi|fammi ricordare|(?:metti|imposta|crea|aggiungi|fai|puoi mettere)\s+(?:un\s+)?promemoria)\b",
        "nome": r"\bpromemoria\b",
        "rel_pref": r"tra|fra|dopo",
        "rel_unita": [(r"second[oi]", 1), (r"minut[oi]", 60), (r"or[ae]", 3600), (r"giorn[oi]", 86400), (r"settiman[ae]", 604800)],
        "rel_speciali": [(r"mezz ora|mezza ora", 1800), (r"un quarto d ora|quarto d ora", 900), (r"un ora e mezza|un ora e mezzo", 5400)],
        "abs_pref": r"(?:alle ore|alle|all|per le|verso le|entro le)",
        "ocl": r"(?:ore|ora)",
        "sep": r"e",
        "frac": [(r"mezza|mezzo", 30), (r"un quarto|quarto", 15), (r"tre quarti", 45)],
        "mezzogiorno": r"mezzogiorno", "mezzanotte": r"mezzanotte",
        "am": [r"(?:di|del|la|nella|dalla)\s+mattin[ao]|stamattina|stamani", r"\ba\s*m\b"],
        "pm": [r"(?:del|di|nel|il)\s+pomeriggio|(?:di|della|alla)\s+sera|stasera|\bp\s*m\b"],
        "notte": [r"(?:di|della)\s+notte|stanotte"],
        "giorni": [(r"dopodomani|dopo domani", ("off", 2)), (r"domani", ("off", 1)), (r"oggi|stasera|stamattina|stamani|stanotte", ("off", 0))],
        "settimana": {"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4, "sabato": 5, "domenica": 6},
        "connettivi": r"^(?:di|a|ad|per|da|d)\s+",
        "riempitivi_fine": r"\s+(?:per favore|per piacere|grazie)$",
    },
    "fr": {
        "trigger": r"\b(?:rappelle moi|rappelez moi|rappeler moi|me rappeler|fais moi penser|faites moi penser|(?:mets|ajoute|cree|programme|peux tu mettre)\s+(?:un\s+)?rappel)\b",
        "nome": r"\brappels?\b",
        "rel_pref": r"dans|d ici|apres",
        "rel_unita": [(r"secondes?", 1), (r"minutes?", 60), (r"heures?", 3600), (r"jours?", 86400), (r"semaines?", 604800)],
        "rel_speciali": [(r"une demi heure|demi heure", 1800), (r"un quart d heure", 900), (r"une heure et demie|une heure et demi", 5400)],
        "abs_pref": r"(?:a|vers|pour)",
        "ocl": r"(?:heures?|h)",
        "sep": r"et",
        "frac": [(r"demie|demi", 30), (r"quart", 15)],
        "mezzogiorno": r"midi", "mezzanotte": r"minuit",
        "am": [r"du matin|(?:ce|le) matin", r"\ba\s*m\b"],
        "pm": [r"de l apres midi|cet apres midi|du soir|ce soir|\bp\s*m\b"],
        "notte": [r"de la nuit|cette nuit"],
        "giorni": [(r"apres demain", ("off", 2)), (r"demain", ("off", 1)), (r"aujourd hui|ce soir|ce matin|cette nuit", ("off", 0))],
        "settimana": {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6},
        "connettivi": r"^(?:de|d|a|pour)\s+",
        "riempitivi_fine": r"\s+(?:s il te plait|s il vous plait|merci)$",
    },
    "es": {
        "trigger": r"\b(?:recuerdame|recuerda me|recordarme|acuerdame|(?:ponme|pon|anade|crea|programa|puedes poner)\s+(?:un\s+)?recordatorio)\b",
        "nome": r"\brecordatorios?\b",
        "rel_pref": r"en|dentro de|despues de",
        "rel_unita": [(r"segundos?", 1), (r"minutos?", 60), (r"horas?", 3600), (r"dias?", 86400), (r"semanas?", 604800)],
        "rel_speciali": [(r"media hora", 1800), (r"un cuarto de hora", 900), (r"una hora y media", 5400)],
        "abs_pref": r"(?:a las|a la|sobre las|sobre la)",
        "ocl": r"(?:horas?|h)",
        "sep": r"y",
        "frac": [(r"media", 30), (r"cuarto", 15)],
        "mezzogiorno": r"mediodia", "mezzanotte": r"medianoche",
        "am": [r"(?:de|por|esta)\s+la\s+manana|esta manana", r"\ba\s*m\b"],
        "pm": [r"(?:de|por|esta)\s+la\s+tarde|esta tarde|\bp\s*m\b"],
        "notte": [r"(?:de|por|esta)\s+la\s+noche|esta noche"],
        "giorni": [(r"pasado manana", ("off", 2)), (r"manana", ("off", 1)), (r"hoy|esta noche|esta tarde|esta manana", ("off", 0))],
        "settimana": {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6},
        "connettivi": r"^(?:de|a|para)\s+",
        "riempitivi_fine": r"\s+(?:por favor|gracias)$",
    },
    "en": {
        "trigger": r"\b(?:remind me|(?:set|add|create|make|put)\s+(?:a\s+|me a\s+)?reminder)\b",
        "nome": r"\breminders?\b",
        "rel_pref": r"in|after|within",
        "rel_unita": [(r"seconds?|secs?", 1), (r"minutes?|mins?", 60), (r"hours?|hrs?", 3600), (r"days?", 86400), (r"weeks?", 604800)],
        "rel_speciali": [(r"half an hour|half hour|a half hour", 1800), (r"a quarter of an hour|quarter of an hour|quarter hour", 900),
                         (r"an hour and a half|one and a half hours", 5400)],
        "abs_pref": r"(?:at|around|by)",
        "ocl": r"(?:o clock|oclock)",
        "sep": r"and",
        "frac": [(r"half", 30), (r"quarter", 15)],
        "mezzogiorno": r"noon", "mezzanotte": r"midnight",
        "am": [r"in the morning|this morning|\ba\s*m\b"],
        "pm": [r"in the afternoon|this afternoon|in the evening|this evening|tonight|\bp\s*m\b"],
        "notte": [r"at night"],
        "giorni": [(r"the day after tomorrow|day after tomorrow", ("off", 2)), (r"tomorrow", ("off", 1)), (r"today|tonight|this evening|this morning|this afternoon", ("off", 0))],
        "settimana": {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6},
        "connettivi": r"^(?:to|about|of)\s+",
        "riempitivi_fine": r"\s+(?:please|thanks|thank you)$",
    },
}
# parole di comandi per elenco e cancellazione
VERBI_CANCELLA = {
    "it": r"cancella|cancellare|elimina|eliminare|togli|togliere|annulla|annullare|rimuovi|rimuovere|svuota",
    "fr": r"supprime|supprimer|annule|annuler|efface|effacer|enleve|enlever|retire|retirer",
    "es": r"borra|borrar|cancela|cancelar|elimina|eliminar|quita|quitar|anula|anular",
    "en": r"delete|cancel|remove|clear|erase|get rid of",
}
VERBI_ELENCO = {
    "it": r"che|quali|quanti|leggi|leggimi|elenca|elencami|dimmi|ho|mostra|mostrami|ascoltare|sentire|lista",
    "fr": r"quels|quel|quelles|lis|lis moi|liste|dis moi|ai|mes|montre|montre moi|combien",
    "es": r"que|cuales|cuantos|lee|leeme|lista|dime|tengo|muestra|muestrame|mis",
    "en": r"what|which|how many|read|list|tell me|have|show|my",
}
TUTTI = {"it": r"tutti|tutte|ogni", "fr": r"tous|toutes", "es": r"todos|todas", "en": r"all|every"}
ORDINALI = {
    "it": {"primo": 1, "prima": 1, "secondo": 2, "seconda": 2, "terzo": 3, "terza": 3, "quarto": 4, "quarta": 4, "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1},
    "fr": {"premier": 1, "premiere": 1, "deuxieme": 2, "second": 2, "seconde": 2, "troisieme": 3, "quatrieme": 4, "cinquieme": 5, "dernier": -1, "derniere": -1},
    "es": {"primero": 1, "primer": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercer": 3, "tercera": 3, "cuarto": 4, "quinto": 5, "ultimo": -1, "ultima": -1},
    "en": {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1},
}


# ======================================================================
# Riconoscere il tipo di frase
# ======================================================================
def riconosci(testo):
    """Restituisce ("crea", lingua) | ("elenca", lingua) | ("cancella", lingua) oppure None."""
    f = piega(testo)
    for lg in LINGUE:
        c = CONFIG[lg]
        if re.search(c["nome"], f):
            if re.search(r"\b(?:" + VERBI_CANCELLA[lg] + r")\b", f):
                return "cancella", lg
            if re.search(r"\b(?:" + VERBI_ELENCO[lg] + r")\b", f) and not re.search(c["trigger"], f):
                return "elenca", lg
        if re.search(c["trigger"], f):
            return "crea", lg
    return None


# ======================================================================
# Capire QUANDO e COSA
# ======================================================================
class Analisi:
    def __init__(self):
        self.rel = None          # secondi da adesso
        self.ora = None          # (ora, minuti)
        self.hint = None         # "am" | "pm" | "notte"
        self.giorno = None       # ("off", n) | ("wd", n)
        self.cosa = ""
        self.lingua = "it"


def analizza(testo, lg, solo_quando=False):
    """Analizza `testo` (nella lingua lg): quando e cosa. Con solo_quando=True non cerca il "cosa"."""
    c = CONFIG[lg]
    orig = testo
    w = piega(testo)
    rimossi = [False] * len(w)
    a = Analisi()
    a.lingua = lg
    NUM = _NUM_RE[lg]

    def prendi(regex, flags=0):
        nonlocal w
        m = re.search(regex, w, flags)
        if m:
            for i in range(m.start(), m.end()):
                rimossi[i] = True
            w = w[:m.start()] + " " * (m.end() - m.start()) + w[m.end():]
        return m

    # il comando ("ricordami ...")
    prendi(c["trigger"])
    # pezzi della giornata ("di mattina", "stasera"...): danno un'indicazione am/pm e a volte il giorno
    for rx in c["am"]:
        if prendi(rx):
            a.hint = "am"
    for rx in c["pm"]:
        if prendi(rx):
            a.hint = "pm"
    for rx in c["notte"]:
        if prendi(rx):
            a.hint = "notte"
    # relativo: "tra dieci minuti", "tra mezz'ora"
    for rx, sec in c["rel_speciali"]:
        if prendi(r"\b(?:" + c["rel_pref"] + r")\s+(?:" + rx + r")\b"):
            a.rel = sec
            break
    if a.rel is None:
        unita = "|".join(f"(?P<u{i}>{rx})" for i, (rx, _) in enumerate(c["rel_unita"]))
        m = prendi(r"\b(?:" + c["rel_pref"] + r")\s+(?:(?P<n>" + NUM + r")|(?P<un>" + _UN[lg] + r"))\s*(?:" + unita + r")\b")
        if m:
            n = valore(m.group("n"), lg) if m.group("n") else 1
            for i, (_, sec) in enumerate(c["rel_unita"]):
                if m.group(f"u{i}"):
                    a.rel = n * sec
                    break
    # ora del giorno
    if a.rel is None:
        frac = "|".join(f"(?P<f{i}>{rx})" for i, (rx, _) in enumerate(c["frac"]))
        m = prendi(r"(?:\b(?:" + c["abs_pref"] + r")\s+)?(?<![\d:])(?P<h>\d{1,2})\s*[:h]\s*(?P<m>\d{2})(?!\d)")
        m2 = prendi(r"\b(?:at\s+)?(?P<f>half|quarter)\s+(?P<dir>past|to)\s+(?P<h>" + NUM + r")\b") if (lg == "en" and not m) else None
        if m:
            a.ora = (int(m.group("h")), int(m.group("m")))
        elif m2:
            h = valore(m2.group("h"), lg)
            if h is not None and 0 <= h <= 24:
                a.ora = ((h % 24, 30) if m2.group("f") == "half" else
                         ((h % 24, 15) if m2.group("dir") == "past" else ((h - 1) % 24, 45)))
        else:
            if lg == "fr":
                rx = (r"\b(?:" + c["abs_pref"] + r")\s+(?P<h>" + NUM + r")\s*(?:" + c["ocl"] + r")\b(?:\s*(?:" + c["sep"] + r"\s+)?(?:(?P<m>" + NUM + r")|" + frac + r"))?")
            else:
                rx = (r"\b(?:" + c["abs_pref"] + r")\s+(?P<h>" + NUM + r")(?:\s+(?:" + c["ocl"] + r"))?(?:\s+(?:(?:" + c["sep"] + r")\s+)?(?:(?P<m>" + NUM + r")|" + frac + r"))?\b")
            m = prendi(rx)
            if m:
                h = valore(m.group("h"), lg)
                mi = 0
                if m.group("m"):
                    mi = valore(m.group("m"), lg) or 0
                else:
                    for i, (_, mm) in enumerate(c["frac"]):
                        if m.group(f"f{i}"):
                            mi = mm
                if h is not None and 0 <= h <= 24 and 0 <= mi <= 59:
                    a.ora = (h % 24, mi)
            else:
                m = prendi(r"\b(?:(?:" + c["abs_pref"] + r")\s+)?(?:" + c["mezzogiorno"] + r")\b")
                if m:
                    a.ora = (12, 0)
                else:
                    m = prendi(r"\b(?:(?:" + c["abs_pref"] + r")\s+)?(?:" + c["mezzanotte"] + r")\b")
                    if m:
                        a.ora = (0, 0)
        # giorno
        for rx, val in c["giorni"]:
            if prendi(r"\b(?:" + rx + r")\b"):
                a.giorno = val
                break
        if a.giorno is None:
            for nome, n in c["settimana"].items():
                if prendi(r"(?:\b(?:el|le|il|on|next|the|este|ce|prossimo|prochain|proximo|de)\s+)?\b" + nome + r"\b"):
                    a.giorno = ("wd", n)
                    break
    else:
        pass
    # il COSA: quello che resta del testo originale (con nomi e accenti), senza il comando e le parole del quando
    if not solo_quando:
        resto = "".join(" " if rimossi[i] else orig[i] for i in range(len(orig)))
        resto = re.sub(r"\s+", " ", resto).strip(" ,.;:!?-")
        for _ in range(3):                      # via "di", "che", "to", "de"... davanti (stessa lunghezza nel testo piegato)
            m = re.match(c["connettivi"], piega(resto))
            if not m:
                break
            resto = resto[m.end():].strip()
        resto = re.sub(c["riempitivi_fine"], "", resto, flags=re.I).strip(" ,.;:!?-")
        a.cosa = resto
    return a


# ======================================================================
# Calcolare la data
# ======================================================================
def risolvi(a, adesso):
    """-> ("ok", datetime) | ("chiedi_ampm", (ora, minuti)) | ("chiedi_giorno", (ora, minuti)) | ("manca", None)."""
    if a.rel is not None:
        return "ok", (adesso + dt.timedelta(seconds=a.rel)).replace(microsecond=0)
    if a.ora is None:
        if a.giorno is not None and a.giorno[0] == "off":
            return "manca", None
        return "manca", None
    h, m = a.ora
    giorno = a.giorno

    def data_del_giorno():
        oggi = adesso.date()
        if giorno is None:
            return None
        if giorno[0] == "off":
            return oggi + dt.timedelta(days=giorno[1])
        d = (giorno[1] - adesso.weekday()) % 7
        return oggi + dt.timedelta(days=d)

    cand_ore = []
    if h >= 13 or h == 0 and a.ora != (12, 0):
        cand_ore = [h]
    elif a.hint == "am":
        cand_ore = [0 if h == 12 else h]
    elif a.hint == "pm":
        cand_ore = [h if h == 12 else h + 12]
    elif a.hint == "notte":
        cand_ore = [h + 12 if 6 <= h <= 11 else (0 if h == 12 else h)]
    elif h == 12:
        cand_ore = [12]
    else:
        cand_ore = None                                  # 1..11 senza indicazioni: ambiguo mattina/pomeriggio
    d = data_del_giorno()
    if cand_ore is not None:
        ore = cand_ore[0]
        if d is not None:
            q = dt.datetime.combine(d, dt.time(ore, m))
            if q <= adesso:
                if giorno[0] == "wd":
                    q += dt.timedelta(days=7)               # "lunedì alle 10" già passato oggi: lunedì prossimo
                else:
                    return "chiedi_giorno", (ore, m)
            return "ok", q
        quando = dt.datetime.combine(adesso.date(), dt.time(ore, m))
        if quando > adesso:
            return "ok", quando
        return "chiedi_giorno", (ore, m)
    # ambiguo
    if d is not None:                                    # giorno dato: 7-11 mattina, 1-6 pomeriggio
        ore = h if h >= 7 else h + 12
        q = dt.datetime.combine(d, dt.time(ore, m))
        if q <= adesso:
            if giorno[0] == "wd":
                q += dt.timedelta(days=7)
            else:
                return "chiedi_giorno", (ore, m)
        return "ok", q
    futuri = [dt.datetime.combine(adesso.date(), dt.time(o, m)) for o in (h, h + 12)]
    futuri = [q for q in futuri if q > adesso]
    if len(futuri) == 1:
        return "ok", futuri[0]
    return "chiedi_ampm", (h, m)


def con_ampm(ora_min, parola_ampm, adesso, giorno=None):
    """Dopo la domanda "mattina o pomeriggio?": calcola la data."""
    h, m = ora_min
    ore = (0 if h == 12 else h) if parola_ampm == "am" else (h if h == 12 else h + 12)
    base = adesso.date()
    q = dt.datetime.combine(base, dt.time(ore, m))
    if q <= adesso:
        q += dt.timedelta(days=1)
    return q


def riconosci_ampm(testo, lg):
    f = piega(testo)
    c = CONFIG[lg]
    for rx in c["am"] + [r"\bmatin\w*|\bmanana\b|\bmorning\b|\bmattin\w*"]:
        if re.search(rx, f):
            return "am"
    for rx in c["pm"] + [r"\bpomeriggio\b|\bsera\b|\bsoir\b|\btarde\b|\bnoche\b|\bafternoon\b|\bevening\b|\bnight\b|\bnotte\b"]:
        if re.search(rx, f):
            return "pm"
    return None


# ======================================================================
# Archivio (promemoria.json)
# ======================================================================
def _leggi():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _scrivi(lista):
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)


def elenco():
    with _blocco:
        return sorted(_leggi(), key=lambda p: p["quando"])


def aggiungi(cosa, quando, lingua):
    with _blocco:
        lista = _leggi()
        p = {"id": uuid.uuid4().hex[:8], "cosa": cosa, "quando": quando.strftime("%Y-%m-%dT%H:%M:%S"),
             "creato": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "lingua": lingua}
        lista.append(p)
        _scrivi(lista)
        return p


def rimuovi(id_):
    with _blocco:
        lista = [p for p in _leggi() if p["id"] != id_]
        _scrivi(lista)


def rimuovi_tutti():
    with _blocco:
        _scrivi([])


def prossimo_scaduto(adesso=None):
    adesso = adesso or dt.datetime.now()
    with _blocco:
        for p in sorted(_leggi(), key=lambda x: x["quando"]):
            if dt.datetime.fromisoformat(p["quando"]) <= adesso:
                return p
    return None


def quando_di(p):
    return dt.datetime.fromisoformat(p["quando"])


# ======================================================================
# Frasi (da dire)
# ======================================================================
TX = {
    "it": {
        "conferma": "Va bene: ti ricorderò {cosa} {quando}.",
        "chiedi_cosa": "Cosa devo ricordarti?",
        "chiedi_quando": "Quando devo ricordartelo?",
        "chiedi_ampm": "Alle {ora}: del mattino o del pomeriggio?",
        "chiedi_giorno": "Le {ora} di oggi sono già passate. Intendi domani?",
        "non_capito_quando": "Non ho capito quando. Riprova, per esempio: ricordami domani alle nove di comprare il pane.",
        "annullato": "Va bene, lascio perdere.",
        "annuncio": "{nome}, ti ricordo {cosa}.",
        "annuncio_ritardo": "{nome}, avevi un promemoria {quando}: ti ricordo {cosa}.",
        "nessuno": "Non hai nessun promemoria.",
        "elenco_intro": "Hai {n} promemoria.",
        "elenco_uno": "Hai un promemoria.",
        "elenco_voce": "{i}: {cosa}, {quando}.",
        "cancella_quale": "Quale vuoi cancellare? {elenco} Dimmi il numero, oppure tutti.",
        "cancella_conferma": "Vuoi davvero cancellare il promemoria: {cosa}, {quando}? Dì sì per confermare.",
        "cancella_tutti": "Vuoi davvero cancellare tutti i {n} promemoria? Dì sì per confermare.",
        "cancellato": "Promemoria cancellato.",
        "cancellati": "Ho cancellato tutti i promemoria.",
        "mantenuto": "Va bene, non cancello niente.",
        "non_capito": "Non ho capito quale. Non cancello niente.",
        "oggi": "oggi", "domani": "domani", "dopodomani": "dopodomani", "tra": "tra",
        "giorni": ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"],
        "unita": {"s": ("secondo", "secondi"), "m": ("minuto", "minuti"), "h": ("ora", "ore")},
    },
    "fr": {
        "conferma": "D'accord : je te rappellerai {cosa} {quando}.",
        "chiedi_cosa": "Que dois-je te rappeler ?",
        "chiedi_quando": "Quand dois-je te le rappeler ?",
        "chiedi_ampm": "À {ora} : du matin ou de l'après-midi ?",
        "chiedi_giorno": "{ora} aujourd'hui, c'est déjà passé. Tu veux dire demain ?",
        "non_capito_quando": "Je n'ai pas compris quand. Réessaie, par exemple : rappelle-moi demain à neuf heures d'acheter du pain.",
        "annullato": "D'accord, je laisse tomber.",
        "annuncio": "{nome}, je te rappelle {cosa}.",
        "annuncio_ritardo": "{nome}, tu avais un rappel {quando} : je te rappelle {cosa}.",
        "nessuno": "Tu n'as aucun rappel.",
        "elenco_intro": "Tu as {n} rappels.",
        "elenco_uno": "Tu as un rappel.",
        "elenco_voce": "{i} : {cosa}, {quando}.",
        "cancella_quale": "Lequel veux-tu supprimer ? {elenco} Dis le numéro, ou tous.",
        "cancella_conferma": "Veux-tu vraiment supprimer le rappel : {cosa}, {quando} ? Dis oui pour confirmer.",
        "cancella_tutti": "Veux-tu vraiment supprimer les {n} rappels ? Dis oui pour confirmer.",
        "cancellato": "Rappel supprimé.",
        "cancellati": "J'ai supprimé tous les rappels.",
        "mantenuto": "D'accord, je ne supprime rien.",
        "non_capito": "Je n'ai pas compris lequel. Je ne supprime rien.",
        "oggi": "aujourd'hui", "domani": "demain", "dopodomani": "après-demain", "tra": "dans",
        "giorni": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
        "unita": {"s": ("seconde", "secondes"), "m": ("minute", "minutes"), "h": ("heure", "heures")},
    },
    "es": {
        "conferma": "De acuerdo: te recordaré {cosa} {quando}.",
        "chiedi_cosa": "¿Qué debo recordarte?",
        "chiedi_quando": "¿Cuándo debo recordártelo?",
        "chiedi_ampm": "A las {ora}: ¿de la mañana o de la tarde?",
        "chiedi_giorno": "Las {ora} de hoy ya han pasado. ¿Te refieres a mañana?",
        "non_capito_quando": "No he entendido cuándo. Inténtalo de nuevo, por ejemplo: recuérdame mañana a las nueve comprar pan.",
        "annullato": "De acuerdo, lo dejo.",
        "annuncio": "{nome}, te recuerdo {cosa}.",
        "annuncio_ritardo": "{nome}, tenías un recordatorio {quando}: te recuerdo {cosa}.",
        "nessuno": "No tienes ningún recordatorio.",
        "elenco_intro": "Tienes {n} recordatorios.",
        "elenco_uno": "Tienes un recordatorio.",
        "elenco_voce": "{i}: {cosa}, {quando}.",
        "cancella_quale": "¿Cuál quieres borrar? {elenco} Dime el número, o todos.",
        "cancella_conferma": "¿De verdad quieres borrar el recordatorio: {cosa}, {quando}? Di sí para confirmar.",
        "cancella_tutti": "¿De verdad quieres borrar los {n} recordatorios? Di sí para confirmar.",
        "cancellato": "Recordatorio borrado.",
        "cancellati": "He borrado todos los recordatorios.",
        "mantenuto": "De acuerdo, no borro nada.",
        "non_capito": "No he entendido cuál. No borro nada.",
        "oggi": "hoy", "domani": "mañana", "dopodomani": "pasado mañana", "tra": "en",
        "giorni": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
        "unita": {"s": ("segundo", "segundos"), "m": ("minuto", "minutos"), "h": ("hora", "horas")},
    },
    "en": {
        "conferma": "Alright: I'll remind you {cosa} {quando}.",
        "chiedi_cosa": "What should I remind you about?",
        "chiedi_quando": "When should I remind you?",
        "chiedi_ampm": "At {ora}: in the morning or in the afternoon?",
        "chiedi_giorno": "{ora} today has already passed. Do you mean tomorrow?",
        "non_capito_quando": "I didn't understand when. Try again, for example: remind me tomorrow at nine to buy bread.",
        "annullato": "Alright, never mind.",
        "annuncio": "{nome}, I'm reminding you {cosa}.",
        "annuncio_ritardo": "{nome}, you had a reminder {quando}: I'm reminding you {cosa}.",
        "nessuno": "You have no reminders.",
        "elenco_intro": "You have {n} reminders.",
        "elenco_uno": "You have one reminder.",
        "elenco_voce": "{i}: {cosa}, {quando}.",
        "cancella_quale": "Which one do you want to delete? {elenco} Tell me the number, or all.",
        "cancella_conferma": "Do you really want to delete the reminder: {cosa}, {quando}? Say yes to confirm.",
        "cancella_tutti": "Do you really want to delete all {n} reminders? Say yes to confirm.",
        "cancellato": "Reminder deleted.",
        "cancellati": "I deleted all the reminders.",
        "mantenuto": "Alright, I'm not deleting anything.",
        "non_capito": "I didn't understand which one. I'm not deleting anything.",
        "oggi": "today", "domani": "tomorrow", "dopodomani": "the day after tomorrow", "tra": "in",
        "giorni": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "unita": {"s": ("second", "seconds"), "m": ("minute", "minutes"), "h": ("hour", "hours")},
    },
}


# Esempi per "cosa sai fare?" (aiuto.py): aggiungine qui quando aggiungi un tipo di promemoria
ESEMPI = {
    "it": ["ricordami di chiamare Jorge alle cinque", "ricordami tra dieci minuti di spegnere il forno", "che promemoria ho", "cancella il promemoria"],
    "fr": ["rappelle-moi dans dix minutes d'éteindre le four", "rappelle-moi demain à neuf heures d'acheter du pain", "quels rappels ai-je", "supprime le rappel"],
    "es": ["recuérdame mañana a las nueve comprar pan", "recuérdame en diez minutos apagar el horno", "qué recordatorios tengo", "borra el recordatorio"],
    "en": ["remind me in ten minutes to turn off the oven", "remind me tomorrow at nine to buy bread", "what reminders do I have", "delete the reminder"],
}


def tx(chiave, lg=None, **campi):
    lg = lg or lingue.corrente
    t = TX[lg][chiave].format(**campi)
    return lingue.pulisci_nome(t) if ("nome" in campi and not campi["nome"]) else t


def _ora_parlata(h, m, lg):
    if lg == "it":
        if (h, m) == (12, 0):
            return "a mezzogiorno"
        if (h, m) == (0, 0):
            return "a mezzanotte"
        if h == 1:
            return "all'una" + (f" e {m}" if m else "")
        return f"alle {h}" + (f" e {m}" if m else "")
    if lg == "fr":
        if (h, m) == (12, 0):
            return "à midi"
        if (h, m) == (0, 0):
            return "à minuit"
        return f"à {h} heure{'s' if h > 1 else ''}" + (f" {m}" if m else "")
    if lg == "es":
        if (h, m) == (12, 0):
            return "a mediodía"
        if (h, m) == (0, 0):
            return "a medianoche"
        return ("a la una" if h == 1 else f"a las {h}") + (f" y {m}" if m else "")
    h12 = h % 12 or 12
    suff = "PM" if h >= 12 else "AM"
    if (h, m) == (12, 0):
        return "at noon"
    if (h, m) == (0, 0):
        return "at midnight"
    return f"at {h12}" + (f":{m:02d}" if m else "") + f" {suff}"


def quando_parlato(quando, adesso=None, relativo=None):
    """"tra 2 minuti" / "oggi alle 17" / "domani alle 9" / "lunedì alle 10 e 30" nella lingua corrente."""
    lg = lingue.corrente
    adesso = adesso or dt.datetime.now()
    T = TX[lg]
    if relativo is not None and relativo < 6 * 3600:
        sec = int(relativo)
        if sec < 120 and sec % 60 != 0:
            n, u = sec, "s"
        elif sec < 3600:
            n, u = round(sec / 60), "m"
        else:
            n, u = round(sec / 3600, 1), "h"
            n = int(n) if float(n).is_integer() else n
        un = T["unita"][u][0] if n == 1 else T["unita"][u][1]
        return f"{T['tra']} {n} {un}"
    giorni = (quando.date() - adesso.date()).days
    if giorni == 0:
        g = T["oggi"]
    elif giorni == 1:
        g = T["domani"]
    elif giorni == 2:
        g = T["dopodomani"]
    else:
        g = f"{T['giorni'][quando.weekday()]} {quando.day}"
    ora = _ora_parlata(quando.hour, quando.minute, lg)
    return f"{g} {ora}" if lg != "en" else f"{g} {ora}"


def _infinito(cosa, lg):
    prima = piega(cosa).split()[0] if cosa.strip() else ""
    if lg == "it":
        return bool(re.search(r"(are|ere|ire|arre|orre|urre|arsi|ersi|irsi)$", prima))
    if lg == "fr":
        return bool(re.search(r"(er|ir|re|oir)$", prima))
    if lg == "es":
        return bool(re.search(r"(ar|er|ir|arse|erse|irse)$", prima))
    return True


def cosa_parlata(cosa, lg=None):
    """Inserisce il "di / de / que / to" giusto: "chiamare Jorge" -> "di chiamare Jorge"."""
    lg = lg or lingue.corrente
    cosa = cosa.strip()
    if not cosa:
        return cosa
    prima = piega(cosa).split()[0]
    if lg == "it":
        return cosa if prima in ("che", "di") else (f"di {cosa}" if _infinito(cosa, lg) else cosa)
    if lg == "fr":
        if prima in ("que", "qu", "de", "d"):
            return cosa
        return (f"d'{cosa}" if prima[0] in "aeiouhy" else f"de {cosa}") if _infinito(cosa, lg) else cosa
    if lg == "es":
        if prima == "que":
            return cosa
        return f"que tienes que {cosa}" if _infinito(cosa, lg) else cosa
    return cosa if prima in ("to", "that", "about") else f"to {cosa}"
