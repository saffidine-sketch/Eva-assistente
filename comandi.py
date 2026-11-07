"""Comandi vocali per dettatura.py.

Come funziona il comando "apri":
  la frase deve iniziare con "apri", "avvia", "lancia" o "vai su" (anche
  pronunciati male: "abri", "apre", "apri-"); il resto è il nome di quello
  che vuoi aprire.

  Ordine dei controlli:
    0. correzioni.json    errori ricorrenti -> frase giusta (hai la precedenza)
    1. verbo              corrispondenza approssimata con PREFISSI
    2. nome               si confronta con app, siti, cartelle e app installate
                          e si sceglie il più simile (anche con errori di
                          pronuncia: "iutub" = YouTube, "crom" = Chrome).
                          Se due sono molto simili vince il più usato
                          (l'ordine degli elenchi qui sotto: prima = più usato)
    3. Windows            ultima possibilità, con filtri di sicurezza

  Se la corrispondenza è debole NON si apre niente: si stampa
  "Forse intendevi: ..." (soglie nella sezione SOGLIE).

Per aggiungere una cosa che si apre basta una riga negli elenchi qui sotto.
Per un errore ricorrente di pronuncia aggiungi una riga in correzioni.json.

Sicurezza: questo file apre soltanto. Non cancella, non installa, non modifica.
"""
import difflib
import json
import os
import re
import subprocess
import unicodedata
import webbrowser
import winreg
from collections import namedtuple
from pathlib import Path

# ======================================================================
# ELENCHI DA MODIFICARE (nomi in minuscolo, senza accenti)
# L'ordine conta: i primi sono i "più usati" e vincono a parità di somiglianza.
# ======================================================================

# Parole che indicano "apri qualcosa"
PREFISSI = ["apri", "avvia", "lancia", "vai su"]

# 1. App comuni: nome detto -> LISTA DI METODI, provati in ordine.
#    Il primo che funziona vince e il programma stampa quale è stato.
#    Metodi ("tipo:valore"):
#      comando:<nome>      nome semplice dato a Windows (notepad, calc, winword...)
#      protocollo:<x>:     indirizzo speciale di Windows (ms-settings:, outlookmail:...)
#                          si usa solo se è registrato sul PC
#      appid:<AUMID>       identificatore di un'app dello Store (shell:AppsFolder);
#                          si usa solo se l'app è installata
#      exe:<FILE.EXE>      file .exe cercato in App Paths, cartelle di Office e PATH
#      start:<nome app>    app con quel nome nel menu Start (corrispondenza approssimata)
#    (un valore scritto come semplice testo vale come "comando:testo")
#    Per cambiare cosa si apre, riordina i metodi: quello in cima ha la precedenza.

# Cartelle di installazione di Office, dentro Program Files (per outlook.exe ecc.)
OFFICE_CARTELLE = [r"Microsoft Office\root\Office16", r"Microsoft Office\Office16",
                   r"Microsoft Office\root\Office15", r"Microsoft Office\Office15",
                   r"Microsoft Office\root\Office14"]

_OUTLOOK_NUOVO = "appid:Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows"
_OUTLOOK_CLASSICO = ["exe:OUTLOOK.EXE", "appid:Microsoft.Office.OUTLOOK.EXE.15",
                     "start:outlook classic", "comando:outlook"]

# Posta / Outlook: stessa app per tutti i nomi. Ordine: app Posta di Windows
# (se c'è ancora), nuovo Outlook, poi Outlook classico (Office).
# Se preferisci l'Outlook classico, metti _OUTLOOK_CLASSICO prima del nuovo.
METODI_POSTA = [
    "protocollo:outlookmail:",
    "appid:microsoft.windowscommunicationsapps_8wekyb3d8bbwe!microsoft.windowslive.mail",
    _OUTLOOK_NUOVO,
    "protocollo:ms-outlook:",
    *_OUTLOOK_CLASSICO,
    "start:outlook",
]
METODI_CALENDARIO = [
    "protocollo:outlookcal:",
    "appid:microsoft.windowscommunicationsapps_8wekyb3d8bbwe!microsoft.windowslive.calendar",
    "start:calendario", "start:calendar",
    _OUTLOOK_NUOVO,                    # il nuovo Outlook contiene il calendario
]
METODI_CONTATTI = [
    "protocollo:ms-people:",
    "appid:Microsoft.People_8wekyb3d8bbwe!x4c7a3b7dy2188y46d4ya362y19ac5a5805e5x",
    "start:contatti", "start:people",
    _OUTLOOK_NUOVO,
]
METODI_FOTO = ["protocollo:ms-photos:", "appid:Microsoft.Windows.Photos_8wekyb3d8bbwe!App",
               "start:foto"]
