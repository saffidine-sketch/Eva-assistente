"""Comandi vocali "extra" per dettatura.py: volume, ricerca web, bozza di mail.

Come funziona:
  - ogni comando è una funzione qui sotto (alza_volume, cerca_su_web, ...);
  - tutti i comandi sono registrati in ELENCO_COMANDI (in fondo alla sezione
    FUNZIONI): per aggiungerne uno basta scrivere la funzione e una riga;
  - la frase viene confrontata con le varianti di ogni comando usando la stessa
    correzione approssimata del comando "apri" (difflib + suono delle parole);
  - corrispondenza forte  -> esegue
    corrispondenza debole -> stampa "Forse intendevi: ..." e non esegue niente
    nessuna              -> la frase passa al comando "apri" (comandi.py)
                            e se non è nemmeno quello resta testo.

Sicurezza: questi comandi SOLO aprono, cercano, regolano il volume e preparano
bozze. Non inviano mail o messaggi, non cancellano, non installano, non
modificano file.
"""
import ctypes
import json
import re
import time
import unicodedata
from collections import namedtuple
from pathlib import Path
from urllib.parse import quote, urlencode

import comandi
from comandi import (MARGINE_PARI, SOGLIA_INCERTA, SOGLIA_SICURA, Risultato,
                     _avvia, _apri_url, _punteggio)

# ======================================================================
# IMPOSTAZIONI (da modificare qui)
# ======================================================================
MAIL_PREDEFINITA = "gmail"     # "gmail" = Gmail nel browser, "posta" = app Posta (mailto:)
PASSO_VOLUME = 10              # di quanto cambia "alza/abbassa il volume" (in %)
FILE_RUBRICA = Path(__file__).with_name("rubrica.json")

# Parole che si possono togliere dalla frase senza cambiarne il senso
RIEMPITIVI = {"per", "favore", "grazie", "adesso", "ora", "subito", "po"}

# Numeri scritti a parole (le cifre "50" vengono capite da sole)
NUMERI = {
    "zero": 0, "dieci": 10, "quindici": 15, "venti": 20, "venticinque": 25,
    "trenta": 30, "trentacinque": 35, "quaranta": 40, "quarantacinque": 45,
    "cinquanta": 50, "cinquantacinque": 55, "sessanta": 60, "sessantacinque": 65,
    "settanta": 70, "settantacinque": 75, "ottanta": 80, "ottantacinque": 85,
    "novanta": 90, "novantacinque": 95, "cento": 100,
}

# --- Varianti delle frasi (senza accenti; "il/la/lo" si ignorano) -------
VARIANTI_VOLUME_SU = ["alza il volume", "aumenta il volume", "volume su",
                      "più volume", "volume alto"]
VARIANTI_VOLUME_GIU = ["abbassa il volume", "diminuisci il volume", "volume giù",
                       "meno volume", "riduci il volume", "volume basso"]
VARIANTI_MUTO = ["muto", "metti muto", "silenzia"]   # "silenzio" ora spegne la VOCE di Eva (vedi risposte.json)
VARIANTI_TOGLI_MUTO = ["togli il muto", "riattiva audio", "riattiva il volume",
                       "disattiva muto", "attiva audio", "rimetti il volume"]

VERBI_CERCA = ["cerca", "cercami"]
VERBI_MAIL = ["scrivi", "manda", "invia", "prepara", "crea"]
PAROLE_MAIL = ["mail", "email", "emeil", "imeil", "imail"]
MOTORI = {"google": "https://www.google.com/search?q=",
          "youtube": "https://www.youtube.com/results?search_query="}

# Come si dicono a voce i simboli degli indirizzi email
SIMBOLI = {"punto": ".", "trattino": "-", "underscore": "_", "sottolineato": "_"}
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")

# ======================================================================
# FUNZIONI: una per comando. "prova=True" = non fa niente, descrive e basta.
# ======================================================================

# --- Volume ------------------------------------------------------------
def _volume_pycaw():
    """Oggetto volume di pycaw, oppure None se pycaw non è installato."""
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return None
    try:
        dispositivo = AudioUtilities.GetSpeakers()
        if hasattr(dispositivo, "EndpointVolume"):          # pycaw recente
            return dispositivo.EndpointVolume
        from ctypes import POINTER, cast                    # pycaw più vecchio
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        interfaccia = dispositivo.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interfaccia, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def pycaw_disponibile():
    return _volume_pycaw() is not None


def _tasto(codice, volte=1):
    """Preme un tasto multimediale (ogni pressione = circa 2% di volume)."""
    for _ in range(volte):
        ctypes.windll.user32.keybd_event(codice, 0, 0, 0)
        ctypes.windll.user32.keybd_event(codice, 0, 2, 0)
        time.sleep(0.01)


VK_MUTO, VK_GIU, VK_SU = 0xAD, 0xAE, 0xAF


