"""Il "cervello" di Eva: decide cosa rispondere a saluti e chiacchiere semplici.

Tutta la logica delle risposte è qui, in rispondi(frase). In futuro si potrà
collegare un modello di intelligenza artificiale SENZA toccare il resto:

    import vox_cervello
    vox_cervello.collega_modello(la_mia_funzione)   # riceve la frase, restituisce un testo

Per ora non c'è nessun servizio online e non servono chiavi.

Sicurezza: questo file SOLO sceglie frasi da dire (e legge ora e data del
computer). Non apre, non scrive, non cancella, non invia nulla.
"""
import datetime
import difflib
import json
import logging
import os
import random
import re
import unicodedata
from collections import deque, namedtuple
from pathlib import Path

import eva_percorsi as percorsi
from comandi import normalizza

import memoria
import vox_lingue as lingue

FILE_RISPOSTE = percorsi.risorsa("risposte.json")

# testo: cosa dire | azione: None / "chiudi" / "voce_off" / "voce_on" | continua: resta in ascolto
Risposta = namedtuple("Risposta", "testo azione continua nome_intento")

# Se la frase inizia con uno di questi verbi è un COMANDO (apri, cerca...): non è una chiacchiera.
VERBI_COMANDO = {"apri", "aprimi", "avvia", "lancia", "cerca", "cercami", "scrivi", "scrivimi",
                 "alza", "abbassa", "metti", "togli", "volume", "impara", "prepara", "manda", "invia"}

VERBI_CHIUDI = {"chiudi", "chiudere", "chiudimi", "chiudete", "termina", "killa"}

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]

NOMI_BELLI = {"youtube": "YouTube", "chrome": "Chrome", "gmail": "Gmail", "whatsapp": "WhatsApp",
              "outlook": "Outlook", "google": "Google", "facebook": "Facebook",
              "instagram": "Instagram", "spotify": "Spotify", "word": "Word", "excel": "Excel",
              "powerpoint": "PowerPoint", "edge": "Edge", "firefox": "Firefox"}

_dati = {}
_ultima = {}                     # ultima risposta data per ogni gruppo (per non ripetersi)
_modello_ia = None


# ----------------------------------------------------------------------
# Caricamento e piccole utilità
# ----------------------------------------------------------------------
def carica(log=None):
    """Legge risposte.json. Restituisce (numero di intenti, nome utente)."""
    global _dati
    _ultima.clear()
    try:
        file = lingue.file_risposte(FILE_RISPOSTE.parent)          # risposte.json, risposte_fr.json, ...
        if not file.exists():
            if log:
                log(f"Manca {file.name}: uso risposte.json (italiano).")
            file = FILE_RISPOSTE
        _dati = json.loads(file.read_text(encoding="utf-8"))
    except Exception as errore:
        if log:
            log(f"Non riesco a leggere risposte.json ({errore}): uso solo le risposte base.")
        _dati = {"utente": "amico", "intenti": [], "vox_da_solo": ["Dimmi."], "conferme": {}, "errori": {}}
    for intento in _dati.get("intenti", []):
        intento["_norm"] = [normalizza(f) for f in intento.get("frasi", [])]
    return len(_dati.get("intenti", [])), nome_utente()


def nome_utente():
    return _dati.get("utente") or "amico"


def collega_modello(funzione):
    """Per il futuro: funzione(frase) -> testo da dire (o None se non sa rispondere)."""
    global _modello_ia
    _modello_ia = funzione


# ----------------------------------------------------------------------
# Modello IA: Groq (pacchetto "groq", chiave nella variabile d'ambiente GROQ_API_KEY)
# ----------------------------------------------------------------------
MODELLO_GROQ = "qwen/qwen3.8-27b"     # modello veloce (mediana circa 0,15-0,25 s al primo testo)
MODELLO_RISERVA = "openai/gpt-oss-120b"   # se il veloce dà errore (es. limite del piano gratuito) si riprova con questo
MESSAGGI_MEMORIA = 8             # ultimi messaggi tenuti (4 scambi domanda/risposta)
LIMITE_TOKEN = 250               # limite di lunghezza della risposta (include il piccolo "ragionamento" del modello)
LIMITE_FRASI = 3                 # Eva dice al massimo tante frasi, anche se il modello ne scrivesse di più
TIMEOUT_GROQ = 15                # secondi di attesa massima (poi Eva dice che Groq non risponde)
ISTRUZIONI_GROQ = (
    "Sei Eva, l'assistente vocale del PC Windows di @NOME@. Rispondi sempre in italiano, in modo breve "
    "(una o due frasi, al massimo 40 parole), naturale e adatto a essere letto ad alta voce. Non usare elenchi, punti elenco, "
    "tabelle, markdown, emoji o simboli. "
    "Eva PUO aprire e chiudere programmi, gestire file e cartelle (cercare, copiare, spostare) e dettare testo "
    "con il tasto F9. Non rispondere mai che non puoi aprire o chiudere programmi o gestire file: questi "
    "comandi li esegue il PC di @NOME@. Se @NOME@ ti chiede di aprire o chiudere qualcosa e la richiesta "
    "arriva a te, rispondi in modo positivo e invitalo a ripetere il comando, per esempio "
    "«Certo, dimmi: apri Chrome» oppure «chiudi Chrome». Per il resto puoi chiacchierare liberamente con @NOME@."
)
COMPAGNA = False                 # impostato da vox_core (dettatura.py: COMPAGNA)
ISTRUZIONI_COMPAGNA = (
    " Sei soprattutto una compagna di conversazione: @NOME@ ti parla in continuazione, anche senza darti ordini. "
    "Rispondi con calore e curiosità, come un'amica, ricorda quello che vi siete detti e ogni tanto fai una domanda "
    "breve per proseguire il discorso. Se la frase sembra non rivolta a te (rumore, TV, parole senza senso) "
    "rispondi con una battuta brevissima o chiedi con gentilezza di ripetere."
)


