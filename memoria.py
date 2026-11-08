"""Memoria della conversazione in corso (italiano, francese, spagnolo, inglese).

Eva ricorda gli ultimi scambi (domanda, risposta, che tipo di scambio era) così capisce le frasi che si riferiscono
alla precedente:  "e domani?" dopo il meteo, "e a Roma?", "ripeti", "dillo più piano", "dimmi di più", "perché?".

  - SOLO IN RAM: niente viene scritto su disco. Si svuota quando Eva si spegne, con "dimentica tutto" / "ricominciamo",
    e da sola per gli scambi vecchi di più di MAX_ETA_SECONDI (la conversazione "in corso" finisce).
  - Tiene solo gli ultimi MAX_TURNI scambi. A Groq ne manda al massimo MAX_TURNI_GROQ, e comunque non più di
    MAX_CARATTERI_GROQ caratteri in tutto (ogni messaggio è accorciato): la risposta non rallenta.
  - I comandi semplici (apri, chiudi, promemoria, finestre...) restano locali e veloci. Se "e domani?" arriva dopo uno
    di questi, Eva NON indovina: chiede di che cosa si parla.

Questo modulo non parla e non scrive niente: registra, riconosce le frasi e prepara i messaggi per Groq.
"""
import contextlib
import re
import threading
import time
from collections import deque

import promemoria
import vox_lingue as lingue

MAX_TURNI = 8                 # scambi tenuti in memoria
MAX_TURNI_GROQ = 5            # scambi mandati a Groq
MAX_CARATTERI_GROQ = 1200     # budget totale della cronologia mandata a Groq (circa 330 token): misurato, non rallenta
MAX_CARATTERI_MSG = 240       # ogni messaggio mandato a Groq è accorciato a tanto
MAX_ETA_SECONDI = 900         # dopo 15 minuti di silenzio la conversazione "in corso" è finita

_lock = threading.RLock()
_turni = deque(maxlen=MAX_TURNI)
_pendente = None


class Turno:
    def __init__(self, domanda):
        self.domanda = domanda.strip()
        self.risposta = ""
        self.tipo = "comando"        # "comando" (locale) | "chat" (Groq o chiacchiere) | "meteo"
        self.ctx = {}
        self.quando = time.time()


# ----------------------------------------------------------------------
# Registrare
# ----------------------------------------------------------------------
def _scadenza():
    limite = time.time() - MAX_ETA_SECONDI
    while _turni and _turni[0].quando < limite:
        _turni.popleft()


def nuovo_turno(domanda):
    """Inizia un nuovo scambio (e chiude quello precedente, che entra in memoria)."""
    global _pendente
    with _lock:
        if _pendente is not None:
            _turni.append(_pendente)
        _scadenza()
        _pendente = Turno(domanda)
        return _pendente


def completa(tipo=None, ctx=None, risposta=None):
    """Dice come è andato lo scambio in corso: tipo ("chat", "meteo"...), contesto e/o testo della risposta."""
    with _lock:
        if _pendente is None:
            return
        if tipo:
            _pendente.tipo = tipo
        if ctx:
            _pendente.ctx = dict(ctx)
        if risposta is not None:
            _pendente.risposta = risposta.strip()


def aggiungi_detta(testo):
    """Eva ha detto `testo` durante lo scambio in corso."""
    with _lock:
        if _pendente is not None and testo:
            _pendente.risposta = (_pendente.risposta + " " + testo.strip()).strip()


def annulla_turno():
    """Lo scambio in corso non va in memoria (es. "ripeti": non deve diventare lui l'ultima risposta)."""
    global _pendente
    with _lock:
        _pendente = None


@contextlib.contextmanager
def fuori_turno():
    """Mentre Eva dice qualcosa che non c'entra con lo scambio in corso (es. un promemoria che scatta)."""
    global _pendente
    with _lock:
        messo = _pendente
        _pendente = None
    try:
        yield
    finally:
        with _lock:
            _pendente = messo if _pendente is None else _pendente


def svuota():
    global _pendente
    with _lock:
        _turni.clear()
        _pendente = None


def conta():
    with _lock:
        _scadenza()
        return len(_turni)


def ultimo():
    """L'ultimo scambio CONCLUSO (non quello in corso) o None."""
    with _lock:
        _scadenza()
        return _turni[-1] if _turni else None