def _cambia_volume(delta, prova):
    verso = "alzerebbe" if delta > 0 else "abbasserebbe"
    if prova:
        return f"(prova) {verso} il volume di {abs(delta)}%"
    ep = _volume_pycaw()
    if ep:
        nuovo = min(1.0, max(0.0, ep.GetMasterVolumeLevelScalar() + delta / 100))
        ep.SetMasterVolumeLevelScalar(nuovo, None)
        return f"Volume al {round(nuovo * 100)}%"
    _tasto(VK_SU if delta > 0 else VK_GIU, max(1, abs(delta) // 2))
    return f"Volume {'alzato' if delta > 0 else 'abbassato'} di circa {abs(delta)}% (tasti multimediali)"


def alza_volume(prova=False):
    return _cambia_volume(+PASSO_VOLUME, prova)


def abbassa_volume(prova=False):
    return _cambia_volume(-PASSO_VOLUME, prova)


def volume_al(valore, prova=False):
    valore = max(0, min(100, int(valore)))
    if prova:
        return f"(prova) porterebbe il volume al {valore}%"
    ep = _volume_pycaw()
    if ep:
        ep.SetMasterVolumeLevelScalar(valore / 100, None)
        return f"Volume al {valore}%"
    _tasto(VK_GIU, 50)                       # prima tutto giù...
    _tasto(VK_SU, round(valore / 2))         # ...poi su fino al valore
    return f"Volume portato a circa {valore}% (tasti multimediali)"


def _muto(attivo, prova):
    parola = "metterebbe" if attivo else "toglierebbe"
    if prova:
        return f"(prova) {parola} il muto"
    ep = _volume_pycaw()
    if ep:
        ep.SetMute(1 if attivo else 0, None)
        return "Muto attivato" if attivo else "Muto tolto"
    _tasto(VK_MUTO)
    return ("Premuto il tasto muto (è un interruttore: senza pycaw non so se "
            "l'audio era già muto)")


def metti_muto(prova=False):
    return _muto(True, prova)


def togli_muto(prova=False):
    return _muto(False, prova)


# --- Ricerca web ---------------------------------------------------------
_ultima_ricerca_youtube = {"testo": None, "quando": 0.0}    # per "falla partire"
VALIDITA_RICERCA_YOUTUBE = 600                              # secondi: oltre, "falla partire" non vale più


def cerca_su_web(testo, motore="google", prova=False):
    if not testo:
        return "Non ho capito cosa cercare"
    url = MOTORI[motore] + quote(testo, safe="")
    if prova:
        return f"(prova) cercherebbe su {motore}: {url}"
    _apri_url(url)
    if motore == "youtube":
        _ultima_ricerca_youtube.update(testo=testo, quando=time.time())
    return f"Cerco su {motore}: {testo}"


# --- "Falla partire": apre il primo risultato della ricerca YouTube ----------
def _primo_video_youtube(testo, n=1):
    """ID dell'n-esimo video (il primo di solito) nei risultati di YouTube per `testo` (lo stesso che si
    otterrebbe cliccando quel risultato), oppure None."""
    import urllib.request
    richiesta = urllib.request.Request(
        MOTORI["youtube"] + quote(testo, safe=""),
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                 "Accept-Language": "it-IT,it;q=0.9",
                 "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    with urllib.request.urlopen(richiesta, timeout=8) as r:
        html = r.read().decode("utf-8", "replace")
    ids = []                                    # i video nell'ordine dei risultati, senza doppioni
    for v in re.findall(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html):
        if v not in ids:
            ids.append(v)
    return ids[n - 1] if len(ids) >= n else None


def _ricerca_youtube_recente():
    """Il testo dell'ultima ricerca su YouTube se è abbastanza recente, altrimenti None."""
    testo = _ultima_ricerca_youtube["testo"]
    if not testo or time.time() - _ultima_ricerca_youtube["quando"] > VALIDITA_RICERCA_YOUTUBE:
        return None
    return testo


def falla_partire(prova=False, n=1):
    testo = _ricerca_youtube_recente()
    if not testo:
        return "Non ho capito cosa far partire: prima cerca qualcosa su YouTube"
    if prova:
        return f"(prova) aprirebbe il risultato numero {n} di YouTube per: {testo}"
    try:
        video = _primo_video_youtube(testo, n)
    except Exception as e:
        return f"Non ho capito come far partire il risultato numero {n} ({e})"
    if not video:
        return f"Non ho capito quale sia il risultato numero {n}"
    _apri_url("https://www.youtube.com/watch?v=" + video)
    return f"Faccio partire il risultato numero {n} di: {testo}"


# "aprila", "aprilo", "apri il secondo video"... dopo una ricerca su YouTube: aprono quel risultato.
# Senza una ricerca recente su YouTube non fanno niente di diverso da prima (vanno avanti come sempre).
ORDINALI_RISULTATO = {"primo": 1, "prima": 1, "uno": 1, "1": 1, "secondo": 2, "seconda": 2, "due": 2, "2": 2,
                      "terzo": 3, "terza": 3, "tre": 3, "3": 3, "quarto": 4, "quarta": 4, "quattro": 4, "4": 4,
                      "quinto": 5, "quinta": 5, "cinque": 5, "5": 5}
_APRI_PRONOME = {"aprila", "aprilo", "aprimela", "aprimelo", "apriamola", "apriamolo", "aprile"}
_APRI_VERBO = {"apri", "aprire", "apriamo", "aprimi"}
_RIEMPITIVI_RISULTATO = {"eva", "vox", "ehi", "ei", "hey", "ok", "okay", "allora", "dai", "poi", "e", "per", "favore",
                         "adesso", "ora", "subito", "pure", "anche", "quindi", "mi", "puoi", "potresti", "vorrei",
                         "voglio", "grazie"}
_ARTICOLI_RISULTATO = {"il", "lo", "la", "l", "un", "uno", "una"}
_NOMI_RISULTATO = {"video", "risultato", "canzone", "link", "clip", "brano"}


def indice_risultato_youtube(testo):
    """Se la frase chiede di aprire un risultato dell'ultima ricerca YouTube ("aprila", "aprilo", "apri il primo
    video", "apri il secondo", "apri il terzo risultato"...) restituisce il numero del risultato (1, 2, 3...).
    Altrimenti None (anche se non c'è stata una ricerca su YouTube di recente)."""
    if _ricerca_youtube_recente() is None:
        return None
    parole = [p for p in comandi.normalizza(testo).split() if p not in _RIEMPITIVI_RISULTATO]
    if parole[-2:] == ["su", "youtube"]:             # "apri il secondo video su YouTube"
        parole = parole[:-2]
    if not parole or len(parole) > 5:
        return None
    if len(parole) == 1 and parole[0] in _APRI_PRONOME:
        return 1
    if len(parole) == 2 and (parole[0] in _APRI_VERBO and parole[1] in ("la", "lo")
                             or parole[1] in _APRI_VERBO and parole[0] in ("la", "lo")):
        return 1
    if parole[0] not in _APRI_VERBO:
        return None
    resto = [p for p in parole[1:] if p not in _ARTICOLI_RISULTATO or p == "uno"]
    numeri = [ORDINALI_RISULTATO[p] for p in resto if p in ORDINALI_RISULTATO]
    nomi = [p for p in resto if p in _NOMI_RISULTATO]
    if len(numeri) + len(nomi) != len(resto) or len(numeri) > 1 or not resto:
        return None                  # c'è altro (es. "apri il video progetto.mp4"): non è per i risultati YouTube
    return numeri[0] if numeri else 1


def riconosci_falla_partire(coppie):
    """'falla partire', 'fallo partire', 'fammela partire', 'fai partire'..."""
    norm = [n for _, n in coppie]
    if not norm or len(norm) > 4 or "partire" not in norm[-2:]:
        return None
    if not any(_punteggio(n, v) >= SOGLIA_SICURA for n in norm[:-1]
               for v in ("falla", "fallo", "fammela", "fammelo", "fai", "far")):
        return None
    return Match(1.0, 2, {}, "falla partire")


# --- Chiudere la finestra del browser / di Esplora file ----------------------
RisultatoDetto = namedtuple("RisultatoDetto", "tipo frase_corretta messaggio detto")
BROWSER_EXE = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
PAROLE_BROWSER = ["browser", "brauser", "chrome", "crome", "google", "edge", "firefox", "brave", "opera", "internet"]
PAROLE_CARTELLA = ["cartella", "cartelle", "esplora"]      # "la cartella", "esplora file/risorse"
PAROLE_FINESTRA = ["finestra", "finestre", "schermata"]    # "chiudi finestra": quella giusta (vedi chiudi_finestra_attiva)
SALUTI_INIZIALI = {"ehi", "ei", "hey", "ciao", "ok", "allora", "dai", "eva", "vox"}   # davanti a "chiudi ..."


def _finestre_visibili():
    """[(hwnd, classe, titolo, nome exe)] delle finestre visibili, dalla più in alto nello Z-order."""
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k.OpenProcess.restype = ctypes.c_void_p
    k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
                                             ctypes.POINTER(ctypes.c_ulong)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    trovate = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        titolo = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, titolo, n + 1)
        classe = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, classe, 256)
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = k.OpenProcess(0x1000, False, pid.value)            # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf, dim = ctypes.create_unicode_buffer(520), ctypes.c_ulong(520)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(dim)):
                exe = buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            k.CloseHandle(h)
        trovate.append((hwnd, classe.value, titolo.value, exe))
        return True

    u.EnumWindows(callback, 0)
    return trovate


def _chiudi_una(filtro, nessuna, etichetta, prova):
    """Chiude UNA finestra (WM_CLOSE, come premere la X): quella in primo piano se corrisponde,
    altrimenti la più in alto. Mai Eva (non è un browser né Esplora file)."""
    finestre = [f for f in _finestre_visibili() if filtro(f)]
    if not finestre:
        return None, nessuna
    primo = ctypes.windll.user32.GetForegroundWindow()
    scelta = next((f for f in finestre if f[0] == primo), finestre[0])
    if not prova:
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(scelta[0]), 0x0010, 0, 0)
    return scelta, f"{'(prova) chiuderei' if prova else 'Chiudo'} {etichetta}: {scelta[2]}"