def istruzioni_groq():
    """Il messaggio di sistema per Groq nella lingua scelta (la lingua della risposta cambia con quella di Eva)."""
    testo = (lingue.groq_chat(ISTRUZIONI_GROQ) + memoria.istruzione_groq()
             + (ISTRUZIONI_COMPAGNA if COMPAGNA else ""))
    return testo.replace("@NOME@", nome_utente())     # il nome di chi usa Eva (schermata iniziale)


def parametri_modello(modello):
    """I modelli gpt-oss "ragionano": per risposte veloci si chiede il minimo. Gli altri non hanno il parametro."""
    return {"reasoning_effort": "low"} if "gpt-oss" in modello else {}


_cronologia = deque(maxlen=MESSAGGI_MEMORIA)
_client_groq = None


def _nuovo_client():
    """Il client Groq (la chiave si legge dalla variabile d'ambiente GROQ_API_KEY)."""
    from groq import Groq
    return Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=TIMEOUT_GROQ, max_retries=1)


def _modello_groq(frase):
    """Chiede una risposta a Groq. Restituisce None se manca il pacchetto, la chiave o c'è un errore."""
    global _client_groq
    if _client_groq is None:
        chiave = os.environ.get("GROQ_API_KEY")
        if not chiave:
            logging.getLogger("eva").error("GROQ_API_KEY non trovata: niente chiacchiere con Groq")
            return None
        try:
            _client_groq = _nuovo_client()
        except Exception:
            logging.getLogger("eva").exception("Non riesco a creare il client Groq")
            return None
    messaggi = [{"role": "system", "content": istruzioni_groq()}, *memoria.messaggi_groq(),
                {"role": "user", "content": frase}]
    try:
        modello = MODELLO_GROQ
        esito = _client_groq.chat.completions.create(model=modello, messages=messaggi,
                                                     max_completion_tokens=LIMITE_TOKEN,
                                                     **parametri_modello(modello))
        testo = (esito.choices[0].message.content or "").strip()
    except Exception:
        logging.getLogger("eva").exception("Chiamata a Groq fallita (rete, chiave o limite del piano gratuito)")
        return None
    return testo or None                        # (lo scambio entra in memoria.py da vox_core)


collega_modello(_modello_groq)


def _modello_groq_pronto():
    """Crea in anticipo il client Groq (chiamata dal thread di avvio)."""
    global _client_groq
    if _client_groq is None:
        _client_groq = _nuovo_client()


_FINE_FRASE = re.compile(r"(?<=[.!?…])\s+")
_PAUSA = re.compile(r"(?<=[,;:])\s+")      # frase lunga: la voce parte già alla prima virgola (dopo almeno 45 caratteri)
SOGLIA_PAUSA = 45
_SIGLE = ("sig.", "dott.", "prof.", "ecc.", "es.", "ing.", "avv.")


def frasi_da_stream(pezzi, max_frasi=None):
    """Riceve i pezzetti di testo man mano che arrivano e restituisce una FRASE alla volta, appena è completa
    (così la voce può partire dalla prima frase senza aspettare il resto). Si ferma dopo max_frasi."""
    max_frasi = max_frasi or LIMITE_FRASI
    buffer, date = "", 0
    for pezzo in pezzi:
        buffer += pezzo
        while True:
            m = _FINE_FRASE.search(buffer)
            if not m:
                m = next((x for x in _PAUSA.finditer(buffer) if x.start() >= SOGLIA_PAUSA), None)
            if not m:
                break
            frase, buffer = buffer[:m.start()].strip(), buffer[m.end():]
            if frase.lower().endswith(_SIGLE) or len(frase) < 12:      # "Sig." o frase troppo corta: la unisco
                buffer = frase + " " + buffer
                break
            if frase:
                yield frase
                date += 1
                if date >= max_frasi:
                    return
    resto = buffer.strip()
    if resto and date < max_frasi:
        yield resto