def _accorcia(t, n=MAX_CARATTERI_MSG):
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def messaggi_groq():
    """Gli ultimi scambi come messaggi per Groq, in ordine, entro il budget di lunghezza."""
    with _lock:
        _scadenza()
        scelti, usati = [], 0
        for t in reversed(_turni):
            if len(scelti) >= MAX_TURNI_GROQ:
                break
            if not t.risposta:                  # un comando silenzioso non dice niente a Groq
                continue
            u = _accorcia(t.domanda)
            a = _accorcia(t.risposta)
            if t.tipo == "meteo" and t.ctx.get("citta"):
                a += f" [{t.ctx['citta']}]"
            costo = len(u) + len(a)
            if usati + costo > MAX_CARATTERI_GROQ and scelti:
                break
            scelti.append((u, a))
            usati += costo
        out = []
        for u, a in reversed(scelti):
            out += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        return out


ISTRUZIONE_GROQ = {
    "it": (" Hai la memoria della conversazione in corso (gli ultimi scambi, qui sopra). Usala per capire le frasi che si "
           "riferiscono alle precedenti, come «e domani?», «e a Roma?», «ripeti» o «dimmi di più». Se non è chiaro a cosa si "
           "riferisce la frase, chiedi di cosa si sta parlando invece di indovinare."),
    "fr": (" Tu as la mémoire de la conversation en cours (les derniers échanges, ci-dessus). Utilise-la pour comprendre les "
           "phrases qui se rapportent aux précédentes, comme « et demain ? », « et à Rome ? », « répète » ou « dis-m'en plus ». "
           "Si on ne voit pas à quoi la phrase se rapporte, demande de quoi on parle au lieu de deviner."),
    "es": (" Tienes la memoria de la conversación en curso (los últimos intercambios, arriba). Úsala para entender las frases "
           "que se refieren a las anteriores, como «¿y mañana?», «¿y en Roma?», «repite» o «dime más». Si no está claro a qué "
           "se refiere la frase, pregunta de qué se está hablando en lugar de adivinar."),
    "en": (" You have the memory of the current conversation (the latest exchanges, above). Use it to understand sentences "
           "that refer to the previous ones, like \"and tomorrow?\", \"and in Rome?\", \"repeat\" or \"tell me more\". If it "
           "isn't clear what the sentence refers to, ask what we are talking about instead of guessing."),
}


def istruzione_groq():
    return ISTRUZIONE_GROQ[lingue.corrente] if conta() else ""


# ----------------------------------------------------------------------
# Riconoscere le frasi che si riferiscono a quella precedente
# ----------------------------------------------------------------------
_GIORNI = {
    "it": {"oggi": 0, "stasera": 0, "stamattina": 0, "questa sera": 0, "questa notte": 0, "domani": 1, "dopodomani": 2, "dopo domani": 2},
    "fr": {"aujourd hui": 0, "ce soir": 0, "ce matin": 0, "demain": 1, "apres demain": 2},
    "es": {"hoy": 0, "esta noche": 0, "esta tarde": 0, "esta manana": 0, "manana": 1, "pasado manana": 2},
    "en": {"today": 0, "tonight": 0, "this evening": 0, "tomorrow": 1, "the day after tomorrow": 2},
}


def _alt(lg):
    return "|".join(sorted((re.escape(k) for k in _GIORNI[lg]), key=len, reverse=True))