def chiudi_browser(prova=False):
    return _chiudi_una(lambda f: f[3] in BROWSER_EXE, "Non c'è nessun browser aperto",
                       "la finestra del browser", prova)


def chiudi_cartella(prova=False):
    return _chiudi_una(lambda f: f[1] == "CabinetWClass" and f[3] == "explorer.exe",
                       "Non c'è nessuna cartella aperta", "la cartella", prova)


def chiudi_finestra_attiva(prova=False):
    """'chiudi finestra': chiude la finestra giusta = quella in primo piano se è un browser o una
    cartella, altrimenti il browser/cartella più in alto. Mai Eva né altri programmi."""
    return _chiudi_una(lambda f: f[3] in BROWSER_EXE or (f[1] == "CabinetWClass" and f[3] == "explorer.exe"),
                       "Non c'è nessuna finestra da chiudere", "la finestra", prova)


def chiudi_finestra(testo, esegui=True, nomi=()):
    """'chiudi il browser' / 'chiudi Chrome' / 'chiudi Google' / 'chiudi la cartella' / 'chiudi finestra':
    chiude SOLO quella finestra (mai Eva). `nomi` = parole del nome di Eva (anche "Eva, chiudi Chrome").
    Restituisce un risultato da dire, oppure None se la frase non è una di queste."""
    ignora = SALUTI_INIZIALI | {_norm_token(n) for n in nomi if len(n.split()) == 1}
    norm = [n for _, n in _coppie(testo)]
    while norm and norm[0] in ignora:                  # "Eva, chiudi Chrome" -> "chiudi Chrome"
        norm = norm[1:]
    if not 2 <= len(norm) <= 6 or max(_punteggio(norm[0], v) for v in ("chiudi", "chiudere", "chiudimi")) < SOGLIA_SICURA:
        return None
    resto = [n for n in norm[1:] if n not in ARTICOLI_VOCE and n not in RIEMPITIVI
             and n not in ("di", "del", "dei", "delle", "file", "risorse", "aperta", "aperto", "web")]
    if not resto or len(resto) > 2:
        return None

    def somiglia(parole):
        return any(_punteggio(n, p) >= SOGLIA_SICURA for n in resto for p in parole)

    if somiglia(PAROLE_BROWSER):
        corretta, azione, detto = "chiudi il browser", chiudi_browser, "Chiudo il browser."
    elif somiglia(PAROLE_CARTELLA):
        corretta, azione, detto = "chiudi la cartella", chiudi_cartella, "Chiudo la cartella."
    elif somiglia(PAROLE_FINESTRA):
        corretta, azione, detto = "chiudi la finestra", chiudi_finestra_attiva, "Chiudo la finestra."
    else:
        return None
    try:
        scelta, messaggio = azione(prova=not esegui)
    except Exception as e:
        return RisultatoDetto("non_trovato", corretta, f"Non riesco a chiuderla: {e}", "Non riesco a chiuderla.")
    if scelta is None:
        return RisultatoDetto("non_trovato", corretta, messaggio, messaggio + ".")
    return RisultatoDetto("eseguito", corretta, messaggio, detto)