METODI_STORE = ["protocollo:ms-windows-store:",
                "appid:Microsoft.WindowsStore_8wekyb3d8bbwe!App", "start:microsoft store"]
METODI_IMPOSTAZIONI = ["protocollo:ms-settings:", "start:impostazioni"]
METODI_TERMINALE = ["appid:Microsoft.WindowsTerminal_8wekyb3d8bbwe!App", "comando:wt",
                    "start:terminale"]
METODI_POWERSHELL = ["comando:powershell", "start:windows powershell"]
METODI_PROMPT = ["comando:cmd", "start:prompt dei comandi"]
METODI_WHATSAPP = ["appid:5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
                   "protocollo:whatsapp:", "start:whatsapp"]
METODI_SPOTIFY = ["protocollo:spotify:", "appid:SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
                  "start:spotify"]
METODI_TEAMS = ["appid:MSTeams_8wekyb3d8bbwe!MSTeams", "protocollo:msteams:", "start:teams"]
METODI_WORD = ["comando:winword", "exe:WINWORD.EXE", "start:word"]
METODI_EXCEL = ["comando:excel", "exe:EXCEL.EXE", "start:excel"]
METODI_POWERPOINT = ["comando:powerpnt", "exe:POWERPNT.EXE", "start:powerpoint"]
METODI_CHROME = ["comando:chrome", "exe:chrome.exe", "start:google chrome"]
METODI_EDGE = ["comando:msedge", "exe:msedge.exe", "start:microsoft edge"]
METODI_FIREFOX = ["comando:firefox", "exe:firefox.exe", "start:firefox"]

ALIAS_APP = {
    "chrome": METODI_CHROME,
    "google chrome": METODI_CHROME,
    # posta: tutti questi nomi portano alla stessa app
    "outlook": METODI_POSTA,
    "posta": METODI_POSTA,
    "mail": METODI_POSTA,
    "email": METODI_POSTA,
    "app posta": METODI_POSTA,
    "posta di windows": METODI_POSTA,
    "posta elettronica": METODI_POSTA,
    "nuovo outlook": METODI_POSTA,
    "outlook classico": _OUTLOOK_CLASSICO,
    "outlook classic": _OUTLOOK_CLASSICO,
    "blocco note": "notepad",
    "notepad": "notepad",
    "calcolatrice": ["comando:calc", "start:calcolatrice"],
    "calcolatore": ["comando:calc", "start:calcolatrice"],
    "esplora file": "explorer",
    "esplora risorse": "explorer",
    "impostazioni": METODI_IMPOSTAZIONI,
    "impostazioni di windows": METODI_IMPOSTAZIONI,
    "gestione attivita": "taskmgr",
    "task manager": "taskmgr",
    "paint": ["comando:mspaint", "start:paint"],
    "word": METODI_WORD,
    "microsoft word": METODI_WORD,
    "excel": METODI_EXCEL,
    "microsoft excel": METODI_EXCEL,
    "powerpoint": METODI_POWERPOINT,
    "power point": METODI_POWERPOINT,
    "microsoft powerpoint": METODI_POWERPOINT,
    "calendario": METODI_CALENDARIO,
    "calendar": METODI_CALENDARIO,
    "agenda": METODI_CALENDARIO,
    "contatti": METODI_CONTATTI,
    "rubrica": METODI_CONTATTI,
    "persone": METODI_CONTATTI,
    "foto": METODI_FOTO,
    "app foto": METODI_FOTO,
    "store": METODI_STORE,
    "microsoft store": METODI_STORE,
    "windows store": METODI_STORE,
    "terminale": METODI_TERMINALE,
    "windows terminal": METODI_TERMINALE,
    "terminale di windows": METODI_TERMINALE,
    "powershell": METODI_POWERSHELL,
    "power shell": METODI_POWERSHELL,
    "windows powershell": METODI_POWERSHELL,
    "prompt dei comandi": METODI_PROMPT,
    "prompt": METODI_PROMPT,
    "riga di comando": METODI_PROMPT,
    "whatsapp": METODI_WHATSAPP,      # se non c'è l'app, si apre il sito (ALIAS_SITI)
    "whatsapp desktop": METODI_WHATSAPP,
    "app whatsapp": METODI_WHATSAPP,
    "spotify": METODI_SPOTIFY,        # se non c'è l'app, si apre il sito (ALIAS_SITI)
    "app spotify": METODI_SPOTIFY,
    "teams": METODI_TEAMS,
    "microsoft teams": METODI_TEAMS,
    "edge": METODI_EDGE,
    "microsoft edge": METODI_EDGE,
    "firefox": METODI_FIREFOX,
}

