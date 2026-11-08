"""Eva - assistente vocale.

Come si usa:
  - dì "Eva" (poi il comando) oppure tieni premuto F9 e parla
    (fino a quando ACCETTA_VECCHIO_NOME è True risponde anche a "Vox");
  - la finestra compare, esegue il comando e sparisce;
  - l'icona accanto all'orologio ha "Attiva ora" ed "Esci";
  - Eva risponde a voce (Elsa) e fa due chiacchiere: risposte in risposte.json.

Prova da PowerShell:   python -u dettatura.py
Senza finestra nera:   pythonw dettatura.py   (o avvia_eva.bat)
Gli errori e le frasi riconosciute si salvano in eva.log (stessa cartella).
"""
import logging
import logging.handlers
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import eva_percorsi as percorsi
VERSIONE = "2.2"           # versione di Eva (si scrive in eva_log.txt a ogni avvio)

T0 = time.perf_counter()      # per misurare i tempi di avvio (scritti in eva.log come "TEMPO avvio")

# ----------------------------------------------------------------------
# IMPOSTAZIONI (facili da modificare)
# ----------------------------------------------------------------------
# --- Lingua: it (italiano), fr (francese), es (spagnolo), en (inglese) ---
# Cambiano insieme: trascrizione del microfono, voce di Windows, comandi (apri/chiudi...), risposte fisse e Groq.
# A voce: "Eva, parla francese" / "speak English" / "habla español" / "parla italiano". La scelta fatta a voce si salva
# in impostazioni_eva.json (chiave "lingua") e VALE AL POSTO di questa; per tornare a questa, cancella quella chiave.
LINGUA = "it"
VOCI_LINGUE = {"it": "Elsa", "fr": "Hortense", "es": "Helena", "en": "Zira"}     # voci di Windows (anche solo una parte del nome)

# --- MODALITÀ COMPAGNA (l'unico interruttore per tornare a com'era prima) ---
# True  = Eva ascolta SEMPRE, senza F9 e senza dire "Eva" ogni volta: la conversazione è continua, ricorda quello
#         che è stato detto ("aprila" dopo una ricerca su YouTube), e Groq decide se una frase è un comando o una
#         chiacchierata (se non capisce bene chiede con naturalezza: "Scusa <nome>, forse intendevi...").
#         "chiudi/basta/ciao" la fanno solo tacere un momento: per fermarla del tutto di' "spegni Eva".
# False = come prima: sta a riposo, si sveglia con la parola "Eva" o con F9, e i comandi fissi hanno la precedenza.
COMPAGNA = True
COMPAGNA_CONFIDENZA_MIN = -0.7   # in compagna: una frase con sicurezza di trascrizione più bassa (es. -0.9) viene scartata
                                # senza rispondere (tv, rumore, parole storpiate). Più vicino a 0 = più severo; -5 = non scarta mai.
                                # Se dici "Eva" all'inizio, o Eva aspetta un tuo sì/no, il filtro non si applica.
COMPAGNA_IGNORA_AUDIO = True    # in compagna: mentre Eva parla o il PC sta riproducendo audio (video, musica) NON ascolta.
                                # Per darle un ordine in quel momento tieni premuto F9. False = ascolta anche allora.

WAKE_WORD = True          # True = ascolta anche "Eva"; False = solo il tasto F9
TASTO = "f9"              # tieni premuto per parlare
MODELLO = "medium"        # modello preciso (tiny < small < medium)
MODELLO_WAKE = "small"    # modello veloce per sentire "Eva" (se uguale a MODELLO ne usa uno solo)
CONVERSAZIONE_CONTINUA = True   # True = dopo "Eva" resta aperto e ascolta un comando dopo l'altro
                                # (si chiude con "chiudi", "basta", "arrivederci"...). False = come prima:
                                # un comando e la finestra sparisce