_NON_LUOGHI = {
    "it": {"tu", "lei", "voi", "te", "me", "io", "poi", "quindi", "allora", "dopo", "ora", "adesso", "anche", "ancora", "come",
           "cosa", "perche", "quando", "dove", "chi", "quanto", "questo", "quello", "altro", "altra", "no", "si",
           "acceso", "accesa", "spento", "spenta", "attivo", "attiva", "connesso", "collegato", "carica", "scarica", "pieno"},
    "fr": {"toi", "vous", "moi", "puis", "alors", "ensuite", "apres", "maintenant", "aussi", "encore", "comment", "quoi",
           "pourquoi", "quand", "ou", "qui", "combien", "ca", "ceci", "non", "oui"},
    "es": {"tu", "usted", "vos", "yo", "luego", "entonces", "despues", "ahora", "tambien", "todavia", "como", "que",
           "por que", "cuando", "donde", "quien", "cuanto", "eso", "esto", "no", "si"},
    "en": {"you", "me", "then", "so", "now", "also", "still", "how", "what", "why", "when", "where", "who", "that", "this",
           "no", "yes", "else", "more"},
}
_P = {
    "it": {
        "dimentica": r"dimentica tutto|dimentica la conversazione|dimentica quello che ho detto|dimentica quanto detto|ricominciamo|ricominciamo da capo|ricomincia da capo|cancella la conversazione|svuota la memoria|azzera la conversazione|azzera la memoria",
        "ripeti": r"ripeti(?:lo|la|mi)?|puoi ripetere|potresti ripetere|ripetere|ancora una volta|come hai detto|cosa hai detto|che cosa hai detto|non ho sentito|non ho capito",
        "piano": r"(?:dillo|dilla|dimmelo|parla|ripetilo|ripeti)\s+(?:un po |molto )?piu\s+(?:piano|lentamente|lento|adagio)|piu lentamente|piu lento|piu adagio",
        "di_piu": r"dimmi (?:di )?piu|dimmi altro|dimmene di piu|dammi piu dettagli|piu dettagli|approfondisci|spiegami meglio|spiegami di piu|continua|vai avanti|e poi|raccontami di piu|racconta di piu",
        "chiarimento": r"perche|come mai|in che senso|cioe|davvero|sul serio|sei sicura|sei sicuro",
        "e_giorno": r"e\s+(?:per\s+)?(?P<g>{giorni})",
        "e_luogo": r"e\s+(?:a|ad|in|per|per quanto riguarda)?\s*(?P<luogo>[a-z][a-z ]{{1,28}}?)(?:\s+(?P<g>{giorni}))?",
    },
    "fr": {
        "dimentica": r"oublie tout|oublie la conversation|oublions tout|on recommence|recommencons|recommencons a zero|efface la conversation|vide la memoire|efface la memoire",
        "ripeti": r"repete(?: le| la| ca)?|peux tu repeter|pouvez vous repeter|repeter|encore une fois|qu est ce que tu as dit|qu as tu dit|tu as dit quoi|je n ai pas entendu|je n ai pas compris",
        "piano": r"(?:dis le|dis la|parle|repete le|repete)\s+plus\s+(?:lentement|doucement|lent)|plus lentement|plus doucement",
        "di_piu": r"dis m en plus|dis moi en plus|dis m en davantage|plus de details|continue|explique mieux|explique moi mieux|raconte m en plus|et ensuite|va plus loin",
        "chiarimento": r"pourquoi|vraiment|comment ca|c est a dire|tu es sur",
        "e_giorno": r"et\s+(?:pour\s+)?(?P<g>{giorni})",
        "e_luogo": r"et\s+(?:a|en|au|aux|pour|dans)?\s*(?P<luogo>[a-z][a-z ]{{1,28}}?)(?:\s+(?P<g>{giorni}))?",
    },
    "es": {
        "dimentica": r"olvida todo|olvida la conversacion|olvidalo todo|empecemos de nuevo|empezamos de nuevo|empecemos otra vez|borra la conversacion|vacia la memoria|borra la memoria",
        "ripeti": r"repite(?:lo|la)?|puedes repetir|podrias repetir|repetir|otra vez|una vez mas|que has dicho|que dijiste|no te he oido|no he entendido",
        "piano": r"(?:dilo|dila|habla|repitelo|repite)\s+mas\s+(?:despacio|lento|lentamente|bajo|suave)|mas despacio|mas lentamente",
        "di_piu": r"dime mas|cuentame mas|dame mas detalles|mas detalles|continua|sigue|explicate mejor|explicame mejor|y luego|profundiza",
        "chiarimento": r"por que|como asi|de verdad|en serio|que quieres decir",
        "e_giorno": r"y\s+(?:para\s+)?(?P<g>{giorni})",
        "e_luogo": r"y\s+(?:en|a|para|de)?\s*(?P<luogo>[a-z][a-z ]{{1,28}}?)(?:\s+(?P<g>{giorni}))?",
    },
    "en": {
        "dimentica": r"forget everything|forget all that|forget it all|forget the conversation|let s start over|start over|start again|let s start again|clear the conversation|clear your memory|reset the conversation|reset your memory",
        "ripeti": r"repeat(?: that| it)?|say that again|say it again|what did you say|what was that|come again|once more|one more time|pardon|sorry what",
        "piano": r"(?:say it|say that|speak|repeat it|repeat that)\s+(?:more\s+)?(?:slowly|slower)|slower|more slowly|slow down|speak slower",
        "di_piu": r"tell me more|go on|continue|elaborate|more details|give me more details|explain more|explain further|and then|keep going",
        "chiarimento": r"why|really|how come|what do you mean|are you sure",
        "e_giorno": r"(?:and|what about|how about)\s+(?:for\s+)?(?P<g>{giorni})",
        "e_luogo": r"(?:and|what about|how about)\s+(?:in|at|for)?\s*(?P<luogo>[a-z][a-z ]{{1,28}}?)(?:\s+(?P<g>{giorni}))?",
    },
}
TX = {
    "it": {"dimenticato": "Fatto, ho dimenticato la nostra conversazione.", "chiedi_argomento": "Di che cosa stiamo parlando?",
           "niente_da_ripetere": "Non c'è niente da ripetere.", "solo_oggi_domani": "Posso dirti il meteo solo di oggi e di domani."},
    "fr": {"dimenticato": "C'est fait, j'ai oublié notre conversation.", "chiedi_argomento": "De quoi est-ce qu'on parle ?",
           "niente_da_ripetere": "Il n'y a rien à répéter.", "solo_oggi_domani": "Je peux te donner la météo seulement pour aujourd'hui et demain."},
    "es": {"dimenticato": "Hecho, he olvidado nuestra conversación.", "chiedi_argomento": "¿De qué estamos hablando?",
           "niente_da_ripetere": "No hay nada que repetir.", "solo_oggi_domani": "Solo puedo darte el tiempo de hoy y de mañana."},
    "en": {"dimenticato": "Done, I've forgotten our conversation.", "chiedi_argomento": "What are we talking about?",
           "niente_da_ripetere": "There's nothing to repeat.", "solo_oggi_domani": "I can only give you the weather for today and tomorrow."},
}


