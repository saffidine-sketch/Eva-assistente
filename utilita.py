"""Utilità di Eva (solo italiano): timer, cronometro, sveglia, appunti, calcoli, batteria, musica, luminosità,
Wi-Fi e Bluetooth.

  "imposta un timer di dieci minuti"      "quanto manca al timer"       "annulla il timer"
  "avvia il cronometro"                   "ferma il cronometro"         "quanto segna il cronometro"
  "svegliami alle sette"                  "metti una sveglia domani alle 6 e 30"     "cancella la sveglia"
  (poi, a mano a mano che si aggiungono: appunti, calcoli, batteria, musica, luminosità, Wi-Fi, Bluetooth)

Questo modulo NON parla e non apre finestre: riconosce la frase, fa il lavoro e restituisce il testo da dire
(un oggetto Ut). A dirlo pensa vox_core.py (_ut_gestisci). Timer e sveglia sono promemoria con un "tipo": stanno
in promemoria.json e Eva li annuncia anche se era spenta all'ora giusta.
"""
import datetime as dt
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation, localcontext

import comandi_pc
import eva_percorsi as percorsi
import promemoria
import utilita_pc as pc
from promemoria import piega

LUNGHEZZA_MAX_TIMER = 24 * 3600          # un timer oltre 24 ore è un promemoria: lo dico

# Esempi per "cosa sai fare?" (aiuto.py). Solo italiano: in un'altra lingua il gruppo non compare.
ESEMPI = {
    "it": ["imposta un timer di dieci minuti", "svegliami alle sette", "prendi nota comprare il latte", "quanto fa dodici per otto"],
}


class Ut:
    """Il risultato: cosa dire (testo), di che tipo era (per il registro), e cosa succede dopo.
    stato    = Eva aspetta una risposta ("sì", un numero, un testo): dict {"fase": ...}
    analisi  = una sveglia da completare con il flusso dei promemoria (vox_core._pr_nuovo)"""

    def __init__(self, testo, tipo, stato=None, analisi=None):
        self.testo, self.tipo, self.stato, self.analisi = testo, tipo, stato, analisi


# ======================================================================
# Pezzi comuni: durate e frasi
# ======================================================================
_NUM = promemoria._NUM_RE["it"]
_UNO = r"(?:un|uno|una)"


def _parole(f):
    return set(f.split())


def durata_secondi(f):
    """Quanto dura? "10 minuti", "mezz'ora", "un'ora e mezza", "due ore e 30 minuti", "90 secondi". f = testo piegato.
    Restituisce i secondi, oppure None se non c'è nessuna durata."""
    if re.search(r"\bun ora e mezz[ao]\b", f):
        return 5400
    if re.search(r"\bmezz ora\b|\bmezza ora\b", f):
        return 1800
    if re.search(r"\bun quarto d ora\b|\bquarto d ora\b", f):
        return 900
    totale, trovato = 0, False
    for m in re.finditer(r"\b(?P<n>" + _NUM + r"|" + _UNO + r")\s*(?P<u>second[oi]|minut[oi]|or[ae])\b(?P<coda>\s+e\s+(?:mezz[ao]|un quarto))?", f):
        n = 1 if re.fullmatch(_UNO, m.group("n")) else promemoria.valore(m.group("n"), "it")
        if n is None:
            continue
        u = m.group("u")
        secondi = 1 if u.startswith("second") else (60 if u.startswith("minut") else 3600)
        totale += n * secondi
        if m.group("coda") and secondi > 1:
            totale += secondi // 2 if "mezz" in m.group("coda") else secondi // 4
        trovato = True
    return totale if trovato and totale > 0 else None


def durata_parlata(sec):
    """90 -> "1 minuto e 30 secondi"; 3600 -> "1 ora"; 45 -> "45 secondi"."""
    sec = int(round(sec))
    h, resto = divmod(sec, 3600)
    m, s = divmod(resto, 60)
    pezzi = []
    if h:
        pezzi.append(f"{h} ora" if h == 1 else f"{h} ore")
    if m:
        pezzi.append(f"{m} minuto" if m == 1 else f"{m} minuti")
    if s or not pezzi:
        pezzi.append(f"{s} secondo" if s == 1 else f"{s} secondi")
    return pezzi[0] if len(pezzi) == 1 else ", ".join(pezzi[:-1]) + " e " + pezzi[-1]


def _quando_scade(p, adesso=None):
    adesso = adesso or dt.datetime.now()
    return max(0, (promemoria.quando_di(p) - adesso).total_seconds())


# ======================================================================
# TIMER (un promemoria di tipo "timer")
# ======================================================================
_ANNULLA = r"\b(?:cancella|cancellare|annulla|annullare|ferma|fermare|spegni|spegnere|elimina|togli|togliere|interrompi|stoppa|disattiva)\b"


def _timer_attivi():
    return [p for p in promemoria.elenco() if p.get("tipo") == "timer"]


def _gestisci_timer(f, adesso):
    if not re.search(r"\btimer\b|\bconto alla rovescia\b", f):
        return None
    durata = durata_secondi(f)
    attivi = _timer_attivi()
    if re.search(_ANNULLA, f) and durata is None:
        if not attivi:
            return Ut("Non c'è nessun timer in corso.", "timer: nessuno da annullare")
        p = attivi[0]                                    # il primo a scadere
        promemoria.rimuovi(p["id"])
        resto = len(attivi) - 1
        testo = f"Ho annullato il {p['cosa']}."
        if resto:
            testo += f" Ne restano {resto}." if resto > 1 else " Ne resta uno."
        return Ut(testo, "timer annullato")
    if durata is None:
        if re.search(r"\b(?:quanto|quanti|quanta|resta|restano|rimane|manca|mancano|quale|quali|che|ho|segna)\b", f):
            if not attivi:
                return Ut("Non hai nessun timer in corso.", "timer: elenco vuoto")
            if len(attivi) == 1:
                p = attivi[0]
                return Ut(f"Hai un {p['cosa']}: mancano {durata_parlata(_quando_scade(p, adesso))}.", "timer: elenco")
            parti = [f"{p['cosa']}, mancano {durata_parlata(_quando_scade(p, adesso))}" for p in attivi[:3]]
            return Ut(f"Hai {len(attivi)} timer: " + "; ".join(parti) + ".", "timer: elenco")
        return Ut("Per quanto tempo? Dimmi per esempio dieci minuti.", "timer: chiedo la durata", stato={"fase": "timer_durata"})
    return _crea_timer(durata, adesso)