TIMEOUT_CONVERSAZIONE = 300     # secondi di silenzio dopo i quali la conversazione finisce da sola (300 = 5 minuti)
SECONDI_SILENZIO = 6      # (solo senza conversazione continua) dopo "Eva", se non parli per tanti secondi torna a riposo
TRASCRIZIONE_VELOCE_PRIMA = True  # True = prima la trascrizione veloce; il modello preciso solo se la veloce non basta
CONFIDENZA_VELOCE_MIN = -0.6      # sicurezza minima (avg_logprob) per fidarsi della veloce: più alto (es. -0.4) = più severo
SOGLIA_VOCE_MIN = 0.006   # volume minimo per considerare "voce" (più basso = più sensibile)

# --- Frasi che CHIUDONO Eva (saluta, la finestra sparisce, torna a riposo) ---
# Una frase breve (fino a 4 parole) che somiglia a una di queste, o che la contiene, chiude Eva.
# "chiudi" da solo chiude Eva. "chiudi il browser" / "chiudi Chrome" / "chiudi Google" / "chiudi la cartella" /
# "chiudi finestra" NON chiudono Eva: chiudono solo quella finestra (vedi azioni.py, chiudi_finestra).
# Aggiungi qui le storpiature che vedi nel terminale quando dici "chiudi" e Eva non si chiude
# (nel tuo log "chiudi" è stato sentito come "QD", "Esce", "Termina", "Finisci").
FRASI_CHIUSURA = [
    "chiudi", "chiuditi", "chiusi", "chiudere", "chiudi pure", "chiudi tutto", "vai via", "vai", "basta",
    "basta così", "va bene così", "è tutto", "ho finito", "grazie basta", "ok grazie", "grazie ciao",
    "a dopo", "a più tardi", "a presto", "arrivederci", "arrivederla", "ciao ciao", "addio", "buonanotte",
    "esci", "uscire", "puoi andare", "riposo", "riposati", "spegniti", "stop",
    # storpiature ricorrenti nel tuo log:
    "qd", "esce", "termina", "finisci",
]
# Per FERMARE DEL TUTTO il programma si dice: "spegni Eva" oppure "esci del tutto".

# --- Ripiego con Groq: se i comandi fissi non capiscono la frase, Groq la traduce in un'azione ---
# (apri, apri cartella, chiudi, cerca, volume, copia, sposta, incolla). Esegue Eva con le funzioni di sempre.
# I comandi semplici restano locali (veloci, senza internet). Serve GROQ_API_KEY, la stessa della chat.
# Senza internet o se Groq non risponde, Eva lo dice a voce.

GROQ_AZIONI = True             # False = come prima: niente ripiego, risponde solo con le frasi fisse
GROQ_AZIONI_TIMEOUT = 8         # secondi di attesa massima per Groq (poi dice che non risponde)

# --- Aspetto della sfera: dopo aver aperto qualcosa si rimpicciolisce e va in un angolo ---
SPARISCI_DOPO_COMANDO = True    # True = dopo un comando che apre o cambia qualcosa (apri, cerca, chiudi finestra, volume...) Eva SPARISCE
                                # del tutto e torna a riposo: si richiama con "Eva" o con F9. Dopo una semplice risposta
                                # (meteo, domanda...) sparisce da sola dopo SECONDI_PRIMA_DI_SPARIRE. False = come prima
                                # (sfera ridotta in un angolo, resta in ascolto)
SECONDI_PRIMA_DI_SPARIRE = 5    # secondi di silenzio dopo una risposta prima di sparire (se parli prima, resta)
RIDUCI_DOPO_APERTURA = True     # (vale solo con SPARISCI_DOPO_COMANDO = False) False = come prima: la sfera resta sempre grande al centro (i comandi
                                # vocali "mettiti da parte", "mettiti al centro"... funzionano comunque)
ANGOLO_PREDEFINITO = "basso_destra"   # basso_destra | basso_sinistra | alto_destra | alto_sinistra
                                # (se trascini la sfera, o dici "sposta in alto a sinistra", la scelta viene
                                # salvata in impostazioni_eva.json e vale al posto di questa)