# 2. Siti più usati: nome detto -> indirizzo
ALIAS_SITI = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "whatsapp web": "https://web.whatsapp.com",
    "whatsapp": "https://web.whatsapp.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com",
    "traduttore": "https://translate.google.com",
    "twitter": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "amazon": "https://www.amazon.it",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "github": "https://github.com",
    "wikipedia": "https://it.wikipedia.org",
    "claude": "https://claude.ai",
    "chatgpt": "https://chatgpt.com",
    "outlook web": "https://outlook.live.com",
}

# Terminazioni accettate per riconoscere un indirizzo ("esempio punto it")
ESTENSIONI_SITO = ("com", "it", "org", "net", "eu", "io", "info", "edu",
                   "gov", "app", "dev", "co", "uk", "de", "fr", "es")

# 3. Cartelle comuni: nome detto -> nome della cartella nel profilo utente
CARTELLE = {
    "documenti": "Documents",
    "scaricati": "Downloads",
    "download": "Downloads",
    "downloads": "Downloads",
    "desktop": "Desktop",
    "immagini": "Pictures",
    "foto": "Pictures",
    "musica": "Music",
    "video": "Videos",
}

# 4. App del menu Start: si ignorano i nomi che contengono queste parti
#    (così non si apre mai un programma di installazione o disinstallazione)
NOMI_VIETATI = ("install", "uninst", "disinstall", "setup", "rimuov", "remove")

# 5. Ultima possibilità: comandi e tipi di file che NON si aprono mai
COMANDI_PERICOLOSI = {
    "format", "del", "erase", "rd", "rmdir", "diskpart", "cipher", "sfc",
    "shutdown", "reg", "regedit", "msiexec", "bcdedit", "takeown", "icacls",
    "attrib", "move", "copy", "xcopy", "robocopy", "sc", "net", "taskkill",
    "wmic", "powershell", "pwsh", "cmd", "mshta", "wscript", "cscript",
    "rundll32", "runas", "schtasks", "setup", "install", "uninstall",
}
ESTENSIONI_VIETATE = {"bat", "cmd", "ps1", "msi", "vbs", "vbe", "js", "jse",
                      "wsf", "reg", "scr", "com", "hta", "lnk", "inf"}

# ======================================================================
# SOGLIE (da 0 a 1: più alto = più severo)
# ======================================================================
SOGLIA_SICURA = 0.80        # da qui in su il nome si apre
SOGLIA_INCERTA = 0.68       # tra incerta e sicura: solo "Forse intendevi"
MARGINE_PARI = 0.04         # entro questo margine due risultati sono "pari"
SOGLIA_VERBO_SICURO = 0.74  # per "abri", "apre"... (parola di lunghezza simile)
SOGLIA_VERBO_INCERTO = 0.60

# File con gli errori ricorrenti, nella stessa cartella di questo script
FILE_CORREZIONI = Path(__file__).with_name("correzioni.json")
# Alias tuoi (nome detto -> app), anche creati a voce con "impara <nome> è <app>"
FILE_ALIAS_UTENTE = Path(__file__).with_name("alias_utente.json")
# Nomi che non sono stati trovati: serve per aggiungere in seguito l'alias giusto
FILE_NON_TROVATI = Path(__file__).with_name("non_trovati.txt")