def _crea_timer(durata, adesso):
    if durata > LUNGHEZZA_MAX_TIMER:
        return Ut("Un timer può durare al massimo 24 ore. Per più tempo usa un promemoria.", "timer: troppo lungo")
    etichetta = f"timer di {durata_parlata(durata)}"
    promemoria.aggiungi(etichetta, adesso + dt.timedelta(seconds=durata), "it", tipo="timer")
    return Ut(f"Timer di {durata_parlata(durata)} avviato.", "timer creato")


# ======================================================================
# CRONOMETRO (solo in memoria: se Eva si chiude, si azzera)
# ======================================================================
_cron = {"t0": None, "fermo": None}


def _cron_trascorso():
    if _cron["t0"] is None:
        return None
    return (_cron["fermo"] if _cron["fermo"] is not None else time.monotonic()) - _cron["t0"]


def _gestisci_cronometro(f):
    if not re.search(r"\bcronometro\b", f):
        return None
    t = _cron_trascorso()
    if re.search(r"\b(?:azzera|azzerare|resetta|reimposta|cancella|annulla)\b", f):
        _cron["t0"] = _cron["fermo"] = None
        return Ut("Cronometro azzerato.", "cronometro azzerato")
    if re.search(r"\b(?:ferma|fermare|stoppa|stop|interrompi|blocca|spegni|basta|termina)\b", f):
        if t is None or _cron["fermo"] is not None:
            return Ut("Il cronometro non è in corso.", "cronometro: non in corso")
        _cron["fermo"] = time.monotonic()
        return Ut(f"Cronometro fermato: {durata_parlata(_cron_trascorso())}.", "cronometro fermato")
    if re.search(r"\b(?:quanto|segna|punto|leggi|dimmi|tempo|vedi|controlla|quanti)\b", f):
        if t is None:
            return Ut("Il cronometro non è stato avviato.", "cronometro: non avviato")
        stato = "è fermo a" if _cron["fermo"] is not None else "segna"
        return Ut(f"Il cronometro {stato} {durata_parlata(t)}.", "cronometro letto")
    # avvia, fai partire, metti, parti, "il cronometro" da solo
    if t is not None and _cron["fermo"] is None:
        return Ut(f"Il cronometro è già in corso: segna {durata_parlata(t)}.", "cronometro: già in corso")
    _cron["t0"], _cron["fermo"] = time.monotonic(), None
    return Ut("Cronometro avviato.", "cronometro avviato")


# ======================================================================
# SVEGLIA (un promemoria di tipo "sveglia", con il flusso dei promemoria per mattina/pomeriggio)
# ======================================================================
_SVEGLIA = (r"\b(?:svegliami|svegliarmi|svegliaci|svegliatemi)\b|"
            r"\b(?:metti|mettere|imposta|impostare|programma|programmare|crea|creare|attiva|attivare|fai|fammi|aggiungi|"
            r"puoi mettere|puoi impostare)\s+(?:una\s+|la\s+|mi una\s+)?(?:nuova\s+)?sveglia\b|"
            r"\bsveglia\s+(?:alle|all|per|tra|fra|domani|dopodomani|stamattina|stasera|di domani|a mezzogiorno)\b")


def _sveglie_attive():
    return [p for p in promemoria.elenco() if p.get("tipo") == "sveglia"]


def _gestisci_sveglia(f, frase, adesso):
    if not re.search(r"\bsveglia\b|\bsvegliami\b|\bsvegliarmi\b|\bsveglie\b", f):
        return None
    attive = _sveglie_attive()
    if re.search(_ANNULLA, f) and re.search(r"\bsveglia\b|\bsveglie\b", f) and not re.search(r"\b(?:svegliami|svegliarmi)\b", f):
        if not attive:
            return Ut("Non c'è nessuna sveglia impostata.", "sveglia: nessuna da annullare")
        if re.search(r"\b(?:tutte|tutti)\b", f):
            for p in attive:
                promemoria.rimuovi(p["id"])
            return Ut("Ho cancellato tutte le sveglie.", "sveglie cancellate")
        p = attive[0]
        promemoria.rimuovi(p["id"])
        quando = promemoria.quando_parlato(promemoria.quando_di(p), adesso)
        testo = f"Ho cancellato la sveglia {quando}."
        if len(attive) > 1:
            testo += f" Ne restano {len(attive) - 1}."
        return Ut(testo, "sveglia cancellata")
    if re.search(_SVEGLIA, f):
        a = promemoria.analizza(frase, "it", solo_quando=True)
        a.tipo, a.cosa = "sveglia", "sveglia"
        return Ut("", "sveglia", analisi=a)
    if re.search(r"\b(?:quando|quale|quali|che|quante|a che ora|quanto)\b", f):
        if not attive:
            return Ut("Non hai nessuna sveglia impostata.", "sveglia: elenco vuoto")
        voci = [promemoria.quando_parlato(promemoria.quando_di(p), adesso) for p in attive[:4]]
        return Ut(("Hai una sveglia " if len(attive) == 1 else f"Hai {len(attive)} sveglie: ") + "; ".join(voci) + ".",
                  "sveglia: elenco")
    return None