# --- Mail (solo bozza, mai invio) ------------------------------------------
def _url_bozza(provider, email, oggetto, corpo):
    if provider == "posta":
        parametri = {k: v for k, v in (("subject", oggetto), ("body", corpo)) if v}
        uri = "mailto:" + quote(email, safe="@,")
        return uri + ("?" + urlencode(parametri, quote_via=quote) if parametri else "")
    parametri = {"view": "cm", "fs": "1"}
    for chiave, valore in (("to", email), ("su", oggetto), ("body", corpo)):
        if valore:
            parametri[chiave] = valore
    return "https://mail.google.com/mail/?" + urlencode(parametri, quote_via=quote)


def prepara_bozza_mail(provider, email, nome, oggetto, corpo, prova=False):
    """Apre una BOZZA. Non invia niente: l'invio resta a te."""
    url = _url_bozza(provider, email or "", oggetto, corpo)
    app = "app Posta" if provider == "posta" else "Gmail"
    messaggio = (f"(prova) aprirebbe una bozza in {app}: {url}" if prova
                 else f"Bozza aperta in {app} (NON inviata)")
    if not prova:
        if provider == "posta":
            _avvia(url)
        else:
            _apri_url(url)
    if nome and not email:
        messaggio += f"\nDestinatario non trovato: {nome}"
    return messaggio