def chat_stream(frase):
    """Risposta di Groq IN STREAMING: restituisce i pezzetti di testo appena arrivano. Se manca la chiave,
    internet o Groq dà errore, scrive l'errore nel log e non restituisce niente (il chiamante usa il ripiego).
    Alla fine ricorda lo scambio nella cronologia."""
    global _client_groq
    log = logging.getLogger("eva")
    chiave = os.environ.get("GROQ_API_KEY")
    if not chiave:
        log.error("GROQ_API_KEY non trovata: niente chiacchiere con Groq")
        return
    try:
        if _client_groq is None:
            _client_groq = _nuovo_client()
        testo = ""
        elenco = (MODELLO_GROQ, MODELLO_RISERVA)
        for modello in elenco:
            try:
                flusso = _client_groq.chat.completions.create(
                    model=modello, stream=True, max_completion_tokens=LIMITE_TOKEN, **parametri_modello(modello),
                    messages=[{"role": "system", "content": istruzioni_groq()}, *memoria.messaggi_groq(),
                              {"role": "user", "content": frase}])
                for blocco in flusso:
                    pezzo = blocco.choices[0].delta.content if blocco.choices else None
                    if pezzo:
                        testo += pezzo
                        yield pezzo
                break
            except GeneratorExit:
                raise
            except Exception:
                if testo or modello == elenco[-1]:       # già parlato o anche la riserva fallisce
                    raise
                log.exception(f"Groq ({modello}) non risponde: riprovo con {MODELLO_RISERVA}")
    except GeneratorExit:
        raise
    except Exception:
        log.exception("Chiamata a Groq in streaming fallita (rete, chiave o limite del piano gratuito)")
        return
    # (lo scambio entra in memoria.py da vox_core, solo in RAM)


def frase_da_groq(istruzione):
    """Una richiesta singola a Groq (senza memoria della chat), per es. dire a voce i dati del meteo.
    Restituisce il testo oppure None (manca la chiave, niente internet, errore): il chiamante ha un ripiego."""
    global _client_groq
    chiave = os.environ.get("GROQ_API_KEY")
    if not chiave:
        return None
    try:
        if _client_groq is None:
            _client_groq = _nuovo_client()
        modello = MODELLO_GROQ
        esito = _client_groq.chat.completions.create(
            model=modello, max_completion_tokens=LIMITE_TOKEN, **parametri_modello(modello),
            messages=[{"role": "system", "content": istruzioni_groq()}, {"role": "user", "content": istruzione}])
        return (esito.choices[0].message.content or "").strip() or None
    except Exception:
        logging.getLogger("eva").exception("Groq non ha risposto (frase singola)")
        return None


def _scegli(gruppo, varianti, **campi):
    """Sceglie una variante a caso, evitando di ripetere subito l'ultima."""
    if not varianti:
        return ""
    opzioni = [v for v in varianti if v != _ultima.get(gruppo)] or varianti
    scelta = random.choice(opzioni)
    _ultima[gruppo] = scelta
    campi.setdefault("nome", nome_utente())
    try:
        return scelta.format(**campi)
    except (KeyError, IndexError):
        return scelta


def frase_ora(adesso=None):
    altra = lingue.frase_ora(adesso)
    if altra:
        return altra
    adesso = adesso or datetime.datetime.now()
    h, m = adesso.hour, adesso.minute
    if h == 0 and m == 0:
        return "È mezzanotte"
    if h == 12 and m == 0:
        return "È mezzogiorno"
    if m == 0:
        return f"Sono le {h} in punto" if h != 1 else "È l'una in punto"
    if h == 1:
        return f"È l'una e {m}"
    if h == 0:
        return f"È mezzanotte e {m}"
    return f"Sono le {h} e {m}"


def saluto_per_ora(adesso=None):
    altro = lingue.saluto_per_ora(adesso)
    if altro:
        return altro
    h = (adesso or datetime.datetime.now()).hour
    if 5 <= h < 13:
        return "Buongiorno"
    if 13 <= h < 18:
        return "Buon pomeriggio"
    if 18 <= h < 22:
        return "Buonasera"
    return "Ciao"                       # di notte: "Buongiorno" suonerebbe strano


def frase_data(oggi=None):
    altra = lingue.frase_data(oggi)
    if altra:
        return altra
    oggi = oggi or datetime.date.today()
    primo = "primo" if oggi.day == 1 else str(oggi.day)
    return f"{GIORNI[oggi.weekday()]} {primo} {MESI[oggi.month - 1]} {oggi.year}"