def conferma_sveglia(quando, adesso, rel=None):
    return f"Va bene: sveglia {promemoria.quando_parlato(quando, adesso, rel)}."


# ======================================================================
# APPUNTI (appunti.json: restano anche se il PC si spegne; sono dati PERSONALI, mai nel repository)
# ======================================================================
FILE_APPUNTI = percorsi.dati("appunti.json")
_blocco_appunti = threading.RLock()
LIMITE_LETTURA = 10                                 # quanti appunti leggo a voce in una volta


def _leggi_appunti():
    try:
        d = json.loads(FILE_APPUNTI.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _scrivi_appunti(lista):
    tmp = FILE_APPUNTI.with_suffix(".tmp")
    tmp.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, FILE_APPUNTI)


def elenco_appunti():
    with _blocco_appunti:
        return _leggi_appunti()


def aggiungi_appunto(testo, adesso=None):
    adesso = adesso or dt.datetime.now()
    with _blocco_appunti:
        lista = _leggi_appunti()
        voce = {"id": uuid.uuid4().hex[:8], "testo": testo.strip(), "quando": adesso.strftime("%Y-%m-%dT%H:%M:%S")}
        lista.append(voce)
        _scrivi_appunti(lista)
        return voce


def _rimuovi_appunti(ids=None):
    """ids=None: tutti. Restituisce quanti ne ha tolti."""
    with _blocco_appunti:
        lista = _leggi_appunti()
        resto = [] if ids is None else [a for a in lista if a["id"] not in ids]
        _scrivi_appunti(resto)
        return len(lista) - len(resto)


_OGGETTO_APPUNTO = r"(?:nota|note|appunto|appunti)"
_AGGIUNGI_APPUNTO = (r"\b(?:prendi|prendere|prendimi|fai|fammi|scrivi|scrivimi|salva|salvami|aggiungi|crea)\s+"
                     r"(?:una\s+|un\s+|la\s+|mi una\s+|mi un\s+)?(?:nuova\s+|nuovo\s+)?" + _OGGETTO_APPUNTO + r"\b"
                     r"|\b(?:annota|annotami|annotare|appunta|appuntami|appuntare)\b"
                     r"|\b(?:aggiungi|salva|scrivi|metti)\s+(?:questo\s+|questa\s+)?(?:agli|negli|nei|ai)\s+appunti\b")


def _testo_dopo(frase, f, m):
    """Il testo da annotare: quello che segue il comando, con maiuscole e accenti originali."""
    resto = frase[m.end():].strip(" :,.;-—")
    resto = re.sub(r"^(?:che|di|questo|quanto segue|segue|la seguente)\s*[:,]?\s+", "", resto, flags=re.I).strip(" :,.;-—")
    return resto


def _gestisci_appunti(f, frase, adesso):
    if not re.search(r"\b" + _OGGETTO_APPUNTO + r"\b|\bannota\w*\b|\bappunta\w*\b", f):
        return None
    m = re.search(_AGGIUNGI_APPUNTO, f)
    if m and not re.search(r"\b(?:cancella|elimina|svuota|leggi|leggimi)\b", f):
        testo = _testo_dopo(frase, f, m)
        if not testo:
            return Ut("Cosa devo annotare?", "appunti: chiedo il testo", stato={"fase": "appunto_testo"})
        aggiungi_appunto(testo, adesso)
        return Ut(f"Annotato: {testo}.", "appunto salvato")
    lista = elenco_appunti()
    if re.search(r"\b(?:cancella|cancellare|elimina|eliminare|svuota|svuotare|togli|togliere|butta|rimuovi)\b", f):
        if not lista:
            return Ut("Non hai nessun appunto.", "appunti: niente da cancellare")
        if re.search(r"\b(?:ultimo|ultima)\b", f):
            ultimo = lista[-1]
            _rimuovi_appunti({ultimo["id"]})
            return Ut(f"Ho cancellato l'ultimo appunto: {ultimo['testo']}.", "appunto cancellato")
        return Ut(f"Vuoi davvero cancellare {'il tuo appunto' if len(lista) == 1 else f'tutti i {len(lista)} appunti'}? Dì sì per confermare.",
                  "appunti: chiedo conferma", stato={"fase": "appunti_cancella"})
    if re.search(r"\b(?:quanti|quante)\b", f):
        n = len(lista)
        return Ut("Non hai nessun appunto." if n == 0 else ("Hai un appunto." if n == 1 else f"Hai {n} appunti."), "appunti: conto")
    if re.search(r"\b(?:leggi|leggimi|leggere|rileggi|dimmi|dimmeli|elenca|elencami|mostra|mostrami|ascoltare|sentire|che|quali|ho|ripeti)\b", f):
        if not lista:
            return Ut("Non hai nessun appunto.", "appunti: vuoti")
        if re.search(r"\b(?:ultimo|ultima)\b", f):
            return Ut(f"L'ultimo appunto: {lista[-1]['testo']}.", "appunti: letto l'ultimo")
        voci = lista[-LIMITE_LETTURA:]
        parti = [f"{i}: {a['testo']}." for i, a in enumerate(voci, len(lista) - len(voci) + 1)]
        intro = "Hai un appunto. " if len(lista) == 1 else f"Hai {len(lista)} appunti. "
        if len(lista) > len(voci):
            intro += f"Leggo gli ultimi {len(voci)}. "
        return Ut(intro + " ".join(parti), "appunti: letti")
    return None