# ======================================================================
# COMANDI DIVERSI DA "APRI" (facili da aggiungere)
# ======================================================================
# Ogni comando: parole che devono essere TUTTE nella frase, e funzione che
# esegue l'azione e restituisce un testo da stampare.
#
# def alza_volume():
#     ...
#     return "Volume alzato"
#
COMANDI = [
    # {"parole": ["alza", "volume"], "azione": alza_volume},
]


def trova_comando(frase):
    """Restituisce il primo comando di COMANDI che corrisponde, o None."""
    for comando in COMANDI:
        if all(parola in frase for parola in comando["parole"]):
            return comando
    return None


# ======================================================================
# CODICE (di solito non serve modificarlo)
# ======================================================================

# tipo: "testo" (non è un comando), "eseguito", "incerto", "non_trovato"
Risultato = namedtuple("Risultato", "tipo frase_corretta messaggio")

ARTICOLI = {"il", "lo", "la", "l", "i", "gli", "le", "un", "uno", "una"}


def normalizza(testo):
    """Minuscolo, senza accenti né punteggiatura ("punto" diventa un punto)."""
    testo = unicodedata.normalize("NFD", testo.lower())
    testo = "".join(c for c in testo if unicodedata.category(c) != "Mn")
    testo = re.sub(r"[^a-z0-9.\- ]+", " ", testo)
    testo = re.sub(r"(?<![a-z0-9])-+|-+(?![a-z0-9])", " ", testo)  # "apri-"
    testo = re.sub(r"\s+", " ", f" {testo} ")
    testo = testo.replace(" punto ", ".")
    testo = re.sub(r"\.+(?= |$)", "", testo)        # punti a fine parola
    testo = re.sub(r"(?<= )\.+", "", testo)         # punti a inizio parola
    return re.sub(r"\s+", " ", testo).strip()


# --- somiglianza fra parole -------------------------------------------
def _fonetica(testo):
    """Forma semplificata che avvicina pronuncia italiana e grafia inglese."""
    for a, b in (("ph", "f"), ("ch", "c"), ("gh", "g"), ("ck", "c"),
                 ("qu", "cu"), ("ou", "u"), ("oo", "u"), ("y", "i"),
                 ("w", "u"), ("k", "c"), ("x", "cs"), ("h", "")):
        testo = testo.replace(a, b)
    testo = re.sub(r"(.)\1+", r"\1", testo)                  # lettere doppie
    parole = [p[:-1] if len(p) > 3 and p[-1] in "aeiou" else p   # vocale finale
              for p in testo.split()]
    return " ".join(parole)


def _punteggio(detto, chiave):
    """Somiglianza da 0 a 1 tra quello che ho sentito e un nome dell'elenco."""
    if detto == chiave:
        return 1.0
    s = difflib.SequenceMatcher(None, detto, chiave).ratio()
    fd, fc = _fonetica(detto), _fonetica(chiave)
    if fd and fc:
        s = max(s, 0.97 * difflib.SequenceMatcher(None, fd, fc).ratio())
    return s


# --- 0. correzioni.json -----------------------------------------------
_correzioni = None


def carica_correzioni():
    """Legge correzioni.json (errore -> frase giusta). Restituisce quante."""
    global _correzioni
    try:
        dati = json.loads(FILE_CORREZIONI.read_text(encoding="utf-8"))
    except FileNotFoundError:
        dati = {}
    except Exception as errore:
        print(f"Attenzione: correzioni.json non leggibile ({errore})", flush=True)
        dati = {}
    coppie = []
    for errore, giusta in dati.items():
        if errore.startswith("_") or not isinstance(giusta, str):
            continue
        e, g = normalizza(errore), normalizza(giusta)
        if e and g:
            coppie.append((e, g))
    coppie.sort(key=lambda c: -len(c[0]))        # prima le più lunghe
    _correzioni = coppie
    return len(coppie)


def applica_correzioni(frase):
    if _correzioni is None:
        carica_correzioni()
    for errore, giusta in _correzioni:
        nuova = re.sub(rf"(?<![a-z0-9]){re.escape(errore)}(?![a-z0-9])", giusta, frase)
        if nuova != frase:
            return re.sub(r"\s+", " ", nuova).strip()   # una sola correzione
    return frase