# ----------------------------------------------------------------------
# Ora e data a parole chiave: regge gli errori del riconoscimento vocale ("che ora sono", "chi ora sono")
# e le parole in più prima del nome ("Poi riprovo, Eva che ore sono"). Solo in italiano.
# ----------------------------------------------------------------------
_CHIEDE = {"che", "chi", "quale", "qual", "dimmi", "dimmela", "dici", "dirmi", "dire", "sai", "saresti", "sapere"}
# parole che indicano un'altra richiesta ("tra un'ora", "a che ora ricordami", "sveglia"...): meglio non rispondere
_ALTRO = {"a", "alle", "all", "tra", "fra", "per", "dopo", "prima", "ogni", "timer", "sveglia", "promemoria",
          "ricordami", "ricordarmi", "ricorda", "allarme", "scadenza", "compleanno", "festa", "natale", "pasqua",
          "evento", "appuntamento", "buon", "buona", "buongiorno"}
_ALTRO_ORA = _ALTRO | {"di", "del", "della", "quante", "quanto", "quanti", "mezza", "un", "una", "due", "tre"}
_SCARTO_DATA = {"a", "alle", "all"}                     # per la data queste parole non contano


def _richiesta_ora_data(frase, varianti_wake=()):
    """('ora'|'data', spostamento_in_giorni) se la frase chiede l'ora o la data di oggi/ieri/domani, altrimenti None."""
    parole = normalizza(frase.replace("'", " ")).split()
    wake = {normalizza(v) for v in varianti_wake if " " not in normalizza(v)}
    for i in range(len(parole) - 1, -1, -1):             # tengo solo ciò che segue l'ultima chiamata a Eva
        if parole[i] in wake:
            parole = parole[i + 1:] if i < len(parole) - 1 else parole[:i]
            break
    if not parole or len(parole) > 8:
        return None
    insieme = set(parole)
    if insieme & {"ora", "ore"} and insieme & _CHIEDE and not insieme & _ALTRO_ORA:
        return ("ora", 0)
    mesi = {normalizza(m) for m in MESI} | {normalizza(g) for g in GIORNI}
    cifre = any(any(c.isdigit() for c in p) for p in parole)
    chiede_data = ((insieme & {"giorno", "data"} and (insieme & _CHIEDE or insieme & {"oggi", "siamo", "abbiamo", "domani", "ieri", "dopodomani"}))
                   or ("quanti" in insieme and "ne" in insieme and insieme & {"abbiamo", "oggi", "domani", "ieri"}))
    if chiede_data and not insieme & (_ALTRO - _SCARTO_DATA) and not insieme & mesi and not cifre:
        if "dopodomani" in insieme:
            return ("data", 2)
        if "domani" in insieme:
            return ("data", 1)
        if "ieri" in insieme:
            return ("data", -1)
        return ("data", 0)
    return None


def _parla_d_altro(frase):
    """True se la frase contiene un luogo/tempo/data precisi ("a Londra", "il 5 maggio", "tra un'ora"):
    in quel caso l'ora o la data di adesso sarebbe una risposta sbagliata."""
    parole = normalizza(frase.replace("'", " ")).split()
    mesi = {normalizza(m) for m in MESI} | {normalizza(g) for g in GIORNI}
    return bool(set(parole) & (_ALTRO | mesi)) or any(any(c.isdigit() for c in p) for p in parole)


def _risposta_ora_data(tipo, giorni):
    """Legge l'orologio del computer. Per oggi usa i testi di risposte.json (modificabili)."""
    if tipo == "data" and giorni:
        giorno = datetime.date.today() + datetime.timedelta(days=giorni)
        inizio = {2: "Dopodomani sarà", 1: "Domani sarà", -1: "Ieri era"}[giorni]
        return Risposta(f"{inizio} {frase_data(giorno)}.", None, False, "data")
    intento = next((i for i in _dati.get("intenti", []) if i.get("nome") == tipo), None)
    if intento and intento.get("risposte"):
        testo = _scegli("intento:" + tipo, intento["risposte"], ora=frase_ora(), data=frase_data(),
                        saluto_ora=saluto_per_ora())
    else:
        testo = frase_ora() + "." if tipo == "ora" else f"Oggi è {frase_data()}."
    return Risposta(testo, None, False, tipo)


# ----------------------------------------------------------------------
# Riconoscimento della frase (difflib: capisce anche le frasi pronunciate male)
# ----------------------------------------------------------------------
def _pulisci(frase, varianti_wake):
    """Normalizza e toglie la parola "Eva" (e le sue varianti) dalla frase."""
    parole = normalizza(frase.replace("'", " ")).split()
    wake = {normalizza(v) for v in varianti_wake if " " not in normalizza(v)}
    # il nome (anche storpiato) si toglie solo in testa ("eva come stai", "ehi eva ...") o in coda
    # ("arrivederci eva"), mai in mezzo: così parole come "eve" dentro una frase restano
    for idx in (0, 1):
        if idx < len(parole) and parole[idx] in wake and (idx == 0 or parole[0] in
                                                          {"ehi", "ei", "hey", "ok", "okay", "ciao", "salve"}):
            del parole[idx]
            break
    if len(parole) > 1 and parole[-1] in wake:
        parole.pop()
    return " ".join(parole)