# ======================================================================
# CALCOLI (in locale, senza internet e SENZA eval: un piccolo valutatore con le precedenze)
# ======================================================================
_U = dict(promemoria._NUMERI["it"])                  # zero ... novantanove (anche "ventuno", "ventotto"...)


def _sotto_mille(s):
    if s == "":
        return 0
    n = 0
    s = re.sub(r"cent(?=ott)", "cento", s)           # "centottanta" = cento + ottanta
    m = re.fullmatch(r"(?:(due|tre|quattro|cinque|sei|sette|otto|nove)?cento)(.*)", s)
    if m:
        n = (_U[m.group(1)] if m.group(1) else 1) * 100
        s = m.group(2)
        if s == "":
            return n
    v = _U.get(s)
    return None if v is None else n + v


def parola_a_numero(s):
    """"centoventi" -> 120, "duemilacinquecento" -> 2500, "un milione" -> 1000000. None se non è un numero."""
    s = s.replace(" ", "")
    if not s:
        return None
    m = re.fullmatch(r"(.*?)(milioni|milione)(.*)", s)
    if m:
        sx = 1 if m.group(1) in ("", "un", "uno") else parola_a_numero(m.group(1))
        dx = 0 if m.group(3) == "" else parola_a_numero(m.group(3))
        return None if sx is None or dx is None else sx * 1_000_000 + dx
    m = re.fullmatch(r"(.*?)(mille|mila)(.*)", s)
    if m:
        sx = 1 if (m.group(2) == "mille" and m.group(1) == "") else _sotto_mille(m.group(1))
        dx = _sotto_mille(m.group(3))
        return None if not sx or dx is None else sx * 1000 + dx
    return _sotto_mille(s)


_OPERATORI = {"piu": "+", "meno": "-", "per": "*", "x": "*", "volte": "*", "moltiplicato": "*", "moltiplicata": "*",
              "diviso": "/", "fratto": "/", "+": "+", "-": "-", "*": "*", "/": "/", "^": "^", "%": "%"}
_RIEMPITIVI_CALCOLO = {"eva", "quanto", "quant", "fa", "fanno", "viene", "vengono", "vale", "e", "il", "lo", "la", "le",
                       "gli", "i", "un", "uno", "una", "dimmi", "calcola", "calcolami", "calcolare", "fammi", "fai",
                       "calcolo", "risultato", "qual", "quale", "di", "del", "della", "uguale", "pari", "a", "da",
                       "mi", "puoi", "potresti", "sai", "dirmi", "dire", "ora", "adesso", "che", "cosa"}
_TRIGGER_CALCOLO = (r"\b(?:quanto|quanti|quant)\s+(?:fa|fanno|viene|vengono|vale|e|da)\b|\bcalcol\w+\b|"
                    r"\brisultato di\b|\bdimmi quanto\b")
_INIZIO_CALCOLO = r"^(?:eva\s+)?(?:la\s+)?(?:radice|meta di|il doppio di|il triplo di|un terzo di|un quarto di|un quinto di|il \d+(?:[.,]\d+)?\s*(?:%|per cento|percento))"