# --- 1. verbo ----------------------------------------------------------
def riconosci_verbo(parole):
    """Cerca il verbo di comando all'inizio della frase, anche storpiato.

    Restituisce (verbo, quante parole usa, punteggio, sicuro) o None.
    La prima lettera deve coincidere, così "capri" o "aprile" non passano
    per "apri" (al massimo diventano "Forse intendevi").
    """
    migliore = None
    for verbo in PREFISSI:
        n = len(verbo.split())
        if len(parole) < n:
            continue
        detto = " ".join(parole[:n])
        if detto[0] != verbo[0]:
            continue
        s = 1.0 if detto == verbo else difflib.SequenceMatcher(None, detto, verbo).ratio()
        if migliore is None or s > migliore[2]:
            migliore = (verbo, n, s, abs(len(detto) - len(verbo)) <= 1)
    if migliore is None:
        return None
    verbo, n, s, lunghezza_ok = migliore
    if s >= SOGLIA_VERBO_SICURO and lunghezza_ok:
        return verbo, n, s, True
    if s >= SOGLIA_VERBO_INCERTO:
        return verbo, n, s, False
    return None


# --- 2. nome: candidati da tutte le fonti ------------------------------
def _apri_url(url):
    if not webbrowser.open(url):
        raise OSError("nessun browser disponibile")


def _avvia(destinazione):
    # Si parte da una cartella di sistema, così non si apre per sbaglio
    # un file con lo stesso nome presente nella cartella da cui lancio lo script.
    cwd = os.getcwd()
    try:
        os.chdir(os.environ.get("SystemRoot", "C:\\Windows"))
        os.startfile(destinazione)
    finally:
        os.chdir(cwd)


_catalogo = None
_app_ids = {}        # identificatore (minuscolo) -> (nome, identificatore originale)


def _nome_permesso(nome):
    n = normalizza(nome)
    return not any(v in n for v in NOMI_VIETATI)


def _carica_catalogo():
    """Elenco {nome normalizzato: (nome, come aprirlo, tipo)}. Una volta sola."""
    global _catalogo
    if _catalogo is not None:
        return _catalogo
    catalogo = {}
    # Collegamenti .lnk nel menu Start di sistema e dell'utente
    for variabile in ("ProgramData", "APPDATA"):
        base = os.environ.get(variabile)
        if not base:
            continue
        cartella = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if not cartella.is_dir():
            continue
        for lnk in cartella.rglob("*.lnk"):
            if _nome_permesso(lnk.stem):
                catalogo.setdefault(normalizza(lnk.stem), (lnk.stem, str(lnk), "lnk"))
    # App del menu Start (comprese quelle dello Store)
    try:
        risultato = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        dati = json.loads(risultato.stdout or "[]")
        if isinstance(dati, dict):
            dati = [dati]
        for app in dati:
            nome, app_id = app.get("Name", ""), app.get("AppID", "")
            if nome and app_id:
                _app_ids[app_id.lower()] = (nome, app_id)
            if nome and app_id and _nome_permesso(nome):
                catalogo.setdefault(normalizza(nome), (nome, app_id, "app"))
    except Exception:
        pass
    _catalogo = catalogo
    return catalogo


def _apri_app_installata(destinazione, tipo):
    if tipo == "lnk":
        os.startfile(destinazione)
    else:
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + destinazione])