def _punteggio(frase, candidata):
    punteggio = difflib.SequenceMatcher(None, frase, candidata).ratio()
    a, b = frase.split(), candidata.split()
    # la frase di riferimento (2+ parole) contenuta nelle parole dette: "sto bene grazie"
    if len(b) >= 2 and len(a) <= len(b) + 3:
        for i in range(len(a) - len(b) + 1):
            if a[i:i + len(b)] == b:
                punteggio = max(punteggio, 0.95)
    return punteggio


def trova_intento(frase, varianti_wake=()):
    """Restituisce l'intento di risposte.json che somiglia di più alla frase, o None."""
    pulita = _pulisci(frase.replace("'", " "), varianti_wake)
    if not pulita:
        return None
    primo = pulita.split()[0]
    if primo in VERBI_COMANDO:
        return None
    migliore, voto = None, 0.0
    for intento in _dati.get("intenti", []):
        for candidata in intento["_norm"]:
            p = _punteggio(pulita, candidata)
            soglia = intento.get("soglia") or (0.88 if len(candidata) <= 6 else 0.8)
            if p >= soglia and (p > voto or (p == voto and migliore and len(candidata) >
                                              len(max(migliore["_norm"], key=len)))):
                migliore, voto = intento, p
    return migliore


# ----------------------------------------------------------------------
# La funzione da collegare in futuro a un'intelligenza artificiale
# ----------------------------------------------------------------------
def rispondi(frase, varianti_wake=()):
    """Risposta a saluti e chiacchiere NOTE (risposte.json). Restituisce Risposta(...) o None."""
    if lingue.corrente == "it":
        richiesta = _richiesta_ora_data(frase, varianti_wake)
        if richiesta:
            return _risposta_ora_data(*richiesta)
    intento = trova_intento(frase, varianti_wake)
    if intento is not None and intento["nome"] in ("ora", "data") and _parla_d_altro(frase):
        intento = None            # "a che ora è la riunione", "che giorno è il 5 maggio": non è l'ora/data di adesso
    if intento is None:
        # "chiudi Chrome", "chiudi finestra"...: NON chiude Eva (solo "chiudi" da solo lo fa)
        parole = _pulisci(frase, varianti_wake).split()
        if len(parole) >= 2 and parole[0] in VERBI_CHIUDI:
            testo = _scegli("chiudi_programmi", _dati.get("fisse", {}).get("chiudi_programmi", []))
            return Risposta(testo or "Chiudere i programmi non lo faccio ancora.", None, False,
                            "chiudi_programmi")
        return None
    testo = _scegli("intento:" + intento["nome"], intento.get("risposte", []),
                    ora=frase_ora(), data=frase_data(), saluto_ora=saluto_per_ora())
    return Risposta(testo, intento.get("azione"), bool(intento.get("continua")), intento["nome"])


def rispondi_libero(frase, varianti_wake=()):
    """PUNTO UNICO per le conversazioni libere. Restituisce SEMPRE una Risposta.

    Oggi usa solo le risposte scritte:
      1) le chiacchiere note di risposte.json;
      2) altrimenti una frase utile ("Questo non lo so ancora fare...", chiave "non_so").

    IN FUTURO qui si collega l'API di un modello di intelligenza artificiale:
    basta chiamare collega_modello(funzione) (oppure scrivere la chiamata al posto
    del punto segnato sotto). Il resto di Eva non cambia.
    """
    nota = rispondi(frase, varianti_wake)
    if nota is not None:
        return nota
    if _modello_ia is not None:                       # <-- QUI andrà la chiamata all'API
        try:
            testo = _modello_ia(frase)
        except Exception:
            testo = None
        if testo:
            return Risposta(str(testo), None, True, "modello")
    testo = _scegli("non_so", _dati.get("non_so", []))
    return Risposta(testo or "Questo non lo so ancora fare. Dimmi aiuto per l'elenco.",
                    None, False, "non_so")


# ----------------------------------------------------------------------
# Chiusura: "chiudi", "arrivederci", "basta"... e "spegni Eva"
# ----------------------------------------------------------------------
# Elenco di riserva (quello vero è FRASI_CHIUSURA in dettatura.py, che lo passa con configura_chiusura)
FRASI_CHIUSURA_PREDEFINITE = ["chiudi", "chiuditi", "vai via", "basta", "arrivederci", "ciao ciao", "a dopo",
                              "esci", "puoi andare", "riposo"]