def _senza_accenti(frase):
    s = unicodedata.normalize("NFD", frase.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _norm_calcolo(s):
    """s = la frase minuscola e senza accenti (_senza_accenti)."""
    s = s.replace("'", " ").replace("×", " * ").replace("÷", " / ")
    s = re.sub(r"[,.](?!\d)", " ", s)                                           # "Eva, quanto fa..." (non "1,5")
    s = re.sub(r"(?<=\d)\s*x\s*(?=\d)", " * ", s)
    s = re.sub(r"\bper favore\b|\bper piacere\b", " ", s)
    s = re.sub(r"\bper cento\b|\bpercento\b", " % ", s)
    s = re.sub(r"\bdiviso per\b|\bdiviso\b", " diviso ", s)
    s = re.sub(r"\bmoltiplicato per\b|\bmoltiplicata per\b", " * ", s)
    s = re.sub(r"\b(?:la )?meta di\b", " 1 / 2 * ", s)
    s = re.sub(r"\bil doppio di\b", " 2 * ", s)
    s = re.sub(r"\bil triplo di\b", " 3 * ", s)
    s = re.sub(r"\bun terzo di\b", " 1 / 3 * ", s)
    s = re.sub(r"\bun quarto di\b", " 1 / 4 * ", s)
    s = re.sub(r"\bun quinto di\b", " 1 / 5 * ", s)
    s = re.sub(r"\bradice cubica( di| del)?\b", " cbrt ", s)
    s = re.sub(r"\bradice( quadrata)?( di| del)?\b", " sqrt ", s)
    s = re.sub(r"\bal quadrato\b", " ^ 2 ", s)
    s = re.sub(r"\bal cubo\b", " ^ 3 ", s)
    s = re.sub(r"\b(?:elevato )?(?:alla|a) seconda\b", " ^ 2 ", s)
    s = re.sub(r"\b(?:elevato )?(?:alla|a) terza\b", " ^ 3 ", s)
    s = re.sub(r"\b(?:elevato )?(?:alla|a) quarta\b", " ^ 4 ", s)
    s = re.sub(r"\belevato (?:alla|al|a)\b", " ^ ", s)
    s = re.sub(r"([+*/^%])", r" \1 ", s)
    s = re.sub(r"(?<=\s)-(?=\s)|^-(?=\s)|(?<=\s)-(?=\d)|(?<=\d)-(?=\d)", " - ", s)      # "-" come meno ("9-3", "-5")
    s = re.sub(r"[?!;]+", " ", s)
    return s


def _numero_digitato(tok):
    tok = tok.rstrip(".,")
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", tok):
        return None
    if "," in tok:
        tok = tok.replace(".", "").replace(",", ".")
    elif "." in tok and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", tok):
        tok = tok.replace(".", "")
    try:
        return Decimal(tok)
    except InvalidOperation:
        return None


def _tokens_calcolo(s):
    """Il testo normalizzato -> lista di ("n", Decimal) | ("o", "+-*/^%") | ("f", "sqrt"|"cbrt") | ("di",). None se c'è altro."""
    parole = s.split()
    out, i = [], 0
    while i < len(parole):
        w = parole[i]
        if w in ("sqrt", "cbrt"):
            out.append(("f", w))
        elif w in _OPERATORI:
            out.append(("o", _OPERATORI[w]))
        elif w == "di" and out and out[-1] == ("o", "%"):
            out.append(("di",))
        elif _numero_digitato(w) is not None:
            n = _numero_digitato(w)
            if i + 1 < len(parole) and parole[i + 1] == "virgola" and i + 2 < len(parole):      # "3 virgola 14"
                dec = parole[i + 2]
                cifre = dec if dec.isdigit() else (str(parola_a_numero(dec)) if parola_a_numero(dec) is not None else None)
                if cifre is None:
                    return None
                n = Decimal(f"{n}.{cifre}")
                i += 2
            out.append(("n", n))
        elif parola_a_numero(w) is not None:
            pezzi = [w]
            while i + 1 < len(parole) and parola_a_numero(parole[i + 1]) is not None and _numero_digitato(parole[i + 1]) is None:
                pezzi.append(parole[i + 1])
                i += 1
            n = parola_a_numero("".join(pezzi))
            if n is None:
                return None
            n = Decimal(n)
            if i + 2 < len(parole) and parole[i + 1] == "virgola":                              # "venti virgola cinque"
                dec = parole[i + 2]
                cifre = dec if dec.isdigit() else (str(parola_a_numero(dec)) if parola_a_numero(dec) is not None else None)
                if cifre is None:
                    return None
                n = Decimal(f"{n}.{cifre}")
                i += 2
            out.append(("n", n))
        elif w in _RIEMPITIVI_CALCOLO:
            pass
        else:
            return None                                       # una parola che non è di un calcolo: non è un calcolo
        i += 1
    # "15 % di 200" -> (15 / 100) * 200
    risultato, k = [], 0
    while k < len(out):
        if (out[k][0] == "n" and k + 3 < len(out) + 0 and out[k + 1] == ("o", "%") and out[k + 2] == ("di",)
                and out[k + 3][0] in ("n", "f")):
            risultato += [("n", out[k][1] / Decimal(100)), ("o", "*")]
            k += 3
            continue
        if out[k] == ("di",):
            return None
        risultato.append(out[k])
        k += 1
    return risultato


class _ErroreCalcolo(Exception):
    pass


def _valuta(tokens):
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def avanti():
        t = tokens[pos[0]]
        pos[0] += 1
        return t

    def espressione():
        v = termine()
        while peek() in (("o", "+"), ("o", "-")):
            op = avanti()[1]
            d = termine()
            v = v + d if op == "+" else v - d
        return v

    def termine():
        v = potenza()
        while peek() in (("o", "*"), ("o", "/")):
            op = avanti()[1]
            d = potenza()
            if op == "*":
                v = v * d
            else:
                if d == 0:
                    raise ZeroDivisionError
                v = v / d
        return v

    def potenza():
        base = unario()
        if peek() == ("o", "^"):
            avanti()
            esp = potenza()
            if esp != esp.to_integral_value() or abs(esp) > 100 or (abs(base) > 1 and abs(esp) * base.adjusted() > 30):
                raise _ErroreCalcolo("troppo grande")
            base = base ** int(esp)
        return base

    def unario():
        t = peek()
        if t is None:
            raise _ErroreCalcolo("incompleto")
        if t == ("o", "-"):
            avanti()
            return -unario()
        if t == ("o", "+"):
            avanti()
            return unario()
        if t[0] == "f":
            avanti()
            x = unario()
            if t[1] == "sqrt":
                if x < 0:
                    raise _ErroreCalcolo("radice di un numero negativo")
                return x.sqrt()
            return x.copy_abs() ** (Decimal(1) / Decimal(3)) * (1 if x >= 0 else -1)
        if t[0] == "n":
            avanti()
            return t[1]
        raise _ErroreCalcolo("sintassi")

    v = espressione()
    if pos[0] != len(tokens):
        raise _ErroreCalcolo("sintassi")
    return v


def _numero_parlato(v):
    """Decimal -> testo da dire: 96 -> "96", 2.5 -> "2,5", -3 -> "meno 3"."""
    v = v.quantize(Decimal("0.0001")).normalize() if v != v.to_integral_value() else v.to_integral_value()
    testo = format(v, "f")
    if "." in testo:
        testo = testo.rstrip("0").rstrip(".").replace(".", ",")
    return ("meno " + testo[1:]) if testo.startswith("-") else testo


def calcola(frase):
    """La frase è un calcolo? Restituisce il testo da dire, oppure None se non lo è."""
    pre = _senza_accenti(frase)
    s = _norm_calcolo(pre)
    pre_pulita = re.sub(r"[,.](?!\d)", " ", pre.replace("'", " "))
    if not re.search(_TRIGGER_CALCOLO, s) and not re.search(_INIZIO_CALCOLO, " ".join(pre_pulita.split())):
        return None
    tokens = _tokens_calcolo(s)
    if not tokens or not any(t[0] == "n" for t in tokens):
        return None
    if len(tokens) < 2 or not any(t[0] in ("o", "f") for t in tokens):
        return None                                             # un numero solo: non è un calcolo
    try:
        with localcontext() as ctx:
            ctx.prec = 40
            v = _valuta(tokens)
    except ZeroDivisionError:
        return "Non si può dividere per zero."
    except _ErroreCalcolo as e:
        if str(e) == "troppo grande":
            return "Il risultato è un numero troppo grande per dirlo a voce."
        if str(e).startswith("radice"):
            return "La radice quadrata di un numero negativo non esiste."
        return None
    except (InvalidOperation, OverflowError):
        return "Il risultato è un numero troppo grande per dirlo a voce."
    if abs(v) >= Decimal(10) ** 15:
        return "Il risultato è un numero troppo grande per dirlo a voce."
    return f"Fa {_numero_parlato(v)}."


def _gestisci_calcolo(frase):
    testo = calcola(frase)
    return Ut(testo, "calcolo") if testo else None


# ======================================================================
# Numeri nelle frasi (cifre o parole): "al 50", "al cinquanta per cento", "di venti"
# ======================================================================
def _numero_in(f):
    """Il primo numero intero nel testo piegato (cifre o parole). None se non c'è."""
    parole = f.split()
    for i, w in enumerate(parole):
        if w.isdigit():
            return int(w)
        if parola_a_numero(w) is not None:
            pezzi = [w]
            while i + 1 < len(parole) and parola_a_numero(parole[i + 1]) is not None and not parole[i + 1].isdigit():
                pezzi.append(parole[i + 1])
                i += 1
            n = parola_a_numero("".join(pezzi))
            if n is not None:
                return n
    return None


# ======================================================================
# BATTERIA
# ======================================================================
def _gestisci_batteria(f):
    if not re.search(r"\bbatteria\b|\bquanta carica\b|\bin carica\b|\bcaricando\b|\bcaricatore\b", f):
        return None
    b = pc.batteria()
    if not b.get("presente"):
        return Ut("Questo computer non ha una batteria: è alimentato dalla corrente.", "batteria: assente")
    perc, carica = b["percentuale"], b["in_carica"]
    if carica and perc >= 100:
        return Ut("La batteria è carica al 100 per cento ed è collegata alla corrente.", "batteria")
    testo = f"La batteria è al {perc} per cento" + (" e si sta caricando." if carica else " e non è in carica.")
    if not carica and b.get("secondi"):
        testo += f" Dovrebbe durare circa {durata_parlata(round(b['secondi'] / 60) * 60)}."
    if not carica and perc <= 20:
        testo += " È quasi scarica: collega il caricatore."
    return Ut(testo, "batteria")


# ======================================================================
# MUSICA (pausa, riprendi, brano successivo e precedente, "che canzone è")
# ======================================================================
_OGG_MUSICA = r"(?:musica|canzone|brano|traccia|riproduzione|player|lettore|video)"
_MUSICA_PAUSA = (r"\b(?:metti|mettere|mettimi|fai)\s+(?:in\s+)?pausa\b|^(?:eva\s+)?pausa$|\bpausa\s+(?:alla\s+|la\s+)?" + _OGG_MUSICA +
                 r"\b|\b(?:ferma|fermare|stoppa|stop|interrompi|sospendi|zittisci)\s+(?:la\s+|il\s+|lo\s+)?" + _OGG_MUSICA + r"\b|"
                 r"\bstop\s+" + _OGG_MUSICA + r"\b")
_MUSICA_RIPRENDI = (r"\b(?:riprendi|riprendere|riavvia|riproduci|rimetti|rimettila|fai ripartire|fai partire|avvia|metti)\s+(?:la\s+|il\s+|lo\s+)?" +
                    _OGG_MUSICA + r"\b|^(?:eva\s+)?(?:riprendi|riproduci|play)$|\bplay\s+" + _OGG_MUSICA + r"\b")
_MUSICA_AVANTI = (r"\b(?:prossim[ao]|successiv[ao])\s+" + _OGG_MUSICA + r"\b|\b" + _OGG_MUSICA + r"\s+(?:successiv[ao]|prossim[ao])\b|"
                  r"\b(?:salta|saltare|skip)\s+(?:la\s+|il\s+|alla\s+|al\s+)?" + _OGG_MUSICA + r"\b|^(?:eva\s+)?(?:skip|salta)$|"
                  r"\bpassa\s+(?:alla|al)\s+(?:prossim[ao]|successiv[ao])\s+" + _OGG_MUSICA + r"\b|"
                  r"\bavanti\s+(?:con\s+)?(?:la\s+|il\s+)?" + _OGG_MUSICA + r"\b")
_MUSICA_INDIETRO = (r"\b(?:precedente)\s+" + _OGG_MUSICA + r"\b|\b" + _OGG_MUSICA + r"\s+precedente\b|\b" + _OGG_MUSICA + r"\s+di prima\b|"
                    r"\bindietro\s+(?:con\s+)?(?:la\s+|il\s+)?" + _OGG_MUSICA + r"\b|\btorna\s+(?:alla|al|a)\s+(?:canzone|brano|traccia)\b")
_MUSICA_CHE = r"\b(?:che|quale|cosa)\s+(?:canzone|brano|musica|traccia)\b|\bcosa sta suonando\b|\bche cosa sta suonando\b|\bchi canta\b"


def _gestisci_musica(f):
    if re.search(_MUSICA_CHE, f):
        azione = "stato"
    elif re.search(_MUSICA_INDIETRO, f):
        azione = "indietro"
    elif re.search(_MUSICA_AVANTI, f):
        azione = "avanti"
    elif re.search(_MUSICA_PAUSA, f):
        azione = "pausa"
    elif re.search(_MUSICA_RIPRENDI, f):
        azione = "riprendi"
    else:
        return None
    r = pc.musica(azione)
    esito, titolo = r.get("esito"), (r.get("titolo") or "").strip()
    nome = f": {titolo}" if titolo else ""
    if "errore" in r:
        return Ut("Non riesco a controllare il lettore musicale.", f"musica {azione}: errore")
    if esito == "nessun_lettore":
        return Ut("Non c'è nessuna musica in riproduzione. Apri prima un lettore, per esempio Spotify o YouTube.", f"musica {azione}: nessun lettore")
    if esito == "rifiutato":
        return Ut("Il lettore non accetta questo comando.", f"musica {azione}: rifiutato")
    if azione == "stato":
        sta = "Sta suonando" if r.get("stato") == "Playing" else "In pausa"
        return Ut(f"{sta}{nome}." if titolo else ("Non so dirti che brano è." if r.get("stato") == "Playing" else "La musica è in pausa."), "musica: stato")
    if esito == "non_in_riproduzione":
        return Ut("La musica è già in pausa.", "musica: già in pausa")
    if esito == "gia_in_riproduzione":
        return Ut("La musica è già in riproduzione.", "musica: già in riproduzione")
    if azione == "pausa":
        return Ut(f"Musica in pausa{nome}." if titolo else "Musica in pausa.", "musica: pausa")
    if azione == "riprendi":
        return Ut(f"Riprendo{nome}." if titolo else "Riprendo la musica.", "musica: ripresa")
    return Ut((f"Brano successivo{nome}." if azione == "avanti" else f"Brano precedente{nome}.") if titolo
              else ("Brano successivo." if azione == "avanti" else "Brano precedente."), f"musica: {azione}")


# ======================================================================
# LUMINOSITÀ
# ======================================================================
LUM_MIN, LUM_PASSO = 5, 10                       # non scendo sotto il 5% (schermo nero): il passo di "alza / abbassa"


def _gestisci_luminosita(f):
    if not re.search(r"\bluminosita\b|\bluminosa\b|\billuminazione\b|\bschermo\s+(?:piu|meno)\s+(?:luminoso|scuro|chiaro)\b", f):
        return None
    attuale = pc.luminosita_leggi()
    if not attuale.get("supportata"):
        return Ut("Non riesco a regolare la luminosità di questo schermo: non accetta il comando.", "luminosità: non supportata")
    ora = attuale["percentuale"]
    n = _numero_in(re.sub(r"\bper cento\b|\bpercento\b", " ", f))
    su = re.search(r"\b(?:alza|alzare|aumenta|aumentare|rialza|accresci)\b|\bpiu luminos|\bpiu chiaro\b", f)
    giu = re.search(r"\b(?:abbassa|abbassare|diminuisci|diminuire|riduci|ridurre|scurisci)\b|\bmeno luminos|\bpiu scuro\b|\bmeno chiaro\b", f)
    if re.search(r"\b(?:massima|massimo|al massimo|piena|al cento)\b", f):
        nuovo = 100
    elif re.search(r"\b(?:minima|minimo|al minimo)\b", f):
        nuovo = LUM_MIN
    elif (su or giu) and n is not None and re.search(r"\bdi\s+\w+", f):
        nuovo = ora + (n if su else -n)                                   # "alza la luminosità di 20"
    elif su or giu:
        passo = 30 if re.search(r"\b(?:molto|tanto|parecchio)\b", f) else LUM_PASSO
        nuovo = ora + (passo if su else -passo)
    elif n is not None and re.search(r"\b(?:al|a|su|imposta|metti|porta)\b", f):
        nuovo = n                                                          # "luminosità al 50"
    else:
        return Ut(f"La luminosità è al {ora} per cento.", "luminosità: letta")
    nuovo = max(LUM_MIN, min(100, nuovo))
    if nuovo == ora:
        return Ut(f"La luminosità è già al {ora} per cento" + (" (il massimo)." if ora == 100 else "."), "luminosità: invariata")
    r = pc.luminosita_imposta(nuovo)
    if not r.get("ok"):
        return Ut("Non sono riuscita a cambiare la luminosità: lo schermo non ha risposto.", "luminosità: errore")
    return Ut(f"Luminosità al {nuovo} per cento.", "luminosità: impostata")


# ======================================================================
# WI-FI e BLUETOOTH (spegnere il Wi-Fi chiede prima conferma: senza internet Eva perde Groq)
# ======================================================================
_WIFI = r"\bwi ?fi\b|\bwifi\b|\bwaifai\b|\buai ?fai\b|\bwireless\b|\brete senza fili\b"
_BLUETOOTH = r"\bbluetooth\b|\bblue ?tooth\b|\bblutut\b|\bblu ?tooth\b"
_NOMI_RADIO = {"wifi": "Wi-Fi", "bluetooth": "Bluetooth"}


def _gestisci_radio(f):
    if re.search(_BLUETOOTH, f):
        quale = "bluetooth"
    elif re.search(_WIFI, f):
        quale = "wifi"
    else:
        return None
    accendi = re.search(r"\b(?:accendi|accendere|accendimi|attiva|attivare|abilita|abilitare|riattiva|riaccendi|riaccendere|metti|rimetti)\b", f)
    spegni = re.search(r"\b(?:spegni|spegnere|spegnimi|disattiva|disattivare|disabilita|disabilitare|stacca|staccare|togli)\b", f)
    chiede = re.search(r"\b(?:acceso|accesa|attivo|attiva|spento|spenta|stato|funziona|collegato|connesso|e on|e off|come va)\b", f)
    if accendi and not spegni:
        azione = "accendi"
    elif spegni and not accendi:
        azione = "spegni"
    elif chiede:
        azione = "stato"
    else:
        return None
    return _esegui_radio(quale, azione, confermato=False)


def _esegui_radio(quale, azione, confermato):
    nome = _NOMI_RADIO[quale]
    st = pc.radio(quale, "stato")
    if "errore" in st:
        return Ut(f"Non riesco a leggere lo stato del {nome}.", f"{quale}: errore")
    if not st.get("presente"):
        return Ut(f"Questo computer non ha il {nome}.", f"{quale}: assente")
    stato = st.get("stato")
    if stato == "Disabled":
        return Ut(f"Il {nome} è disattivato da Windows o dall'hardware: da qui non posso cambiarlo.", f"{quale}: disabilitato")
    acceso = stato == "On"
    if azione == "stato":
        return Ut(f"Il {nome} è {'acceso' if acceso else 'spento'}.", f"{quale}: stato")
    if azione == "accendi" and acceso:
        return Ut(f"Il {nome} è già acceso.", f"{quale}: già acceso")
    if azione == "spegni" and not acceso:
        return Ut(f"Il {nome} è già spento.", f"{quale}: già spento")
    if azione == "spegni" and quale == "wifi" and not confermato:
        return Ut("Spengo il Wi-Fi? Resterò senza internet e non potrò usare Groq finché non lo riaccendi. Dì sì per confermare.",
                  "wifi: chiedo conferma", stato={"fase": "wifi_off"})
    r = pc.radio(quale, azione)
    if r.get("esito") == "accesso_negato":
        return Ut(f"Windows non mi permette di cambiare il {nome}. Controlla le impostazioni di Windows.", f"{quale}: accesso negato")
    if "errore" in r or r.get("stato") not in ("On", "Off"):
        return Ut(f"Ho provato, ma non sono riuscita a cambiare il {nome}.", f"{quale}: errore")
    voluto = "On" if azione == "accendi" else "Off"
    if r.get("stato") != voluto:
        return Ut(f"Ho provato, ma il {nome} è ancora {'acceso' if r.get('stato') == 'On' else 'spento'}.", f"{quale}: non cambiato")
    if azione == "accendi":
        return Ut(f"{nome} acceso." + (" Tra qualche secondo si ricollega." if quale == "wifi" else ""), f"{quale}: acceso")
    return Ut(f"{nome} spento." + (" Per riaccenderlo dimmi: accendi il Wi-Fi." if quale == "wifi" else ""), f"{quale}: spento")


# ======================================================================
# Annuncio alla scadenza (lo usa vox_core._pr_annuncia per i promemoria con un "tipo")
# ======================================================================
def annuncio(p, nome, ritardo, quando_parlato, ora_attuale):
    """La frase da dire quando scade un timer o una sveglia. None = promemoria normale (frase di sempre)."""
    nome = (nome or "").strip()
    tipo = p.get("tipo")
    if tipo == "timer":
        if ritardo > 120:
            return f"{nome + ', ' if nome else ''}mentre Eva era spenta è scaduto il {p['cosa']} ({quando_parlato})."
        return f"{nome + ', ' if nome else ''}il {p['cosa']} è scaduto."
    if tipo == "sveglia":
        if ritardo > 120:
            return f"{nome + ', ' if nome else ''}la sveglia {quando_parlato} non è suonata perché Eva era spenta. {ora_attuale}."
        saluto = f"Sveglia, {nome}!" if nome else "Sveglia!"
        return f"{saluto} {ora_attuale}. {saluto}"
    return None


# ======================================================================
# Punto d'ingresso
# ======================================================================
def _risposta_a_stato(frase, stato, adesso):
    """Eva aveva fatto una domanda: questa frase è la risposta. None = non c'entra (la frase prosegue normale)."""
    f = piega(frase)
    fase = stato.get("fase")
    if fase == "timer_durata":
        d = durata_secondi(f)
        if d is None:
            return None
        return _crea_timer(d, adesso)
    if fase == "appunto_testo":
        if comandi_pc.e_no(frase) and len(f.split()) <= 3:
            return Ut("Va bene, non annoto niente.", "appunti: annullato")
        testo = frase.strip(" .,;:!?")
        if not testo:
            return None
        aggiungi_appunto(testo, adesso)
        return Ut(f"Annotato: {testo}.", "appunto salvato")
    if fase == "wifi_off":
        if comandi_pc.e_si(frase) and not comandi_pc.e_no(frase):
            return _esegui_radio("wifi", "spegni", confermato=True)
        if comandi_pc.e_no(frase):
            return Ut("Va bene, lascio il Wi-Fi acceso.", "wifi: spegnimento annullato")
        return None
    if fase == "appunti_cancella":
        if comandi_pc.e_si(frase) and not comandi_pc.e_no(frase):
            n = _rimuovi_appunti()
            return Ut("Ho cancellato l'appunto." if n == 1 else f"Ho cancellato tutti i {n} appunti.", "appunti cancellati")
        if comandi_pc.e_no(frase):
            return Ut("Va bene, non cancello niente.", "appunti: cancellazione annullata")
        return None
    return None


def interpreta(frase, stato=None, adesso=None):
    """La frase è per le utilità? Restituisce un Ut oppure None. stato = domanda in sospeso (dict) o None."""
    adesso = adesso or dt.datetime.now()
    if stato:
        r = _risposta_a_stato(frase, stato, adesso)
        if r is not None:
            return r
    f = piega(frase.replace("'", " "))
    if not f.strip():
        return None
    for gestore in (lambda: _gestisci_cronometro(f), lambda: _gestisci_timer(f, adesso),
                    lambda: _gestisci_sveglia(f, frase, adesso), lambda: _gestisci_appunti(f, frase, adesso),
                    lambda: _gestisci_calcolo(frase), lambda: _gestisci_batteria(f), lambda: _gestisci_musica(f),
                    lambda: _gestisci_luminosita(f), lambda: _gestisci_radio(f)):
        r = gestore()
        if r is not None:
            return r
    return None