OPACITA_RIDOTTA = 0.55          # trasparenza della sfera ridotta (1 = piena, 0.3 = quasi invisibile)
SCALA_RIDOTTA = 0.25            # grandezza della sfera ridotta rispetto a quella grande (0.25 = un quarto)
MARGINE = 24                    # pixel di distanza dal bordo dello schermo (e dalla barra delle applicazioni)

# --- Copia, taglia e sposta file e cartelle (SEMPRE con conferma a voce) ---
# Regole: mai sovrascrivere (crea "nome - copia"), mai cancellare, cartelle di sistema e file di Eva protetti.
OPERAZIONI_FILE = True          # False = copia e sposta spenti ("Le operazioni sui file sono spente.")
                                # A voce: "modalità sola lettura" li spegne, "modalità normale" li riaccende
OPERAZIONI_LIMITE_GB = 5        # oltre questi gigabyte serve una seconda conferma
OPERAZIONI_LIMITE_FILE = 5000   # oltre questo numero di file serve una seconda conferma
OPERAZIONI_CONFERMA_SECONDI = 15  # tempo per rispondere "sì"; poi Eva dice "Annullato." e non fa niente

# --- Cartelle e file (Eva li sfoglia a voce) ---
PROFONDITA_INDICE = 6           # quanti livelli di sottocartelle indicizzare
MAX_CARTELLE_INDICE = 40000     # massimo di cartelle nell'indice
MAX_FILE_INDICE = 80000         # massimo di file nell'indice
AGGIORNA_INDICE_MINUTI = 10     # l'indice si aggiorna da solo ogni tanti minuti (o dì "aggiorna l'indice")
SOGLIA_CARTELLE = 0.78          # somiglianza minima (0..1) tra quello che dici e il nome: più alto = più severo
TEMPO_RICERCA_DISCO = 5         # secondi al massimo per la ricerca di riserva sul disco
NAV_VALIDITA_SECONDI = 600      # per quanto tempo l'"ultima cartella aperta" ha la precedenza su "apri <nome>"
SUGGERISCI_NOMI_CARTELLE = True # aiuta il riconoscimento con i nomi delle cartelle del Desktop

# --- Contro le frasi inventate da Whisper (silenzio o rumore) ---
NO_SPEECH_MAX = 0.6       # scarta i pezzi con probabilità di "non è parlato" sopra questo (0..1). Più basso = più severo
LOGPROB_MIN = -1.0        # scarta i pezzi con punteggio medio sotto questo. Più alto (es. -0.7) = più severo
VAD_SOGLIA = 0.5          # filtro delle pause: più alto = ignora più rumore (0..1)
VAD_SILENZIO_MS = 500     # pausa (in millisecondi) che separa due pezzi di parlato
# Se la trascrizione CONTIENE una di queste frasi viene ignorata (maiuscole e accenti non contano).
# Aggiungi qui le nuove frasi false che vedi nel terminale.
FRASI_FALSE = [
    "Sottotitoli creati dalla comunità", "Sottotitoli a cura di", "Sottotitoli e revisione",
    "Amara.org", "Pouser di Google", "Grazie a tutti", "Iscriviti al canale",
    "Alla prossima", "Grazie per la visione", "Grazie per aver guardato",
    "Mettete like", "Non dimenticate di iscrivervi",
]   # attenzione: parole comuni (es. "musica") bloccherebbero anche comandi veri come "cerca musica"

# --- Voce di Eva (risponde a voce) ---
VOCE_ATTIVA = True        # False = Eva non parla (resta tutto il resto). A voce: "stai zitta" / "parla"
VOCE_NOME = "Elsa"        # nome (o parte del nome) della voce italiana di Windows
VOCE_VELOCITA = 0         # da -10 (lenta) a 10 (veloce); 0 = normale
VOCE_VOLUME = 100         # da 0 a 100
VOCE_PAUSA_DOPO = 0.5     # secondi in cui il microfono resta "sordo" dopo che Eva ha parlato
NOME_UTENTE = ""          # come ti chiama Eva: lo scrivi nella schermata iniziale (vox_inizio.py), non qui