# Esempi per "cosa sai fare?" (aiuto.py): domande, meteo e conversazione
ESEMPI = {
    "it": ["che tempo fa a Milano", "e domani", "raccontami una curiosità", "dimmi di più", "ripeti", "dimentica tutto"],
    "fr": ["quel temps fait-il à Paris", "et demain", "raconte-moi une anecdote", "dis-m'en plus", "répète", "oublie tout"],
    "es": ["qué tiempo hace en Madrid", "y mañana", "cuéntame una curiosidad", "dime más", "repite", "olvida todo"],
    "en": ["what's the weather in London", "and tomorrow", "tell me an interesting fact", "tell me more", "repeat", "forget everything"],
}


def tx(chiave):
    return TX[lingue.corrente][chiave]


def riconosci(frase):
    """Se la frase si riferisce alla precedente (o è "dimentica tutto") restituisce (tipo, dati), altrimenti None.
    tipo: "dimentica" | "ripeti" | "piano" | "di_piu" | "chiarimento" | "e_giorno" | "e_luogo"
    dati: {"giorno": 0/1/2 o None, "luogo": testo originale o None}"""
    from comandi_pc import ripulisci
    lg = lingue.corrente
    grezza = promemoria.piega(frase)                # stessa lunghezza dell'originale
    lead = len(grezza) - len(grezza.lstrip())
    f = grezza.strip()
    pulita = ripulisci(frase)                       # senza "eva", "per favore", "puoi", "can you"...
    P = _P[lg]
    for tipo in ("dimentica", "ripeti", "piano", "di_piu", "chiarimento"):
        if re.fullmatch(P[tipo], pulita):
            return tipo, {"giorno": None, "luogo": None}
    if lg == "it" and re.match(r"\s*[èÈ]", frase):
        return None                                  # "è acceso il wifi?": «è» (verbo) non è la «e» di "e domani?"
    giorni = _alt(lg)
    m = re.fullmatch(P["e_giorno"].format(giorni=giorni), f)
    if m:
        return "e_giorno", {"giorno": _GIORNI[lg][m.group("g")], "luogo": None}
    m = re.fullmatch(P["e_luogo"].format(giorni=giorni), f)
    if m:
        luogo = m.group("luogo").strip()
        parole = luogo.split()
        if luogo and luogo not in _NON_LUOGHI[lg] and len(parole) <= 3 and not any(p in _NON_LUOGHI[lg] for p in parole[:1]):
            originale = frase[lead + m.start("luogo"):lead + m.end("luogo")].strip(" ,.;:!?")
            g = _GIORNI[lg][m.group("g")] if m.group("g") else None
            return "e_luogo", {"giorno": g, "luogo": originale}
    return None


def frase_meteo(citta, giorno):
    """Ricostruisce una domanda sul meteo completa (da dare al riconoscimento del meteo)."""
    lg = lingue.corrente
    g = {"it": ("oggi", "domani"), "fr": ("aujourd'hui", "demain"), "es": ("hoy", "mañana"), "en": ("today", "tomorrow")}[lg][1 if giorno == 1 else 0]
    return {"it": f"che tempo fa a {citta} {g}", "fr": f"quel temps fait-il à {citta} {g}",
            "es": f"qué tiempo hace en {citta} {g}", "en": f"what's the weather in {citta} {g}"}[lg]