# --- Apertura di un'app con più metodi ------------------------------------
def _protocollo_registrato(schema):
    """True se Windows conosce questo protocollo (es. 'outlookmail:')."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, schema.rstrip(":")) as chiave:
            winreg.QueryValueEx(chiave, "URL Protocol")
        return True
    except OSError:
        return False


def _trova_exe(nome_exe):
    """Cerca un .exe in App Paths, nelle cartelle di Office e nel PATH.
    Non cerca mai nella cartella corrente."""
    percorsi = []
    for radice in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(
                    radice, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + nome_exe) as k:
                percorsi.append(str(winreg.QueryValueEx(k, "")[0]).strip('"'))
        except OSError:
            pass
    for variabile in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variabile)
        if base:
            percorsi += [str(Path(base) / sotto / nome_exe) for sotto in OFFICE_CARTELLE]
    percorsi += [str(Path(cartella) / nome_exe)
                 for cartella in os.environ.get("PATH", "").split(os.pathsep) if cartella]
    for percorso in percorsi:
        if Path(percorso).is_file():
            return percorso
    return None


def _risolvi_metodo(metodo):
    """Trasforma 'tipo:valore' in (etichetta, azione). Se quel metodo non è
    disponibile su questo PC solleva LookupError (senza aprire niente)."""
    tipo, _, valore = metodo.partition(":")
    if tipo == "comando":
        # Informativo: se il file si trova lo mostro, altrimenti ci provo lo stesso
        # (Windows a volte trova il nome in altri modi)
        percorso = _trova_exe(valore if valore.lower().endswith(".exe") else valore + ".exe")
        dettaglio = percorso if percorso else "non verificato"
        return f"comando {valore} ({dettaglio})", lambda: _avvia(valore)
    if tipo == "protocollo":
        if not _protocollo_registrato(valore):
            raise LookupError("protocollo non registrato")
        return f"protocollo {valore}", lambda: _avvia(valore)
    if tipo == "appid":
        _carica_catalogo()
        voce = _app_ids.get(valore.lower())
        if not voce:
            raise LookupError("app non installata")
        nome, originale = voce
        return (f"appid {originale} ({nome})",
                lambda: subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + originale]))
    if tipo == "exe":
        percorso = _trova_exe(valore)
        if not percorso:
            raise LookupError("file non trovato")
        return f"exe {percorso}", lambda: os.startfile(percorso)
    if tipo == "start":
        catalogo = _carica_catalogo()
        voluto = normalizza(valore)
        if not catalogo:
            raise LookupError("elenco app vuoto")
        chiave = max(catalogo, key=lambda k: _punteggio(voluto, k))
        if _punteggio(voluto, chiave) < SOGLIA_SICURA:
            raise LookupError("nessuna app con questo nome nel menu Start")
        originale, destinazione, tipo_app = catalogo[chiave]
        return (f"menu Start '{originale}'",
                lambda: _apri_app_installata(destinazione, tipo_app))
    raise ValueError(f"metodo sconosciuto '{tipo}'")


def _lista_metodi(valore):
    return [f"comando:{valore}"] if isinstance(valore, str) else list(valore)


def _apri_con_metodi(metodi):
    """Prova i metodi in ordine e restituisce un testo con quello che ha funzionato."""
    saltati = []
    for metodo in metodi:
        try:
            etichetta, azione = _risolvi_metodo(metodo)
            azione()
        except Exception as errore:
            saltati.append(f"{metodo} ({errore})")
            continue
        extra = f"; prima senza successo: {' | '.join(saltati)}" if saltati else ""
        return f"metodo: {etichetta}{extra}"
    raise OSError("nessun metodo ha funzionato: " + " | ".join(saltati))


def _anteprima_metodi(metodi):
    """Come _apri_con_metodi ma senza aprire niente (per le prove)."""
    saltati = []
    for metodo in metodi:
        try:
            etichetta, _ = _risolvi_metodo(metodo)
        except Exception as errore:
            saltati.append(f"{metodo} ({errore})")
            continue
        extra = f"; saltati: {' | '.join(saltati)}" if saltati else ""
        return f"metodo: {etichetta}{extra}"
    raise LookupError("nessun metodo disponibile: " + " | ".join(saltati))


def _apertura_app(metodi):
    apri = lambda m=metodi: _apri_con_metodi(m)
    apri.anteprima = lambda m=metodi: _anteprima_metodi(m)
    return apri


# --- Alias tuoi e nomi non trovati --------------------------------------------
_alias_utente = {}


def carica_alias_utente():
    """Legge alias_utente.json ({"nome detto": "app"}). Restituisce quanti."""
    global _alias_utente
    try:
        dati = json.loads(FILE_ALIAS_UTENTE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        dati = {}
    except Exception as errore:
        print(f"Attenzione: alias_utente.json non leggibile ({errore})", flush=True)
        dati = {}
    _alias_utente = {}
    for nome, app in dati.items():
        if nome.startswith("_") or not isinstance(app, str):
            continue
        n, a = normalizza(nome), normalizza(app)
        if n and a:
            _alias_utente[n] = a
    return len(_alias_utente)


def salva_alias_utente(nome, bersaglio):
    """Aggiunge un alias a alias_utente.json (e lo rende subito attivo)."""
    nome = normalizza(nome)
    try:
        dati = json.loads(FILE_ALIAS_UTENTE.read_text(encoding="utf-8"))
    except Exception:
        dati = {}
    dati[nome] = bersaglio
    temporaneo = FILE_ALIAS_UTENTE.with_suffix(".tmp")
    temporaneo.write_text(json.dumps(dati, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    temporaneo.replace(FILE_ALIAS_UTENTE)
    _alias_utente[nome] = normalizza(bersaglio)


def risolvi_nome(nome):
    """Cosa aprirebbe 'nome' con certezza? (chiave, descrizione) o None.
    Gli alias tuoi non contano: serve per imparare un nome nuovo."""
    cand = [c for c in _candidati(normalizza(nome), con_alias_utente=False)
            if c[0] >= SOGLIA_SICURA]
    if not cand:
        return None
    migliore = _ordina(cand)[0]
    return migliore[3], migliore[4]


def registra_non_trovato(nome):
    """Aggiunge il nome a non_trovati.txt (una volta sola). True se l'ha scritto."""
    try:
        presenti = (set(FILE_NON_TROVATI.read_text(encoding="utf-8").splitlines())
                    if FILE_NON_TROVATI.exists() else set())
        if nome in presenti:
            return False
        with open(FILE_NON_TROVATI, "a", encoding="utf-8") as f:
            f.write(nome + "\n")
        return True
    except OSError:
        return False