# --- Impara un alias (nome detto -> app) -----------------------------------
def impara_alias(nome, app, prova=False):
    """Salva in alias_utente.json che 'nome' vuol dire 'app'.

    L'app deve essere una che il programma sa già aprire (alias, sito, cartella
    o app installata): così un alias non può mai puntare a qualcosa di arbitrario.
    """
    risolto = comandi.risolvi_nome(app)
    if not risolto:
        return f"Non conosco l'app '{app}': non ho salvato niente"
    chiave, descrizione = risolto
    if comandi.normalizza(nome) == chiave:
        return f"'{nome}' è già un nome conosciuto ({descrizione})"
    if prova:
        return f"(prova) salverebbe: '{nome}' = {descrizione}"
    comandi.salva_alias_utente(nome, chiave)
    return f"Imparato: '{nome}' = {descrizione} (salvato in alias_utente.json)"


# ======================================================================
# RICONOSCIMENTO: come si capisce che una frase è un certo comando
# ======================================================================
Match = namedtuple("Match", "punteggio specificita argomenti descrizione")

ARTICOLI_VOCE = {"il", "lo", "la", "l", "i", "gli", "le", "un", "uno", "una"}


def _norm_token(parola):
    parola = unicodedata.normalize("NFD", parola.lower())
    parola = "".join(c for c in parola if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", parola)


def _coppie(testo):
    """[(parola originale, parola normalizzata)]: l'originale serve per il testo
    da cercare o da scrivere nella mail (accenti e punteggiatura compresi)."""
    coppie = []
    for originale in testo.split():
        n = _norm_token(originale)
        if n:
            coppie.append((originale, n))
    return coppie


def _prepara(frase):
    """Parole della frase senza articoli né riempitivi."""
    return [p for p in (_norm_token(x) for x in frase.split())
            if p and p not in ARTICOLI_VOCE and p not in RIEMPITIVI]


def _punteggio_verbo(detto, atteso):
    """Come _punteggio, ma un verbo deve iniziare con la stessa lettera e avere
    lunghezza simile, altrimenti al massimo è 'incerto'."""
    s = _punteggio(detto, atteso)
    if detto[:1] != atteso[:1] or abs(len(detto) - len(atteso)) > 1:
        s = min(s, 0.7)
    return s


def frase(varianti):
    """Riconoscitore per comandi senza argomenti: 'alza il volume', 'muto'..."""
    preparate = [(_prepara(v), v) for v in varianti]

    def riconosci(coppie):
        norm = [p for p in (n for _, n in coppie) if p not in ARTICOLI_VOCE
                and p not in RIEMPITIVI]
        if not norm or any(p.isdigit() for p in norm):   # coi numeri: "volume al 50"
            return None
        migliore = None
        for parole, _originale in preparate:
            n = len(parole)
            if n == 1:                    # una parola sola: niente parole in più
                if len(norm) != 1:
                    continue
                s = _punteggio(norm[0], parole[0])
            else:
                extra = len(norm) - n
                if extra < 0 or extra > 2:
                    continue
                voluta = " ".join(parole)
                s = 0.0
                for i in range(extra + 1):
                    finestra = norm[i:i + n]
                    # ogni parola deve somigliare almeno un po' a quella prevista
                    if min(_punteggio(d, p) for d, p in zip(finestra, parole)) < 0.6:
                        continue
                    s = max(s, _punteggio(" ".join(finestra), voluta))
                s *= 0.9 ** extra
            if migliore is None or (s, n) > (migliore[0], migliore[1]):
                migliore = (s, n)
        if migliore is None or migliore[0] < SOGLIA_INCERTA:
            return None
        return Match(migliore[0], migliore[1], {}, None)

    return riconosci


def _numero(parola):
    if parola.isdigit() and 0 <= int(parola) <= 100:
        return int(parola)
    return NUMERI.get(parola)


def riconosci_volume_al(coppie):
    """'volume al 50', 'metti il volume a cinquanta'..."""
    norm = [n for _, n in coppie]
    if not 2 <= len(norm) <= 8:
        return None
    voci = [(_punteggio(p, "volume"), i) for i, p in enumerate(norm)]
    s, _ = max(voci)
    valori = [v for v in (_numero(p) for p in norm) if v is not None]
    if s < SOGLIA_INCERTA or not valori:
        return None
    return Match(s, 2, {"valore": valori[0]}, f"volume al {valori[0]}")


def riconosci_impara(coppie):
    """'impara <nome> è <app>' (anche 'uguale a', 'significa')."""
    norm = [n for _, n in coppie]
    if not 4 <= len(norm) <= 10:
        return None
    s = _punteggio_verbo(norm[0], "impara")
    if s < SOGLIA_INCERTA:
        return None
    sep = next((i for i in range(2, len(norm) - 1)
                if norm[i] in {"e", "uguale", "significa"}), None)
    if sep is None:
        return None
    app = norm[sep + 1:]
    while app and app[0] in {"a", "come", "l", "il", "lo", "la"}:
        app = app[1:]
    nome = " ".join(norm[1:sep])
    if not app or not nome or len(nome) > 40:
        return None
    return Match(s, 3, {"nome": nome, "app": " ".join(app)},
                 f"impara {nome} è {' '.join(app)}")


def riconosci_cerca(coppie):
    """'cerca <testo>', 'cerca su Google <testo>', 'cerca su YouTube <testo>'."""
    norm = [n for _, n in coppie]
    orig = [o for o, _ in coppie]
    if not norm:
        return None
    s = max(_punteggio_verbo(norm[0], v) for v in VERBI_CERCA)
    if s < SOGLIA_INCERTA:
        return None
    i, motore = 1, "google"
    if i < len(norm) and norm[i] in ("su", "in", "con"):
        for nome in MOTORI:
            if i + 1 < len(norm) and _punteggio(norm[i + 1], nome) >= SOGLIA_SICURA:
                motore, i = nome, i + 2
                break
        else:
            i += 1                      # "cerca su <testo>"
    resto_n, resto_o = norm[i:], orig[i:]
    # "cercami <canzone> su YouTube": il motore può stare anche alla fine
    if motore == "google" and len(resto_n) >= 2 and resto_n[-2] in ("su", "in", "con", "tramite") \
            and _punteggio(resto_n[-1], "youtube") >= SOGLIA_SICURA:
        motore, resto_n, resto_o = "youtube", resto_n[:-2], resto_o[:-2]
    if motore == "youtube":             # "una canzone che si chiama X" -> "X"
        k = 0
        while k < len(resto_n) and resto_n[k] in ARTICOLI_VOCE:
            k += 1
        if k < len(resto_n) and resto_n[k] in ("canzone", "brano", "video", "musica"):
            k += 1
            if resto_n[k:k + 3] == ["che", "si", "chiama"]:
                k += 3
            elif resto_n[k:k + 1] in (["chiamata"], ["intitolata"]):
                k += 1
            elif resto_n[k:k + 2] == ["dal", "titolo"]:
                k += 2
            if k < len(resto_n):
                resto_n, resto_o = resto_n[k:], resto_o[k:]
    testo = " ".join(resto_o).strip(" .,;:!?")
    return Match(s, 1, {"testo": testo, "motore": motore},
                 f"cerca su {motore} {testo}".strip())


# --- Mail: lettura di destinatario, oggetto e testo --------------------------
def _carica_rubrica():
    """Rubrica {nome normalizzato: email}. Si rilegge a ogni uso."""
    try:
        dati = json.loads(FILE_RUBRICA.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {comandi.normalizza(nome): email.strip().lower()
            for nome, email in dati.items()
            if not nome.startswith("_") and isinstance(email, str)
            and EMAIL.fullmatch(email.strip().lower())}


def _leggi_indirizzo(norm, orig):
    """Se all'inizio c'è un indirizzo (scritto o detto a voce con 'chiocciola')
    restituisce (indirizzo, parole usate), altrimenti None."""
    if not norm:
        return None
    if "@" in orig[0]:
        email = orig[0].strip(".,;:!?").lower()
        return (email, 1) if EMAIL.fullmatch(email) else None
    for i in range(min(len(norm), 8)):
        if _punteggio(norm[i], "chiocciola") >= SOGLIA_SICURA:
            break
    else:
        return None
    locale = "".join(SIMBOLI.get(p, p) for p in norm[:i])
    dominio, j = "", i + 1
    while j < len(norm):
        parola = norm[j]
        if parola in SIMBOLI:
            dominio += SIMBOLI[parola]
        elif dominio and not dominio.endswith((".", "-", "_")):
            break
        else:
            dominio += parola
        j += 1
    email = f"{locale}@{dominio}"
    return (email, j) if locale and EMAIL.fullmatch(email) else None


def _cerca_in_rubrica(nome):
    """Email del contatto più simile al nome detto, o None."""
    rubrica = _carica_rubrica()
    if not rubrica or not nome:
        return None
    migliore = max(rubrica, key=lambda k: _punteggio(nome, k))
    return rubrica[migliore] if _punteggio(nome, migliore) >= SOGLIA_SICURA else None


def _e_marcatore(parola, voluta):
    return _punteggio(parola, voluta) >= 0.85


def riconosci_mail(coppie):
    """'scrivi una mail a <nome> [oggetto ...] [dice|testo ...] [con posta]'."""
    norm = [n for _, n in coppie]
    orig = [o for o, _ in coppie]
    if len(norm) < 3:
        return None
    sv = max(_punteggio_verbo(norm[0], v) for v in VERBI_MAIL)
    i = 1
    while i < len(norm) and norm[i] in {"una", "un", "la", "le", "lo", "il", "e"}:
        i += 1
    if i >= len(norm):
        return None
    sm = max(_punteggio(norm[i], m) for m in PAROLE_MAIL)
    s = min(sv, sm)
    if s < SOGLIA_INCERTA:
        return None
    i += 1
    while i < len(norm) and norm[i] in {"a", "ad", "per", "al"}:
        i += 1
    r_norm, r_orig = norm[i:], orig[i:]

    # dove inizia il testo dettato ("dice" o "testo")
    mk = next((k for k, p in enumerate(r_norm)
               if _e_marcatore(p, "dice") or _e_marcatore(p, "testo")), None)
    testa_n = r_norm if mk is None else r_norm[:mk]
    testa_o = r_orig if mk is None else r_orig[:mk]
    corpo_o = None if mk is None else r_orig[mk + 1:]
    if corpo_o and mk is not None and _e_marcatore(r_norm[mk], "dice") \
            and _norm_token(corpo_o[0]) == "che":
        corpo_o = corpo_o[1:]

    # "con posta" / "con gmail" (solo prima del testo dettato)
    provider = MAIL_PREDEFINITA
    for k in range(len(testa_n)):
        if testa_n[k] != "con":
            continue
        j = k + 1
        if j < len(testa_n) and testa_n[j] in {"la", "l", "il", "lo"}:
            j += 1
        if j < len(testa_n):
            for nome, p in (("posta", "posta"), ("gmail", "gmail")):
                if _punteggio(testa_n[j], p) >= 0.85:
                    provider = nome
                    del testa_n[k:j + 1], testa_o[k:j + 1]
                    break
            else:
                continue
            break

    # oggetto (se detto prima del testo)
    og = next((k for k, p in enumerate(testa_n) if _e_marcatore(p, "oggetto")), None)
    oggetto = ""
    if og is not None:
        oggetto = " ".join(testa_o[og + 1:]).strip(" .,;:!?")
        testa_n, testa_o = testa_n[:og], testa_o[:og]

    # destinatario
    email, nome = None, ""
    letto = _leggi_indirizzo(testa_n, testa_o)
    if letto:
        email, usate = letto
        resto = testa_o[usate:]
    elif mk is not None or og is not None:          # il nome è tutto fino al marcatore
        nome = " ".join(testa_o)
        email, resto = _cerca_in_rubrica(" ".join(testa_n)), []
    else:                                            # "a Marco ciao come stai"
        resto = []
        migliore = (0.0, 0)
        rubrica = _carica_rubrica()
        for k in range(1, min(3, len(testa_n)) + 1):
            candidato = " ".join(testa_n[:k])
            for chiave in rubrica:
                migliore = max(migliore, (_punteggio(candidato, chiave), k))
        if migliore[0] >= SOGLIA_SICURA:
            k = migliore[1]
            email, nome = _cerca_in_rubrica(" ".join(testa_n[:k])), " ".join(testa_o[:k])
            resto = testa_o[k:]
        elif testa_o:
            nome, resto = testa_o[0], testa_o[1:]
    corpo_parole = list(corpo_o if corpo_o is not None else resto)

    # "... con posta" detto in fondo, dopo il testo: vale come scelta dell'app
    fine = [_norm_token(p) for p in corpo_parole]
    for lunghezza in (3, 2):
        if len(fine) >= lunghezza and fine[-lunghezza] == "con" and (
                lunghezza == 2 or fine[-2] in {"la", "l", "il", "lo"}):
            for nome_app in ("posta", "gmail"):
                if _punteggio(fine[-1], nome_app) >= 0.85:
                    provider = nome_app
                    del corpo_parole[-lunghezza:]
                    break
            else:
                continue
            break
    corpo = " ".join(corpo_parole).strip()

    chi = email or nome
    descrizione = "scrivi una mail" + (f" a {chi}" if chi else "")
    if oggetto:
        descrizione += f" oggetto {oggetto}"
    if corpo:
        descrizione += f" dice {corpo}"
    return Match(s, 3, {"provider": provider, "email": email, "nome": nome,
                        "oggetto": oggetto, "corpo": corpo}, descrizione)


# ======================================================================
# ELENCO DEI COMANDI (da modificare per aggiungerne altri)
#   nome      : come viene mostrato in "Forse intendevi: ..."
#   riconosci : funzione che dice se la frase corrisponde (e quanto bene)
#   azione    : funzione eseguita; riceve gli argomenti trovati + prova=...
# ======================================================================
ELENCO_COMANDI = [
    {"nome": "alza il volume", "riconosci": frase(VARIANTI_VOLUME_SU), "azione": alza_volume},
    {"nome": "abbassa il volume", "riconosci": frase(VARIANTI_VOLUME_GIU), "azione": abbassa_volume},
    {"nome": "volume al <numero>", "riconosci": riconosci_volume_al, "azione": volume_al},
    {"nome": "muto", "riconosci": frase(VARIANTI_MUTO), "azione": metti_muto},
    {"nome": "togli il muto", "riconosci": frase(VARIANTI_TOGLI_MUTO), "azione": togli_muto},
    {"nome": "cerca <testo>", "riconosci": riconosci_cerca, "azione": cerca_su_web},
    {"nome": "falla partire", "riconosci": riconosci_falla_partire, "azione": falla_partire},
    {"nome": "scrivi una mail", "riconosci": riconosci_mail, "azione": prepara_bozza_mail},
    {"nome": "impara <nome> è <app>", "riconosci": riconosci_impara, "azione": impara_alias},
]


# ======================================================================
# CODICE PRINCIPALE
# ======================================================================
def precarica():
    """Prepara tutto prima del primo comando:
    (correzioni, app trovate, pycaw sì/no, alias personali)."""
    correzioni, app = comandi.precarica()
    return correzioni, app, pycaw_disponibile(), len(comandi._alias_utente)


MAX_PAROLE_OGGETTO = 4      # "apri ..." con un resto più lungo di così, e non noto, è una chiacchiera


def _apri_ma_non_ordine(testo):
    """True se la frase INIZIA come "apri ..." ma il resto non è un programma, file o cartella NOTO e sembra
    parlato: è lungo (più di MAX_PAROLE_OGGETTO parole) oppure contiene il nome "Eva". Una frase così è una
    chiacchiera (o una domanda), non un ordine: non si cerca sul disco e non si divide in più cose da aprire."""
    try:
        import vox_file
        frase = comandi.applica_correzioni(comandi.normalizza(testo))
        parole = frase.split()
        while parole and (parole[0] in vox_file.RIEMPITIVI_INIZIO or parole[0] in ("eva", "vox")):
            parole.pop(0)
        if not parole:
            return False
        verbo, n = vox_file._verbo(parole)
        if verbo != "apri":
            v = comandi.riconosci_verbo(parole)            # anche storpiato ("appure", "abri"...)
            if v is None:
                return False
            n = v[1]
        oggetto = [p for p in parole[n:] if p not in comandi.ARTICOLI]
        if not oggetto:
            return False
        sospetto = len(oggetto) > MAX_PAROLE_OGGETTO or any(p in ("eva", "vox") for p in oggetto)
        return sospetto and not vox_file.oggetto_noto(" ".join(oggetto))
    except Exception:
        return False


def interpreta(testo, esegui=True):
    """Interpreta la frase ed esegue il comando (se esegui=True).

    Restituisce Risultato(tipo, frase_corretta, messaggio):
      "eseguito", "incerto", "non_trovato" oppure "testo" (non è un comando).
    """
    # "aprila" / "apri il secondo video" subito dopo una ricerca su YouTube: apre quel risultato
    n_risultato = indice_risultato_youtube(testo)
    if n_risultato is not None:
        messaggio = falla_partire(prova=not esegui, n=n_risultato)
        return Risultato("non_trovato" if messaggio.startswith(("Non ho capito", "Non conosco")) else "eseguito",
                         f"apri il risultato {n_risultato} di YouTube", messaggio)

    # "apri ..." seguito da un lungo discorso che non è niente di noto: NON è un ordine (niente ricerca sul disco)
    if _apri_ma_non_ordine(testo):
        return Risultato("testo", comandi.applica_correzioni(comandi.normalizza(testo)), "")

    # Cartelle e file (vox_file.py): "apri la cartella...", "vai su", "torna indietro",
    # "apri Chrome e blocco note"... Se non è una frase per loro, si va avanti come sempre.
    try:
        import vox_file
        r = vox_file.prova_multipla(testo, esegui, interpreta) or vox_file.prova(testo, esegui)
        if r is not None:
            return r
    except Exception:
        import traceback
        print("[cartelle] errore, uso i comandi normali:\n" + traceback.format_exc(), flush=True)

    normale = comandi.normalizza(testo)
    corretta = comandi.applica_correzioni(normale)
    if corretta != normale:                    # correzioni.json ha la precedenza
        coppie = [(p, p) for p in corretta.split()]
    else:
        coppie = _coppie(testo)

    trovati = []
    for ordine, comando in enumerate(ELENCO_COMANDI):
        m = comando["riconosci"](coppie)
        if m:
            trovati.append((m, ordine, comando))
    if not trovati:
        return comandi.gestisci_frase(testo, esegui)

    trovati.sort(key=lambda t: (-t[0].punteggio, -t[0].specificita, t[1]))
    m, _, comando = trovati[0]
    descrizione = m.descrizione or comando["nome"]

    ambiguo = (len(trovati) > 1 and m.punteggio < 0.999
               and trovati[1][0].punteggio >= m.punteggio - MARGINE_PARI
               and trovati[1][2]["azione"] is not comando["azione"])
    if m.punteggio < SOGLIA_SICURA or ambiguo:
        return Risultato("incerto", descrizione, f"Forse intendevi: {descrizione}")

    messaggio = comando["azione"](**m.argomenti, prova=not esegui)
    if messaggio.startswith(("Non ho capito", "Non conosco")):
        return Risultato("non_trovato", descrizione, messaggio)
    return Risultato("eseguito", descrizione, messaggio)