VERBI_PROGRAMMA = {"chiudi", "chiudere", "chiudo", "chiude", "chiudete", "chiudimi", "termina", "terminare",
                   "killa", "spegni", "spegnere", "uccidi"}
RIEMPITIVI_CHIUSURA = {"pure", "per", "favore", "piacere", "ora", "adesso", "dai", "allora", "ecco", "poi", "eh",
                       "ehi", "ei", "hey"}
VERBI_ESCI_TUTTO = {"esci", "uscire", "chiudi", "chiuditi", "chiudere", "termina", "terminare", "spegni",
                    "spegniti", "spegnere", "ferma", "fermati", "fermare"}
DEL_TUTTO = ("del tutto", "completamente", "definitivamente", "per sempre", "totalmente")
SOLO_ESATTE = {"vai", "stop", "esci", "uscire", "riposo", "addio", "chiusi", "qd", "q d", "esce", "fine"}
# verbi che iniziano un comando vero: "vai su youtube", "cerca basta" NON sono una chiusura
INIZI_COMANDO = {"apri", "apre", "aprire", "apro", "aprimi", "avvia", "lancia", "mostra", "mostrami", "cerca",
                 "cercami", "trova", "scrivi", "scrivimi", "alza", "abbassa", "volume", "metti", "togli", "impara",
                 "chiama", "prepara", "manda", "invia", "portami", "andiamo", "naviga", "vedere", "vedi"}
Chiusura = namedtuple("Chiusura", "tipo frase motivo")     # tipo: "chiudi" | "esci_tutto" | "programma" | "no"
_frasi_chiusura = list(FRASI_CHIUSURA_PREDEFINITE)


def configura_chiusura(frasi):
    global _frasi_chiusura
    if frasi:
        _frasi_chiusura = list(frasi)


def _tokens(testo):
    """Minuscolo, senza accenti né punteggiatura: "Eva, chiudi." -> ["eva", "chiudi"]."""
    t = unicodedata.normalize("NFD", testo.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).split()


def analizza_chiusura(frase, varianti_nome=(), frasi_chiusura=None):
    """Decide se la frase chiude Eva. Restituisce Chiusura(tipo, frase_pulita, motivo):
      "esci_tutto": "spegni Eva", "esci del tutto" (ferma il programma);
      "chiudi"   : una frase di chiusura (fino a 4 parole, anche "contiene"): saluta e chiude la finestra;
      "programma": "chiudi Chrome", "chiudi la finestra"... NON chiude Eva;
      "no"       : non è una chiusura.
    """
    frasi = [" ".join(_tokens(f)) for f in (frasi_chiusura or _frasi_chiusura)]
    frasi = [f for f in frasi if f]
    # solo le varianti di UNA parola (eva, eba, ieva...): le altre ("ciao eva", "e va") contengono parole normali
    nomi = {_tokens(v)[0] for v in varianti_nome if len(_tokens(v)) == 1 and len(_tokens(v)[0]) >= 2}
    nomi |= {"eva", "vox"}
    originali = _tokens(frase)
    if not originali:
        return Chiusura("no", "", "frase vuota")

    # "spegni Eva" / "esci del tutto": ferma tutto il programma (prima di togliere il nome)
    testo_unito = " ".join(originali)
    idx_nome = [i for i, t in enumerate(originali) if t in nomi]
    for i, t in enumerate(originali):
        if t in {"spegni", "spegnere", "spegnimi"} and any(j > i for j in idx_nome):
            return Chiusura("esci_tutto", testo_unito, f"'{t}' seguito dal nome")
        if t in VERBI_ESCI_TUTTO and any(d in testo_unito for d in DEL_TUTTO):
            return Chiusura("esci_tutto", testo_unito, f"'{t}' ... 'del tutto'")

    # via il nome (anche in mezzo) e i "ehi / ei / hey" iniziali: "Eva, chiudi." -> "chiudi"
    tok = [t for t in originali if t not in nomi]
    while tok and tok[0] in {"ehi", "ei", "hey"}:
        tok = tok[1:]
    if not tok:
        return Chiusura("no", "", "solo il nome")
    s = " ".join(tok)
    vocabolario = {t for f in frasi for t in f.split()}

    # "chiudi Chrome", "chiudi la finestra": un programma, NON Eva
    prog = [t for t in tok if t in VERBI_PROGRAMMA]
    if prog:
        altro = [t for t in tok if t not in VERBI_PROGRAMMA and t not in vocabolario and t not in RIEMPITIVI_CHIUSURA]
        if altro:
            return Chiusura("programma", s, f"'{prog[0]}' seguito da altro ({' '.join(altro)})")

    if tok[0] in INIZI_COMANDO or (tok[0] == "vai" and len(tok) > 1 and tok[1] != "via"):
        return Chiusura("no", s, f"inizia come un comando ('{tok[0]}')")
    if len(tok) > 4:
        return Chiusura("no", s, f"troppo lunga ({len(tok)} parole)")

    # 1) tutta la frase somiglia a una frase di chiusura (difflib)
    for f in frasi:
        # parole corte: quasi esatte; frasi di più parole: 0.86 (così "grazie" non somiglia a "ok grazie")
        soglia = 0.88 if len(f) <= 5 else (0.86 if " " in f else 0.8)
        r = difflib.SequenceMatcher(None, s, f).ratio()
        if r >= soglia:
            return Chiusura("chiudi", s, f"somiglia a '{f}' ({r:.2f})")
    # 2) la contiene (parole vicine, nello stesso ordine)
    for f in frasi:
        if f in SOLO_ESATTE:
            continue
        ft = f.split()
        for i in range(len(tok) - len(ft) + 1):
            if tok[i:i + len(ft)] == ft:
                return Chiusura("chiudi", s, f"contiene '{f}'")
    # 3) una parola molto simile a una parola di chiusura lunga ("arrivedercci")
    for f in frasi:
        if " " in f or len(f) < 6 or f in SOLO_ESATTE:
            continue
        for t in tok:
            if len(t) >= 5 and difflib.SequenceMatcher(None, t, f).ratio() >= 0.85:
                return Chiusura("chiudi", s, f"'{t}' somiglia a '{f}'")
    return Chiusura("no", s, "nessuna frase di chiusura")