def _candidati(nome, con_alias_utente=True):
    """Tutte le cose apribili abbastanza simili a 'nome'.

    Ogni candidato: (punteggio, fonte, posizione, chiave, descrizione, apri).
    fonte e posizione servono a scegliere il "più usato" a parità di punteggio.
    """
    cand = []
    for i, (chiave, valore) in enumerate(ALIAS_APP.items()):
        s = _punteggio(nome, chiave)
        if s >= SOGLIA_INCERTA:
            cand.append((s, 0, i, chiave, f"app '{chiave}'",
                         _apertura_app(_lista_metodi(valore))))
    for i, (chiave, url) in enumerate(ALIAS_SITI.items()):
        s = _punteggio(nome, chiave)
        if s >= SOGLIA_INCERTA:
            cand.append((s, 1, i, chiave, f"sito '{chiave}' ({url})",
                         lambda u=url: _apri_url(u)))
    estensioni = "|".join(ESTENSIONI_SITO)
    if re.fullmatch(rf"(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.({estensioni})", nome):
        url = "https://" + nome
        cand.append((1.0, 1, -1, nome, f"sito {url}", lambda u=url: _apri_url(u)))
    casa = Path.home()
    for i, (chiave, cartella) in enumerate(CARTELLE.items()):
        s = _punteggio(nome, chiave)
        if s < SOGLIA_INCERTA:
            continue
        for percorso in (casa / cartella, casa / "OneDrive" / cartella):
            if percorso.is_dir():
                cand.append((s, 2, i, chiave, f"cartella '{chiave}' ({percorso})",
                             lambda p=str(percorso): os.startfile(p)))
                break
    for i, (chiave, (originale, dest, tipo)) in enumerate(_carica_catalogo().items()):
        s = _punteggio(nome, chiave)
        if len(nome) >= 3 and re.search(rf"(^| ){re.escape(nome)}( |$)", chiave):
            s = max(s, 0.90)                  # "word" dentro "microsoft word"
        if s >= SOGLIA_INCERTA:
            cand.append((s, 3, i, chiave, f"app installata '{originale}'",
                         lambda d=dest, t=tipo: _apri_app_installata(d, t)))
    if con_alias_utente:
        # I tuoi alias hanno la precedenza: "posta elettronica" -> "outlook"
        for i, (alias, bersaglio) in enumerate(_alias_utente.items()):
            s = _punteggio(nome, alias)
            if s < SOGLIA_INCERTA:
                continue
            sicuri = [c for c in _candidati(bersaglio, con_alias_utente=False)
                      if c[0] >= SOGLIA_SICURA]
            if sicuri:
                migliore = _ordina(sicuri)[0]
                cand.append((s, -1, i, alias,
                             f"alias personale '{alias}' = {migliore[4]}", migliore[5]))
    return cand