# Parole che il modello deve "aspettarsi": aiutano a riconoscere i comandi.
PAROLE_SUGGERITE = [
    "Eva", "apri", "avvia", "lancia", "vai su",
    "Chrome", "YouTube", "Gmail", "Blocco note", "Documenti", "Scaricati", "Desktop",
    "alza il volume", "abbassa il volume", "muto", "cerca", "scrivi una mail", "dice", "oggetto",
    "Outlook", "Posta", "Calendario", "WhatsApp", "impara",
    "cartella", "file", "dentro", "torna indietro", "aggiorna l'indice",
    "copia", "sposta", "taglia", "incolla", "chiavetta", "sì", "conferma", "annulla",
    "mettiti da parte", "mettiti al centro", "nasconditi",
    "ricordami", "promemoria", "chiudi", "basta", "arrivederci", "spegni Eva",       # aiutano a sentire bene le parole di chiusura
]
# --- Il nome: come può essere sentita la parola "Eva" con il tuo accento ---
# Aggiungi qui le varianti che vedi nel terminale o in eva.log ("Parola di attivazione sentita: ...").
# Eva si attiva SOLO se la frase INIZIA con una di queste (o se dici il nome da solo), mai in mezzo a una frase.
VARIANTI_NOME = ["eva", "eba", "ieva", "ewa", "ava", "eve", "eha", "evo", "e va", "ehi eva", "ciao eva", "ehi va"]
ACCETTA_VECCHIO_NOME = True     # True = rispondo anche al vecchio nome "Vox" (metti False per spegnerlo)
VARIANTI_VECCHIO_NOME = ["vox", "box", "vocs", "vos", "voks", "bocs", "vogs", "vocks", "voz", "boks"]
WAKE_VARIANTI = VARIANTI_NOME + (VARIANTI_VECCHIO_NOME if ACCETTA_VECCHIO_NOME else [])   # (non modificare)

PORTA_ISTANZA_UNICA = 47625     # porta locale per non far partire due copie
FILE_LOG = percorsi.dati("eva.log")
FILE_LOG_VECCHIO = percorsi.dati("vox.log")   # il registro di quando si chiamava Vox


# ----------------------------------------------------------------------
# Registro (terminale + eva.log)
# ----------------------------------------------------------------------
def eredita_vecchio_log():
    """Se esiste vox.log e non c'è ancora eva.log, copia il vecchio registro nel nuovo
    (il vecchio resta dov'è). Restituisce True se ha copiato."""
    try:
        if not FILE_LOG.exists() and FILE_LOG_VECCHIO.exists():
            shutil.copyfile(FILE_LOG_VECCHIO, FILE_LOG)
            return True
    except OSError:
        pass
    return False


class Registro:
    """Stampa sul terminale (se c'è) e salva in eva.log."""

    def __init__(self):
        copiato = eredita_vecchio_log()
        self.logger = logging.getLogger("eva")
        self.logger.setLevel(logging.DEBUG)
        gestore = logging.handlers.RotatingFileHandler(
            FILE_LOG, maxBytes=500_000, backupCount=2, encoding="utf-8")
        gestore.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                               "%Y-%m-%d %H:%M:%S"))
        self.logger.addHandler(gestore)
        if copiato:
            self.logger.info("(il contenuto del vecchio vox.log è stato copiato qui sopra)")

    def __call__(self, messaggio):
        self.logger.info(messaggio)
        print(messaggio, flush=True)             # con pythonw non c'è terminale: non fa nulla

    def debug(self, messaggio):
        self.logger.debug(messaggio)

    def errore(self, messaggio):
        self.logger.exception(messaggio)
        print(messaggio + " (dettagli in eva.log)", flush=True)