# ----------------------------------------------------------------------
# Posizione della sfera: "mettiti da parte", "mettiti al centro", "nasconditi", "sposta in alto a sinistra"
# ----------------------------------------------------------------------
COMANDI_VISTA = {
    "ridotta": ["mettiti da parte", "abbassati", "rimpicciolisci", "rimpicciolisciti", "fatti piccola", "fatti piccolo",
                "fatti piu piccola", "mettiti in disparte", "riduciti", "stai da parte"],
    "grande": ["mettiti al centro", "ingrandisci", "ingrandisciti", "vieni qui", "vieni al centro", "fatti grande",
               "torna al centro", "vieni al centro dello schermo"],
    "nascosta": ["nasconditi", "nascondi", "mettiti nascosta", "sparisci"],
}
VERBI_SPOSTA = {"sposta", "spostati", "spostare", "mettiti", "metti", "portati", "muoviti"}      # NON "vai" ("vai su" = cartella superiore)
PAROLE_POSIZIONE = {"in", "a", "su", "giu", "la", "il", "nell", "nel", "angolo", "di", "verso", "dello", "schermo",
                    "un", "po", "piu", "ancora", "parte", "lato"}
Vista = namedtuple("Vista", "azione orizzontale verticale frase")


def _vicina(parola, esatta, soglia=0.8):
    return parola == esatta or difflib.SequenceMatcher(None, parola, esatta).ratio() >= soglia


def comando_vista(frase, varianti_nome=()):
    """Riconosce i comandi che cambiano l'aspetto della sfera (con difflib). Restituisce
    Vista(azione, orizzontale, verticale, frase) oppure None.
      azione: "ridotta" | "grande" | "nascosta" | "angolo"; per "angolo" orizzontale/verticale dicono dove."""
    nomi = {_tokens(v)[0] for v in varianti_nome if len(_tokens(v)) == 1 and len(_tokens(v)[0]) >= 2} | {"eva", "vox"}
    tok = [t for t in _tokens(frase) if t not in nomi]
    if not tok or len(tok) > 7:
        return None
    s = " ".join(tok)
    for azione, frasi in COMANDI_VISTA.items():
        for f in frasi:
            if difflib.SequenceMatcher(None, s, f).ratio() >= (0.88 if " " not in f else 0.85):
                return Vista(azione, None, None, s)
    if any(_vicina(tok[0], v, 0.85) for v in VERBI_SPOSTA):
        resto = tok[1:]
        h = v = None
        riconosciute = 0
        for t in resto:
            if _vicina(t, "sinistra"):
                h = "sinistra"
            elif _vicina(t, "destra"):
                h = "destra"
            elif t in ("alto", "su") or _vicina(t, "alto", 0.85):
                v = "alto"
            elif t in ("basso", "giu") or _vicina(t, "basso", 0.85):
                v = "basso"
            elif t in PAROLE_POSIZIONE:
                continue
            else:
                return None                  # una parola che non è una direzione: non è per la sfera ("sposta lettera in alto")
            riconosciute += 1
        if h or v:
            return Vista("angolo", h, v, s)
    return None


def saluto_chiusura(frase=""):
    """Saluto breve per quando Eva si chiude ("A dopo, <nome>.")."""
    notte = any(t in {"buonanotte", "notte"} for t in _tokens(frase)) or "buona notte" in " ".join(_tokens(frase))
    nome = "buonanotte" if notte else "arrivederci"
    for intento in _dati.get("intenti", []):
        if intento.get("nome") == nome and intento.get("risposte"):
            return _scegli("chiusura:" + nome, intento["risposte"])
    return f"A dopo, {nome_utente()}."