def _ordina(cand):
    """Prima i migliori; fra quelli quasi pari, il più usato."""
    top = max(c[0] for c in cand)
    pari = sorted((c for c in cand if c[0] >= top - MARGINE_PARI),
                  key=lambda c: (c[1], c[2]))
    resto = sorted((c for c in cand if c[0] < top - MARGINE_PARI),
                   key=lambda c: -c[0])
    return pari + resto


# --- 3. Windows (ultima possibilità) -----------------------------------
def cerca_con_windows(nome):
    if not re.fullmatch(r"[a-z0-9 ._-]{1,40}", nome):
        return None
    parti = re.split(r"[ .]", nome)
    if any(p in COMANDI_PERICOLOSI for p in parti):
        return None
    if "." in nome and nome.rsplit(".", 1)[-1] in ESTENSIONI_VIETATE:
        return None
    return f"con Windows '{nome}'", lambda: _avvia(nome)


def precarica():
    """Prepara tutto prima del primo comando. Restituisce (correzioni, app).
    Legge anche alias_utente.json (quanti: len(_alias_utente))."""
    carica_alias_utente()
    return carica_correzioni(), len(_carica_catalogo())


def gestisci_frase(testo, esegui=True):
    """Interpreta la frase ed esegue il comando (se esegui=True).

    Restituisce Risultato(tipo, frase_corretta, messaggio).
    tipo "testo" = non è un comando: il programma continua come prima.
    """
    frase = applica_correzioni(normalizza(testo))

    comando = trova_comando(frase)
    if comando:
        return Risultato("eseguito", frase, comando["azione"]() if esegui else "(prova)")

    parole = frase.split()
    verbo = riconosci_verbo(parole) if parole else None
    if verbo is None:
        return Risultato("testo", frase, "")
    nome_verbo, n, _, verbo_sicuro = verbo
    resto = parole[n:]
    while resto and resto[0] in ARTICOLI:
        resto.pop(0)
    nome = " ".join(resto)
    if not nome:
        return Risultato("non_trovato", nome_verbo, "Non ho capito cosa aprire")

    cand = _candidati(nome)
    if cand:
        ordinati = _ordina(cand)
        top = max(c[0] for c in cand)
        migliore = ordinati[0]
        corretta = f"{nome_verbo} {migliore[3]}"
        tentativi = []
        if top >= SOGLIA_SICURA and verbo_sicuro:
            for punteggio, _f, _p, _chiave, descrizione, apri in ordinati:
                if punteggio < SOGLIA_SICURA:
                    continue
                if not esegui:
                    anteprima = getattr(apri, "anteprima", None)
                    try:
                        dettaglio = f" [{anteprima()}]" if anteprima else ""
                    except Exception as errore:
                        tentativi.append(f"{descrizione}: {errore}")
                        continue             # simulo: non si aprirebbe, passo al successivo
                    return Risultato("eseguito", corretta,
                                     f"(prova) aprirebbe {descrizione}{dettaglio}")
                try:
                    metodo = apri()
                except Exception as errore:
                    tentativi.append(f"{descrizione}: {errore}")
                    continue                 # non si apre: provo il successivo
                dettaglio = f" ({metodo})" if isinstance(metodo, str) and metodo else ""
                return Risultato("eseguito", corretta, f"Aperto {descrizione}{dettaglio}")
        else:
            # corrispondenza debole (nome o verbo): non eseguo niente
            return Risultato("incerto", corretta, f"Forse intendevi: {corretta}")

    if not verbo_sicuro:
        corretta = f"{nome_verbo} {nome}"
        return Risultato("incerto", corretta, f"Forse intendevi: {corretta}")
    corretta = f"{nome_verbo} {nome}"
    ultima = cerca_con_windows(nome)
    if ultima:
        descrizione, apri = ultima
        if not esegui:
            return Risultato("eseguito", corretta, f"(prova) aprirebbe {descrizione}")
        try:
            apri()
            return Risultato("eseguito", corretta, f"Aperto {descrizione}")
        except Exception:
            pass
    messaggio = f"Non ho trovato: {nome}"
    if cand and tentativi:
        messaggio += "\n  (non si sono aperti: " + " | ".join(tentativi) + ")"
    if esegui and registra_non_trovato(nome):
        messaggio += "\n  (nome salvato in non_trovati.txt)"
    return Risultato("non_trovato", corretta, messaggio)