# ----------------------------------------------------------------------
# Una sola copia alla volta
# ----------------------------------------------------------------------
def istanza_unica(log):
    """Prenota una porta locale. Se è già occupata, Eva è già in esecuzione:
    chiedo alla copia attiva di mostrarsi ed esco."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", PORTA_ISTANZA_UNICA))
        s.listen(2)
        return s
    except OSError:
        s.close()
        try:
            with socket.create_connection(("127.0.0.1", PORTA_ISTANZA_UNICA), timeout=2) as c:
                c.sendall(b"attiva\n")
        except OSError:
            pass
        log("Eva è già in esecuzione: non avvio una seconda copia.")
        sys.exit(0)


def _ascolta_altre_copie(server, vox):
    """Se si prova ad avviare una seconda copia, questa ci avvisa: mostro la finestra."""
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            try:
                conn.settimeout(1.0)
                if conn.recv(16).startswith(b"attiva"):
                    vox.attiva_ora()
            except OSError:
                pass


# ----------------------------------------------------------------------
def _senza_finestra(vox, log):
    """Se manca PySide6: Eva funziona lo stesso (F9 e "Eva"), senza finestra."""
    def svuota():
        while True:
            try:
                vox.eventi.get(timeout=1.0)
            except Exception:
                pass
    threading.Thread(target=svuota, daemon=True).start()
    try:
        while not vox.fermo.is_set():          # "spegni Eva" ferma il programma
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("Chiuso con Ctrl+C.")


def main():
    log = Registro()

    def errore_non_gestito(tipo, valore, traccia):
        log.logger.error("Errore non gestito", exc_info=(tipo, valore, traccia))
    sys.excepthook = errore_non_gestito
    threading.excepthook = lambda a: log.logger.error(
        "Errore in un thread", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    if "--impostazioni" in sys.argv:            # cambia_nome_e_chiave.bat: riapre la schermata iniziale e basta
        import vox_inizio
        vox_inizio.assicura({}, log, forza=True)
        return 0
    log("=" * 50)
    log("Eva si avvia...")
    server = istanza_unica(log)

    imp = {"WAKE_WORD": WAKE_WORD, "TASTO": TASTO, "MODELLO": MODELLO,
           "MODELLO_WAKE": MODELLO_WAKE, "SECONDI_SILENZIO": SECONDI_SILENZIO,
           "SOGLIA_VOCE_MIN": SOGLIA_VOCE_MIN, "LINGUA": LINGUA, "VOCI_LINGUE": VOCI_LINGUE,
           "TRASCRIZIONE_VELOCE_PRIMA": TRASCRIZIONE_VELOCE_PRIMA, "CONFIDENZA_VELOCE_MIN": CONFIDENZA_VELOCE_MIN, "PAROLE_SUGGERITE": PAROLE_SUGGERITE,
           "WAKE_VARIANTI": WAKE_VARIANTI, "VARIANTI_NOME": VARIANTI_NOME,
           "ACCETTA_VECCHIO_NOME": ACCETTA_VECCHIO_NOME, "VARIANTI_VECCHIO_NOME": VARIANTI_VECCHIO_NOME, "VOCE_ATTIVA": VOCE_ATTIVA, "VOCE_NOME": VOCE_NOME,
           "VOCE_VELOCITA": VOCE_VELOCITA, "VOCE_VOLUME": VOCE_VOLUME,
           "VOCE_PAUSA_DOPO": VOCE_PAUSA_DOPO, "NOME_UTENTE": NOME_UTENTE, "VERSIONE": VERSIONE,
           "FRASI_CHIUSURA": FRASI_CHIUSURA,
           "GROQ_AZIONI": GROQ_AZIONI, "GROQ_AZIONI_TIMEOUT": GROQ_AZIONI_TIMEOUT,
           "RIDUCI_DOPO_APERTURA": RIDUCI_DOPO_APERTURA, "SPARISCI_DOPO_COMANDO": SPARISCI_DOPO_COMANDO,
           "SECONDI_PRIMA_DI_SPARIRE": SECONDI_PRIMA_DI_SPARIRE, "ANGOLO_PREDEFINITO": ANGOLO_PREDEFINITO,
           "OPACITA_RIDOTTA": OPACITA_RIDOTTA, "SCALA_RIDOTTA": SCALA_RIDOTTA, "MARGINE": MARGINE,
           "OPERAZIONI_FILE": OPERAZIONI_FILE, "OPERAZIONI_LIMITE_GB": OPERAZIONI_LIMITE_GB,
           "OPERAZIONI_LIMITE_FILE": OPERAZIONI_LIMITE_FILE, "OPERAZIONI_CONFERMA_SECONDI": OPERAZIONI_CONFERMA_SECONDI, "PROFONDITA_INDICE": PROFONDITA_INDICE,
           "MAX_CARTELLE_INDICE": MAX_CARTELLE_INDICE, "MAX_FILE_INDICE": MAX_FILE_INDICE,
           "AGGIORNA_INDICE_MINUTI": AGGIORNA_INDICE_MINUTI, "SOGLIA_CARTELLE": SOGLIA_CARTELLE,
           "TEMPO_RICERCA_DISCO": TEMPO_RICERCA_DISCO, "NAV_VALIDITA_SECONDI": NAV_VALIDITA_SECONDI,
           "SUGGERISCI_NOMI_CARTELLE": SUGGERISCI_NOMI_CARTELLE,
           "CONVERSAZIONE_CONTINUA": CONVERSAZIONE_CONTINUA, "COMPAGNA": COMPAGNA, "COMPAGNA_CONFIDENZA_MIN": COMPAGNA_CONFIDENZA_MIN,
           "COMPAGNA_IGNORA_AUDIO": COMPAGNA_IGNORA_AUDIO,
           "TIMEOUT_CONVERSAZIONE": TIMEOUT_CONVERSAZIONE, "NO_SPEECH_MAX": NO_SPEECH_MAX, "LOGPROB_MIN": LOGPROB_MIN, "VAD_SOGLIA": VAD_SOGLIA,
           "VAD_SILENZIO_MS": VAD_SILENZIO_MS, "FRASI_FALSE": FRASI_FALSE}
    imp["_T0"] = T0
    import vox_inizio                       # schermata iniziale: nome di chi usa Eva e chiave gratuita di Groq
    if not vox_inizio.assicura(imp, log):
        server.close()
        return 0
    log(f"TEMPO avvio: Python e impostazioni pronti: {time.perf_counter() - T0:.1f} s")
    try:
        from vox_core import Vox
    except Exception:
        log.errore("Non riesco a caricare vox_core.py")
        raise
    vox = Vox(imp, log)
    threading.Thread(target=_ascolta_altre_copie, args=(server, vox), daemon=True).start()
    log(f"TEMPO avvio: librerie base e voce: {time.perf_counter() - T0:.1f} s (totale)")

    try:
        import vox_ui
    except ImportError as errore:
        vox.avvia()
        log(f"Manca la parte grafica ({errore}). Eva parte senza finestra: "
            "F9" + (' e "Eva"' if WAKE_WORD else "") + " funzionano lo stesso.")
        log("Per la finestra con la sfera:  python -m pip install PySide6-Essentials")
        _senza_finestra(vox, log)
        vox.ferma()
        return 0

    try:
        # La sfera compare SUBITO; ascolto, tasti, modelli, Groq e saluto partono subito dopo, in secondo piano.
        codice = vox_ui.esegui(vox, log, dopo=vox.avvia)
    except Exception:
        log.errore("Errore nella finestra")
        raise
    log("Eva chiuso.")
    vox.ferma()
    server.close()
    return codice


if __name__ == "__main__":
    sys.exit(main())