def testo_fisso(chiave, predefinito="", **campi):
    """Frase fissa da risposte.json (sezione "fisse"), ad esempio "timeout". I segnaposto
    {nome} (il nome di chi usa Eva) e quelli passati in campi ({cosa}, {elenco}...) vengono riempiti."""
    if _dati.get("fisse", {}).get(chiave):
        return _scegli("fissa:" + chiave, _dati["fisse"][chiave], **campi)
    campi.setdefault("nome", nome_utente())
    try:
        return predefinito.format(**campi)
    except (KeyError, IndexError):
        return predefinito


def saluto_vox_da_solo():
    return _scegli("vox_da_solo", _dati.get("vox_da_solo", ["Dimmi."]))


def frase_errore(tipo, **campi):
    return _scegli("errore:" + tipo, _dati.get("errori", {}).get(tipo, []), **campi) or \
        {"non_trovato": "Non ho trovato questo.", "non_capito": "Non ho capito, puoi ripetere?"}.get(tipo, "")


# ----------------------------------------------------------------------
# Conferma a voce dopo un comando eseguito
# ----------------------------------------------------------------------
def _bello(nome):
    nome = nome.strip()
    altro = lingue.nome_visibile(nome)           # "blocco note" -> "Notepad" / "le Bloc-notes" / "el Bloc de notas"
    if altro:
        return altro
    return NOMI_BELLI.get(nome.lower(), nome[:1].upper() + nome[1:])


def conferma(risultato):
    """Frase breve da dire dopo un comando: "Apro Chrome.", "Volume alzato."..."""
    c = _dati.get("conferme", {})
    corretta = normalizza(risultato.frase_corretta or "")
    parole = corretta.split()
    primo = parole[0] if parole else ""
    if primo in {"apri", "avvia", "lancia", "vai"}:
        resto = " ".join(parole[1:])
        resto = re.sub(r"^(su|a|in)\s+", "", resto)
        return _scegli("c:apri", c.get("apri", ["Apro {cosa}."]), cosa=_bello(resto))
    if "mail" in parole or "email" in parole or "bozza" in parole:
        return _scegli("c:mail", c.get("mail", ["Bozza pronta."]))
    if primo == "cerca":
        cosa = re.sub(r"^cerca\s+(su\s+)?(google|youtube)\s*", "", corretta).strip() or "quello che hai detto"
        return _scegli("c:cerca", c.get("cerca", ["Cerco {cosa}."]), cosa=cosa)
    if primo == "impara":
        return _scegli("c:impara", c.get("impara", ["Fatto."]))
    if "muto" in parole or "audio" in parole:
        chiave = "togli_muto" if ({"togli", "riattiva", "attiva", "disattiva"} & set(parole)) else "muto"
        return _scegli("c:" + chiave, c.get(chiave, ["Fatto."]))
    if "volume" in parole or "alza" in parole or "abbassa" in parole:
        numero = re.search(r"\d+", corretta)
        if numero:
            return _scegli("c:vl", c.get("volume_livello", ["Volume al {livello} per cento."]),
                           livello=numero.group())
        if "alza" in parole or "aumenta" in parole:
            return _scegli("c:vs", c.get("volume_su", ["Volume alzato."]))
        if "abbassa" in parole or "diminuisci" in parole:
            return _scegli("c:vg", c.get("volume_giu", ["Volume abbassato."]))
    return _scegli("c:altro", c.get("altro", ["Fatto."]))


def risposta_a_risultato(risultato):
    """Frase da dire per qualsiasi Risultato di azioni.interpreta().
    Restituisce (testo, azione_riuscita)."""
    tipo = risultato.tipo
    detto = getattr(risultato, "detto", "")          # cartelle e file (vox_file.py) hanno la loro frase
    if detto:
        return lingue.rendi(detto), tipo in ("eseguito", "scelta")
    if tipo == "non_trovato" and risultato.messaggio.startswith("Non ho trovato"):
        # dico cosa ho capito (non un semplice "non ho trovato")
        capito = (risultato.frase_corretta or "").strip()
        return (testo_fisso("non_trovato_capito", "Ho capito: {capito}. Non l'ho trovato.", capito=capito)
                if capito else frase_errore("non_trovato")), False
    if tipo == "eseguito":
        return conferma(risultato), True
    if tipo == "non_trovato":
        if risultato.messaggio.startswith("Non ho trovato"):
            return frase_errore("non_trovato"), False
        return frase_errore("non_capito"), False
    if tipo == "incerto":
        return frase_errore("incerto", cosa=risultato.frase_corretta), False
    return frase_errore("non_capito"), False
