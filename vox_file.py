"""Eva sfoglia il computer: cartelle e file, a voce.

  - Indice di cartelle e file (Desktop, Documenti, Scaricati, Immagini, Musica, Video, OneDrive),
    costruito in un thread separato e salvato in indice_cartelle.json (il secondo avvio è immediato).
  - Comandi: "apri la cartella <nome>", "apri <nome> sul desktop", "vai su <nome>",
    "apri il file <nome>", "cerca la cartella <nome>", "apri la cartella fatture dentro",
    "vai su" (cartella superiore), "torna indietro", "aggiorna l'indice",
    "impara questa cartella <nome> è <cartella>", "chiama <cartella> <nuovo nome>",
    "apri Chrome e blocco note" (più cose insieme).
  - Corrispondenza approssimata (difflib) che ignora maiuscole, accenti, punteggiatura e ordine
    delle parole ("Katia documenti" trova "documente katia").

SICUREZZA: questo modulo SOLO apre (os.startfile / Esplora risorse). Non cancella, non sposta,
non rinomina, non copia, non modifica niente, non invia niente. I file eseguibili e gli script
(.exe, .bat, .cmd, .ps1, .vbs, .msi, .py ...) non si aprono mai a voce.
"""
import collections
import ctypes
import difflib
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import eva_percorsi as percorsi
import vox_cervello as cervello
from comandi import ARTICOLI, applica_correzioni, normalizza, registra_non_trovato

AQUI = percorsi.CARTELLA_DATI
FILE_INDICE = percorsi.dati("indice_cartelle.json")
FILE_ALIAS = percorsi.dati("alias_cartelle.json")

# --- impostazioni (le ridefinisce dettatura.py tramite configura()) ---
IMP = {
    "PROFONDITA_INDICE": 6,          # livelli di sottocartelle da indicizzare
    "MAX_CARTELLE_INDICE": 40000,    # numero massimo di cartelle nell'indice
    "MAX_FILE_INDICE": 80000,        # numero massimo di file nell'indice
    "AGGIORNA_INDICE_MINUTI": 10,    # ogni quanti minuti l'indice si aggiorna da solo
    "SOGLIA_CARTELLE": 0.78,         # somiglianza minima (0..1) per accettare una cartella o un file
    "TEMPO_RICERCA_DISCO": 5,        # secondi al massimo per la ricerca di riserva sul disco
    "NAV_VALIDITA_SECONDI": 600,     # per quanto tempo l'"ultima cartella aperta" ha la precedenza
}
MARGINE_PARI = 0.04                  # entro questo margine due risultati sono "simili": chiedo quale
SCELTA_VALIDITA = 60                 # secondi per rispondere "la prima / la seconda / la terza"

ESCLUSE = {"node_modules", ".git", "__pycache__", "appdata", "$recycle.bin", "system volume information",
           ".venv", "venv", "site-packages", ".idea", ".gradle", "windows", "programdata"}
ATTR_NASCOSTO, ATTR_SISTEMA = 0x2, 0x4

# File che NON si aprono mai a voce
ESTENSIONI_VIETATE = {"exe", "bat", "cmd", "ps1", "psm1", "vbs", "vbe", "msi", "msp", "com", "scr", "js", "jse",
                      "wsf", "wsh", "reg", "hta", "lnk", "inf", "jar", "py", "pyw", "sh", "dll", "sys", "cpl",
                      "msc", "appx", "msix", "application", "gadget", "pif", "url", "scf", "chm"}

_log = print


def configura(imp, log=None):
    global _log
    for k in IMP:
        if k in imp:
            IMP[k] = imp[k]
    if log is not None:
        _log = log


def _dire(msg):
    try:
        _log(msg)
    except Exception:
        pass


# ======================================================================
# Utilità
# ======================================================================
def _n(testo):
    """Minuscolo, senza accenti, solo lettere e numeri separati da uno spazio."""
    t = unicodedata.normalize("NFD", testo.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _punteggio(qs, qt, ns, nt):
    """Somiglianza tra la richiesta (stringa qs, parole qt) e un nome (ns, nt). Da 0 a 1.
    Considera il testo intero, le parole in ordine diverso e la copertura delle parole."""
    if not qs or not ns:
        return 0.0
    s_seq = _sim(qs, ns)
    s_ord = _sim(" ".join(sorted(qt)), " ".join(sorted(nt)))
    cop = sum(max(_sim(t, u) for u in nt) for t in qt) / len(qt)
    fattore = min(1.0, len(qt) / len(nt)) ** 0.15      # una parola su tre ("uccello" in "uccello volante firma") conta ancora
    return max(s_seq, s_ord, cop * fattore)


def _cartella_nota(guid, predefinita):
    """Percorso reale di una cartella speciale di Windows (qualunque lingua, anche spostata)."""
    try:
        from ctypes import wintypes
        u = uuid.UUID(guid)

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                        ("d", ctypes.c_ubyte * 8)]
        g = GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(*(bytes([u.clock_seq_hi_variant, u.clock_seq_low]) + u.node.to_bytes(6, "big"))))
        f = ctypes.windll.shell32.SHGetKnownFolderPath
        f.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        ptr = ctypes.c_void_p()
        if f(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0 and ptr.value:
            percorso = ctypes.wstring_at(ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            if percorso and os.path.isdir(percorso):
                return percorso
    except Exception:
        pass
    p = Path.home() / predefinita
    return str(p) if p.is_dir() else None


def cartelle_utente():
    """{'desktop': percorso, 'documenti': ..., 'scaricati': ..., 'immagini': ..., 'musica': ..., 'video': ...,
    'onedrive': ...} (solo quelle che esistono davvero)."""
    nomi = {
        "desktop": ("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop"),
        "documenti": ("FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents"),
        "scaricati": ("374DE290-123F-4565-9164-39C4925E467B", "Downloads"),
        "immagini": ("33E28130-4E1E-4676-835A-98395C3BC3BB", "Pictures"),
        "musica": ("4BD8D571-6D19-48D3-BE97-422220080E43", "Music"),
        "video": ("18989B1D-99B5-455B-841C-AB7C74E4DDFC", "Videos"),
    }
    out = {}
    for chiave, (guid, predef) in nomi.items():
        p = _cartella_nota(guid, predef)
        if p:
            out[chiave] = p
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        p = os.environ.get(var)
        if p and os.path.isdir(p):
            out["onedrive"] = p
            break
    return out


# ======================================================================
# Indice
# ======================================================================
class Indice:
    """Nomi di cartelle e file, con una ricerca veloce (parole e trigrammi, poi difflib)."""

    def __init__(self, cartelle=(), file=(), creato=0.0):
        self.creato = creato
        self.voci = []                          # (percorso, nome_norm, parole, tipo, estensione)
        self.parola_ids = collections.defaultdict(list)
        self.trigr = collections.defaultdict(list)
        self.cache_simili = {}
        for p in cartelle:
            self._aggiungi(p, "d")
        for p in file:
            self._aggiungi(p, "f")
        for parola in self.parola_ids:
            for g in {parola[i:i + 3] for i in range(len(parola) - 2)}:
                self.trigr[g].append(parola)
        self.n_cartelle = sum(1 for v in self.voci if v[3] == "d")
        self.n_file = len(self.voci) - self.n_cartelle

    def _aggiungi(self, percorso, tipo):
        nome = os.path.basename(percorso)
        ext = ""
        if tipo == "f":
            nome, punto, e = nome.rpartition(".")
            if punto:
                ext = e.lower()
            else:
                nome = e
        ns = _n(nome)
        parole = tuple(ns.split())
        if not parole:
            return
        i = len(self.voci)
        self.voci.append((percorso, ns, parole, tipo, ext))
        for p in set(parole):
            self.parola_ids[p].append(i)

    def _simili(self, t):
        if t in self.cache_simili:
            return self.cache_simili[t]
        if len(t) < 3:
            ris = [t] if t in self.parola_ids else []
        else:
            tri = {t[i:i + 3] for i in range(len(t) - 2)}
            conta = collections.Counter()
            for g in tri:
                for u in self.trigr.get(g, ()):
                    conta[u] += 1
            minimo = max(1, len(tri) * 0.4)
            ris = [u for u, c in conta.items() if c >= minimo and (u == t or _sim(t, u) >= 0.68)]
        if len(self.cache_simili) > 5000:
            self.cache_simili.clear()
        self.cache_simili[t] = ris
        return ris

    def cerca(self, richiesta, tipo=None, estensione="", prefissi=None, soglia=0.78, massimo=1500):
        """Elenco [(punteggio, percorso, tipo)] ordinato dal migliore.
        tipo: "d" cartelle, "f" file, None entrambi. prefissi: solo dentro queste cartelle."""
        qs = _n(richiesta)
        qt = qs.split()
        if not qt:
            return []
        voti = collections.Counter()
        for t in qt:
            for u in self._simili(t):
                for i in self.parola_ids[u][:5000]:
                    voti[i] += 1
        ris = []
        for i, _ in voti.most_common(massimo * 3 if prefissi else massimo):
            percorso, ns, nt, tp, ext = self.voci[i]
            if tipo and tp != tipo:
                continue
            if estensione and ext != estensione:
                continue
            if prefissi and not any(_dentro(percorso, pr) for pr in prefissi):
                continue
            p = _punteggio(qs, qt, ns, nt)
            if p >= soglia:
                ris.append((p, percorso, tp))
        ris.sort(key=lambda r: (-round(r[0], 3), r[1].count(os.sep), len(r[1])))
        return ris

    def nomi_primo_livello(self, radice, massimo=12):
        base = os.path.normcase(radice)
        out = []
        for percorso, ns, nt, tp, ext in self.voci:
            if tp == "d" and os.path.normcase(os.path.dirname(percorso)) == base and len(ns) <= 30:
                out.append(os.path.basename(percorso))
        return out[:massimo]


def _dentro(percorso, cartella):
    a, b = os.path.normcase(percorso), os.path.normcase(cartella).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def _scansiona(radici, profondita, max_cartelle, max_file, ferma=None):
    """Percorso in ampiezza (prima i livelli alti). Salta nascosti, di sistema e le cartelle escluse."""
    cartelle, file = [], []
    coda = collections.deque((r, 0) for r in radici)
    visti = set()
    contatore = 0
    while coda:
        if ferma is not None and ferma.is_set():
            break
        base, livello = coda.popleft()
        chiave = os.path.normcase(base)
        if chiave in visti:
            continue
        visti.add(chiave)
        try:
            with os.scandir(base) as it:
                voci = list(it)
        except OSError:
            continue
        contatore += 1
        if contatore % 100 == 0:
            time.sleep(0.002)                       # lascia respirare il resto del programma
        for e in voci:
            nome = e.name
            if nome.startswith((".", "$")) or nome.lower() in ESCLUSE:
                continue
            try:
                attr = e.stat(follow_symlinks=False).st_file_attributes
                if attr & (ATTR_NASCOSTO | ATTR_SISTEMA):
                    continue
                e_dir = e.is_dir(follow_symlinks=False)
                if e_dir and (e.is_symlink() or e.is_junction()):
                    continue
            except OSError:
                continue
            if e_dir:
                if len(cartelle) < max_cartelle:
                    cartelle.append(e.path)
                    if livello + 1 < profondita:
                        coda.append((e.path, livello + 1))
            elif len(file) < max_file:
                file.append(e.path)
        if len(cartelle) >= max_cartelle and len(file) >= max_file:
            break
    return cartelle, file


class GestoreIndice:
    """Tiene l'indice aggiornato in un thread separato."""

    def __init__(self):
        self.indice = None                      # Indice pronto all'uso (o None)
        self.in_corso = False
        self.fermo = threading.Event()
        self.aggiorna_ora = threading.Event()
        self.thread = None
        self.radici = {}

    def avvia(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._ciclo, name="vox-indice", daemon=True)
        self.thread.start()

    def ferma(self):
        self.fermo.set()
        self.aggiorna_ora.set()

    def richiedi_aggiornamento(self):
        self.aggiorna_ora.set()

    def _carica_da_file(self):
        try:
            dati = json.loads(FILE_INDICE.read_text(encoding="utf-8"))
            if dati.get("versione") != 1:
                return None
            return Indice(dati["cartelle"], dati["file"], dati.get("creato", 0.0)), dati.get("radici", {})
        except Exception:
            return None

    def _salva(self, indice, cartelle, file):
        try:
            tmp = FILE_INDICE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"versione": 1, "creato": indice.creato, "radici": self.radici,
                                       "cartelle": cartelle, "file": file}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, FILE_INDICE)
        except OSError:
            _dire("Non riesco a salvare indice_cartelle.json (l'indice resta in memoria).")

    def _ricostruisci(self):
        self.in_corso = True
        t0 = time.time()
        try:
            self.radici = cartelle_utente()
            percorsi = list(self.radici.values())
            percorsi = [p for p in percorsi
                        if not any(q != p and _dentro(p, q) for q in percorsi)]   # niente cartelle dentro cartelle
            cartelle, file = _scansiona(percorsi, IMP["PROFONDITA_INDICE"], IMP["MAX_CARTELLE_INDICE"],
                                        IMP["MAX_FILE_INDICE"], self.fermo)
            if self.fermo.is_set():
                return
            cartelle += [p for p in percorsi if p not in cartelle]
            nuovo = Indice(cartelle, file, time.time())
            self.indice = nuovo
            self._salva(nuovo, cartelle, file)
            _dire(f"Indice: {nuovo.n_cartelle} cartelle, {nuovo.n_file} file "
                  f"(aggiornato in {time.time() - t0:.1f} s)")
        except Exception:
            try:
                _log.errore("Errore nella costruzione dell'indice")
            except Exception:
                pass
        finally:
            self.in_corso = False

    def _ciclo(self):
        pausa = max(1, IMP["AGGIORNA_INDICE_MINUTI"]) * 60
        t0 = time.time()
        caricato = self._carica_da_file()
        if caricato:
            self.indice, self.radici = caricato
            _dire(f"Indice: {self.indice.n_cartelle} cartelle, {self.indice.n_file} file "
                  f"(caricato dal file in {time.time() - t0:.1f} s, lo aggiorno in background)")
        else:
            _dire("Preparo l'indice di cartelle e file (solo la prima volta ci vuole un po')...")
        if caricato and time.time() - self.indice.creato < pausa:
            # l'indice salvato è recente: aspetto il prossimo giro (o "aggiorna l'indice")
            self.aggiorna_ora.wait(timeout=max(1.0, pausa - (time.time() - self.indice.creato)))
        while not self.fermo.is_set():
            self._ricostruisci()
            self.aggiorna_ora.clear()
            self.aggiorna_ora.wait(timeout=pausa)


_gestore = GestoreIndice()


def avvia_indice(imp, log):
    configura(imp, log)
    carica_alias()
    _gestore.avvia()


def ferma_indice():
    _gestore.ferma()


def nomi_per_prompt(massimo=12):
    """Nomi delle cartelle sul Desktop: aiutano Whisper a scrivere bene i nomi (es. "Katia")."""
    ind = _gestore.indice
    base = _gestore.radici.get("desktop")
    return ind.nomi_primo_livello(base, massimo) if ind and base else []


# ======================================================================
# Alias a voce (alias_cartelle.json)
# ======================================================================
_alias = {}


def carica_alias():
    global _alias
    try:
        dati = json.loads(FILE_ALIAS.read_text(encoding="utf-8"))
        _alias = {_n(k): v for k, v in dati.get("alias", {}).items() if isinstance(v, str)}
    except Exception:
        _alias = {}


def _salva_alias(nome, percorso):
    try:
        dati = {}
        if FILE_ALIAS.exists():
            dati = json.loads(FILE_ALIAS.read_text(encoding="utf-8"))
        dati.setdefault("alias", {})[nome] = percorso
        dati["_aiuto"] = ("nome che dici -> percorso della cartella. Si crea a voce: «impara questa cartella "
                          "<nome> è <cartella>» oppure «chiama <cartella> <nuovo nome>».")
        tmp = FILE_ALIAS.with_suffix(".tmp")
        tmp.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, FILE_ALIAS)
        _alias[_n(nome)] = percorso
        return True
    except OSError:
        return False


# ======================================================================
# Stato della navigazione e delle scelte
# ======================================================================
_nav = {"ultima": None, "tempo": 0.0, "storia": []}
_pendente = {"opzioni": [], "scade": 0.0}


class RisultatoFile(collections.namedtuple("RisultatoFile", "tipo frase_corretta messaggio detto cosa")):
    """Come comandi.Risultato, con in più la frase da dire a voce (detto) e il nome breve (cosa).
    tipo: "eseguito", "scelta" (chiedo quale), "non_trovato", "incerto"."""
    __slots__ = ()


def _r(tipo, corretta, messaggio, detto="", cosa=""):
    return RisultatoFile(tipo, corretta, messaggio, detto, cosa)


NOMI_PARLATI = {"shell:RecycleBinFolder": "cestino", "shell:MyComputerFolder": "questo PC", "C:\\": "disco C"}
ETICHETTE = {"desktop": "Desktop", "documenti": "Documenti", "scaricati": "Scaricati", "immagini": "Immagini",
             "musica": "Musica", "video": "Video", "onedrive": "OneDrive"}


def _bello(percorso):
    """Nome da dire a voce. Le cartelle di Windows hanno il nome italiano (Documents -> Documenti)."""
    if percorso in NOMI_PARLATI:
        return NOMI_PARLATI[percorso]
    k = os.path.normcase(percorso.rstrip("\\/"))
    for chiave, p in cartelle_utente().items():
        if os.path.normcase(p) == k:
            return ETICHETTE.get(chiave, chiave)
    nome =os.path.basename(percorso.rstrip("\\/")) or percorso
    return nome if len(nome) <= 60 else nome[:57] + "..."


def _stem(percorso, tipo):
    nome = _bello(percorso)
    if tipo == "f" and "." in nome:
        nome = nome.rsplit(".", 1)[0]
    return nome


def _ricorda_cartella(percorso):
    if _nav["ultima"] and os.path.normcase(_nav["ultima"]) != os.path.normcase(percorso):
        _nav["storia"] = (_nav["storia"] + [_nav["ultima"]])[-20:]
    _nav["ultima"] = percorso
    _nav["tempo"] = time.time()


# ======================================================================
# Cartelle speciali sempre disponibili
# ======================================================================
def _speciali():
    c = cartelle_utente()
    voci = {}

    def agg(nomi, percorso, tipo="d", solo_esplicito=False):
        for n in nomi:
            voci[_n(n)] = (percorso, tipo, solo_esplicito)

    for chiave, nomi in {"desktop": ["desktop", "scrivania"],
                         "documenti": ["documenti", "i miei documenti"],
                         "scaricati": ["scaricati", "download", "downloads"],
                         "immagini": ["immagini", "le immagini"],
                         "musica": ["musica"], "video": ["video", "filmati"]}.items():
        if chiave in c:
            agg(nomi, c[chiave])
    if "immagini" in c:
        agg(["foto"], c["immagini"], solo_esplicito=True)    # "apri foto" resta l'app Foto
    if "onedrive" in c:
        agg(["onedrive", "one drive"], c["onedrive"])
    agg(["cestino"], "shell:RecycleBinFolder", "s")
    agg(["questo pc", "risorse del computer", "il mio computer", "computer"], "shell:MyComputerFolder", "s")
    agg(["disco c", "disco ci", "unita c", "drive c", "il disco c"], "C:\\")
    return voci


def _cerca_speciale(nome_n, esplicito):
    if not nome_n:
        return None
    migliore = None
    for chiave, (percorso, tipo, solo_esplicito) in _speciali().items():
        if solo_esplicito and not esplicito:
            continue
        s = _sim(nome_n, chiave)
        if s >= 0.85 and (migliore is None or s > migliore[0]):
            migliore = (s, percorso, tipo)
    return migliore


LUOGHI = {"desktop": "desktop", "scrivania": "desktop", "documenti": "documenti", "scaricati": "scaricati",
          "download": "scaricati", "downloads": "scaricati", "immagini": "immagini", "musica": "musica",
          "video": "video", "onedrive": "onedrive"}
RE_LUOGO = re.compile(r"(?:^|\s)(?:sul|sulla|sullo|nel|nella|nello|nei|nelle|negli|in|su|dentro|da)\s+"
                      r"(?:(?:il|la|lo|i|le|l|gli)\s+)?(desktop|scrivania|documenti|scaricati|download|"
                      r"downloads|immagini|musica|video|onedrive)$")
RE_LUOGO_INIZIO = re.compile(r"^(?:sul|sulla|sullo|nel|nella|nello|nei|nelle|negli|in|su|dentro|da)\s+"
                             r"(?:(?:il|la|lo|i|le|l|gli)\s+)?(desktop|scrivania|documenti|scaricati|download|"
                             r"downloads|immagini|musica|video|onedrive)\s+")
DENTRO = ("dentro", "qui", "qua", "all interno", "li dentro")


# ======================================================================
# Apertura (SOLO aprire)
# ======================================================================
def _estensione(percorso):
    return os.path.splitext(percorso)[1].lstrip(".").lower()


def _apri(percorso, tipo):
    """Apre una cartella o un file. Solleva un errore se non è consentito o non esiste."""
    if tipo == "s":
        subprocess.Popen(["explorer.exe", percorso])
        return
    if tipo == "d":
        if not os.path.isdir(percorso):
            raise FileNotFoundError(percorso)
        os.startfile(percorso)
        return
    if _estensione(percorso) in ESTENSIONI_VIETATE:
        raise PermissionError("programma o script")
    if not os.path.isfile(percorso):
        raise FileNotFoundError(percorso)
    os.startfile(percorso)


def _e_vietato(percorso, tipo):
    return tipo == "f" and _estensione(percorso) in ESTENSIONI_VIETATE


# ======================================================================
# Ricerca di riserva sul disco (tempo limitato)
# ======================================================================
def _cerca_disco(richiesta, tipo, estensione, secondi):
    qs = _n(richiesta)
    qt = qs.split()
    if not qt:
        return []
    chiavi = [t[:3] for t in qt if len(t) >= 3] or qt
    soglia = IMP["SOGLIA_CARTELLE"]
    scadenza = time.time() + secondi
    radici = [str(Path.home())]
    try:
        radici += [d for d in os.listdrives()]
    except Exception:
        radici.append("C:\\")
    coda = collections.deque((r, 0) for r in radici)
    visti, ris = set(), []
    saltate = {"windows", "program files", "program files (x86)", "programdata", "appdata"}
    while coda and time.time() < scadenza:
        base, livello = coda.popleft()
        k = os.path.normcase(base)
        if k in visti:
            continue
        visti.add(k)
        try:
            with os.scandir(base) as it:
                voci = list(it)
        except OSError:
            continue
        for e in voci:
            nome = e.name
            bassa = nome.lower()
            if nome.startswith((".", "$")) or bassa in ESCLUSE or bassa in saltate:
                continue
            try:
                e_dir = e.is_dir(follow_symlinks=False)
                if e_dir and (e.is_symlink() or e.is_junction()):
                    continue
                if e.stat(follow_symlinks=False).st_file_attributes & (ATTR_NASCOSTO | ATTR_SISTEMA):
                    continue
            except OSError:
                continue
            tp = "d" if e_dir else "f"
            if (not tipo or tp == tipo) and (not estensione or _estensione(nome) == estensione):
                base_nome = nome.rpartition(".")[0] if (tp == "f" and "." in nome) else nome
                ns = _n(base_nome)
                if ns and any(c in ns for c in chiavi):
                    p = _punteggio(qs, qt, ns, tuple(ns.split()))
                    if p >= soglia:
                        ris.append((p, e.path, tp))
            if e_dir and livello < 8:
                coda.append((e.path, livello + 1))
    ris.sort(key=lambda r: (-round(r[0], 3), r[1].count(os.sep)))
    return ris


# ======================================================================
# Capire la frase
# ======================================================================
# Verbi di apertura (si riconoscono anche storpiati, con difflib). Le forme con "mi", "puoi", "per favore"...
# si ottengono togliendo prima i riempitivi (vedi _riempitivi): "mi apre" -> "apre", "puoi aprire" -> "aprire".
VERBI = {"apri": ["apri", "apre", "aprire", "apro", "aprimi", "apriamo", "avvia", "avviami", "lancia",
                  "mostra", "mostrami", "vedere", "vedi", "visualizza"],
         "cerca": ["cerca", "cercami", "cercare", "trova", "trovami", "trovare"],
         "vai": ["vai", "andiamo", "portami", "porta", "naviga"]}       # "vai su / in / al / alla / sul"
NON_VERBI = {"aprile", "vedo", "vedono"}                                # parole simili ma diverse
MARCATORI = {"cartella": "d", "cartelle": "d", "directory": "d", "folder": "d",
             "file": "f", "files": "f", "documento": "f", "documenti": None}
SU_PAROLE = {"su", "sul", "sulla", "sullo", "sui", "nella", "nel", "nello", "nei", "in", "a", "al", "alla",
             "allo", "ai", "alle"}
# Parole di riempimento: non cambiano il senso ("mi apre la cartella di Katia, per favore")
RIEMPITIVI_FRASE = ["per favore", "per piacere", "per cortesia", "e se vai", "se vai", "se puoi", "se riesci"]
RIEMPITIVI_INIZIO = {"mi", "ti", "ci", "allora", "ecco", "poi", "dai", "senti", "scusa", "puoi", "potresti",
                     "vorrei", "voglio", "vuoi", "fammi", "e", "ma", "beh", "ehi"}
RIEMPITIVI_FINE = {"per", "favore", "grazie", "allora", "ecco", "poi", "e"}
# Verbi che NON eseguo mai (Eva solo apre): se compaiono con "cartella" o "file" rifiuto
VERBI_VIETATI = {"cancella", "cancellare", "cancello", "elimina", "eliminare", "elimino", "rimuovi", "rimuovere",
                 "rimuovo", "sposta", "spostare", "sposto", "copia", "copiare", "copio", "rinomina", "rinominare",
                 "rinomino", "modifica", "modificare", "incolla", "taglia", "svuota", "formatta", "installa",
                 "installare", "scarica", "comprimi", "decomprimi"}
_ctx = {"frase": ""}              # l'ultima frase capita (serve per non_trovati.txt)
SU = {"vai su", "sali", "risali", "cartella superiore", "livello superiore", "vai alla cartella superiore",
      "vai nella cartella superiore", "cartella sopra", "sali di un livello", "vai al livello superiore"}
INDIETRO = {"torna indietro", "indietro", "torna alla cartella precedente", "cartella precedente",
            "vai indietro", "torna alla precedente", "torna a prima"}


def _verbo(parole):
    if not parole:
        return None, 0
    w = parole[0]
    if w in NON_VERBI:
        return None, 0
    for nome, lista in VERBI.items():
        # forma esatta, oppure storpiata ma vicina: stessa lettera iniziale e al massimo 1 lettera di differenza
        # nella lunghezza ("abri", "apre"). "appure", "oppure", "aprirà" NON sono il verbo "apri".
        if any(w == v or (_sim(w, v) >= 0.78 and w[0] == v[0] and abs(len(w) - len(v)) <= 1) for v in lista):
            if nome == "vai":
                if len(parole) > 1 and parole[1] in SU_PAROLE:
                    return "vai", 2
                return "vai", 1
            return nome, 1
    return None, 0


def scelta_in_attesa():
    """True se Eva ha chiesto "quale intendi?" e aspetta ancora la risposta (1, 2, 3, annulla...)."""
    return bool(_pendente["opzioni"]) and time.time() <= _pendente["scade"]


def oggetto_noto(nome):
    """True se `nome` corrisponde a qualcosa che Eva CONOSCE: un programma o sito, un alias tuo, una cartella
    speciale, qualcosa nell'indice o nell'ultima cartella aperta. Non apre niente e NON cerca sul disco."""
    parole = _togli_articoli(_n(nome).split())
    if parole and parole[0] in MARCATORI:
        parole = parole[1:]
    parole = _togli_articoli(parole)
    n = " ".join(parole)
    if not n:
        return False
    try:
        import comandi
        if any(c[0] >= comandi.SOGLIA_SICURA and c[1] != 2 for c in comandi._candidati(n)):
            return True
    except Exception:
        pass
    if _trova_cartella_nota(n):
        return True
    ind = _gestore.indice
    if ind is not None and ind.cerca(n, None, "", None, IMP["SOGLIA_CARTELLE"]):
        return True
    if _nav["ultima"] and time.time() - _nav["tempo"] < IMP["NAV_VALIDITA_SECONDI"]:
        if _figli_ultima(n, None, "", IMP["SOGLIA_CARTELLE"], profondo=False):
            return True
    return False


def _in_lista(frase, insieme, soglia=0.88):
    return any(_sim(frase, x) >= soglia for x in insieme)


NUMERI = {"uno": 0, "1": 0, "prima": 0, "primo": 0, "due": 1, "2": 1, "seconda": 1, "secondo": 1,
          "tre": 2, "3": 2, "terza": 2, "terzo": 2}
ANNULLA = {"annulla", "niente", "nessuna", "nessuno", "lascia stare", "no", "lascia perdere", "nessuna delle tre"}


def _scelta_numero(f):
    parole = f.split()
    if not parole or len(parole) > 5:
        return None
    for p in parole:
        if p in NUMERI:
            return NUMERI[p]
        if len(p) >= 4:
            for k, v in NUMERI.items():
                if len(k) >= 4 and _sim(p, k) >= 0.8:
                    return v
    return None


def _testo(chiave, predefinito, **campi):
    return cervello.testo_fisso(chiave, predefinito, **campi)


# ======================================================================
# Cuore: risolvere un nome in cartelle/file
# ======================================================================
def _figli_ultima(richiesta, tipo, estensione, soglia, profondo):
    """Cartelle e file dentro l'ultima cartella aperta (elenco vero dal disco, non dall'indice)."""
    ult = _nav["ultima"]
    if not ult or not os.path.isdir(ult):
        return []
    qs = _n(richiesta)
    qt = qs.split()
    ris = []
    try:
        if profondo:
            cartelle, file = _scansiona([ult], 4, 4000, 8000)
            elementi = [(p, "d") for p in cartelle] + [(p, "f") for p in file]
        else:
            with os.scandir(ult) as it:
                elementi = [(e.path, "d" if e.is_dir() else "f") for e in it if not e.name.startswith(".")]
    except OSError:
        return []
    for percorso, tp in elementi[:12000]:
        if tipo and tp != tipo:
            continue
        if estensione and _estensione(percorso) != estensione:
            continue
        nome = os.path.basename(percorso)
        if tp == "f" and "." in nome:
            nome = nome.rpartition(".")[0]
        ns = _n(nome)
        if not ns:
            continue
        p = _punteggio(qs, qt, ns, tuple(ns.split()))
        if p >= soglia:
            ris.append((p, percorso, tp))
    ris.sort(key=lambda r: (-round(r[0], 3), len(r[1])))
    return ris


def _chiedi_o_apri(ris, etichetta, esegui, cosa_richiesta, tipo_richiesto, fonte):
    """ris: [(punteggio, percorso, tipo)] ordinato. Apre il migliore o chiede quale."""
    top = ris[0][0]
    pari = [r for r in ris if r[0] >= top - MARGINE_PARI]
    unici, visti = [], set()
    for r in pari:
        k = os.path.normcase(r[1])
        if k not in visti:
            visti.add(k)
            unici.append(r)
    if len(unici) >= 2:
        # se il primo è praticamente perfetto e gli altri no, non serve chiedere
        if not (top >= 0.97 and unici[1][0] < 0.97):
            return _chiedi(unici[:3], esegui, cosa_richiesta)
    p, percorso, tp = ris[0]
    return _apri_scelto(p, percorso, tp, esegui, fonte)


def _chiedi(opzioni, esegui, cosa_richiesta):
    nomi = []
    padri = [_bello(os.path.dirname(o[1])) for o in opzioni]
    for i, (p, percorso, tp) in enumerate(opzioni):
        dove = padri[i]
        if padri.count(dove) > 1:                       # stesso nome di cartella: aggiungo quella sopra
            nonno = _bello(os.path.dirname(os.path.dirname(percorso)))
            dove = f"{dove}, dentro {nonno}"
        nome = _stem(percorso, tp)
        if tp == "f" and _estensione(percorso):
            nome += f", {_estensione(percorso)}"        # "ESEMPIO CV, pdf" / "ESEMPIO CV, docx"
        nomi.append(f"{['uno', 'due', 'tre'][i]}, {nome}, in {dove}")
    ordinali = ["la prima", "la seconda", "la terza"][:len(opzioni)]
    domanda = ("Quale intendi, " + ", ".join(ordinali[:-1]) + " o " + ordinali[-1] + "?"
               if len(ordinali) > 1 else "Intendi questa?")
    detto = f"Ho trovato più risultati simili: {'; '.join(nomi)}. {domanda}"
    if esegui:
        _pendente["opzioni"] = list(opzioni)
        _pendente["scade"] = time.time() + SCELTA_VALIDITA
    dettaglio = " | ".join(f"{i + 1}) {o[1]} ({o[0]:.2f})" for i, o in enumerate(opzioni))
    return _r("scelta", f"apri {cosa_richiesta}", "Più risultati simili: " + dettaglio, detto)


def _apri_scelto(punteggio, percorso, tp, esegui, fonte=""):
    cosa = _stem(percorso, tp)
    genere = "file" if tp == "f" else "cartella"
    corretta = f"apri il {genere} {cosa}" if tp == "f" else f"apri la cartella {cosa}"
    if _e_vietato(percorso, tp):
        return _r("non_trovato", corretta, f"Bloccato per sicurezza: {percorso} (programma o script)",
                  _testo("programmi_vietati", "Per sicurezza non apro programmi dai file, {nome}."), cosa)
    info = f"{percorso} (somiglianza {punteggio:.2f}{', ' + fonte if fonte else ''})"
    if not esegui:
        return _r("eseguito", corretta, f"(prova) aprirebbe {genere} {info}", "", cosa)
    try:
        _apri(percorso, tp)
    except PermissionError:
        return _r("non_trovato", corretta, f"Bloccato per sicurezza: {percorso}",
                  _testo("programmi_vietati", "Per sicurezza non apro programmi dai file, {nome}."), cosa)
    except OSError as errore:
        return _r("non_trovato", corretta, f"Non riesco ad aprire {percorso}: {errore}",
                  _testo("non_trovato_generico", "Non riesco ad aprirlo, {nome}."), cosa)
    if tp == "d":
        _ricorda_cartella(percorso)
    elif tp == "f":
        _nav["ultima"], _nav["tempo"] = os.path.dirname(percorso), time.time()
    # somiglianza buona ma non perfetta: apro la migliore e lo dico ("Se non è questa, dimmi.")
    parziale = punteggio < 0.95 and not fonte.startswith(("alias", "cartella speciale", "scelta", "cartella superiore"))
    chiave = ("file_ok" if tp == "f" else "cartella_ok") + ("_parziale" if parziale else "")
    detto = _testo(chiave, "Apro {cosa}. Se non è questa, dimmi." if parziale else "Apro {cosa}.", cosa=cosa)
    return _r("eseguito", corretta, f"Aperto {genere} {info}", detto, cosa)


def _non_trovato(nome, tipo, esegui):
    """Non l'ho trovata: dico cosa ho capito e salvo la FRASE in non_trovati.txt."""
    chiave = {"f": "file_non_trovato", "d": "cartella_non_trovata"}.get(tipo, "oggetto_non_trovato")
    predef = {"f": "Ho capito: apri file {cosa}. Non l'ho trovato.",
              "d": "Ho capito: apri cartella {cosa}. Non l'ho trovata."}.get(tipo, "Ho capito: apri {cosa}. Non l'ho trovato.")
    frase = _ctx.get("frase") or nome
    nota = ""
    if esegui and frase and registra_non_trovato(frase):
        nota = " (frase salvata in non_trovati.txt)"
    genere = {"f": "file ", "d": "cartella "}.get(tipo, "")
    return _r("non_trovato", f"apri {genere}{nome}", f"Non ho trovato: {nome}{nota}",
              _testo(chiave, predef, cosa=nome), nome)


def _risolvi(nome, tipo, estensione, luogo, dentro, esplicito, esegui, usa_disco=True):
    """Trova cosa aprire. tipo: "d", "f" o None. Restituisce RisultatoFile oppure None
    (None = "non è roba mia": lascio fare ai comandi per app e siti)."""
    soglia = IMP["SOGLIA_CARTELLE"]
    nome_n = _n(nome)
    radici = _gestore.radici

    # 1) alias tuoi
    if not luogo and not dentro and nome_n:
        for chiave, percorso in _alias.items():
            s = _sim(nome_n, chiave)
            if s >= 0.85 and os.path.isdir(percorso):
                return _apri_scelto(s, percorso, "d", esegui, "alias tuo")
    # 2) cartelle speciali (desktop, documenti, cestino, questo pc, disco C...)
    if not dentro and tipo in (None, "d"):
        if not nome_n and luogo:                      # "apri sul desktop"
            nome_n = luogo
        sp = _cerca_speciale(nome_n, esplicito)
        if sp and (not luogo or nome_n == luogo or not nome):
            return _apri_scelto(sp[0], sp[1], sp[2], esegui, "cartella speciale")
    if not nome_n:
        return None
    # 3) l'ultima cartella aperta ha la precedenza
    recente = _nav["ultima"] and (time.time() - _nav["tempo"] < IMP["NAV_VALIDITA_SECONDI"] or dentro)
    if recente:
        ris = _figli_ultima(nome, tipo, estensione, soglia, profondo=dentro)
        if ris:
            return _chiedi_o_apri(ris, "", esegui, nome, tipo, "nell'ultima cartella aperta")
    if dentro:
        if not _nav["ultima"]:
            return _r("non_trovato", f"apri {nome}", "Nessuna cartella aperta di recente",
                      _testo("nessuna_cartella_aperta", "Non ho ancora aperto nessuna cartella, {nome}."))
        return _non_trovato(nome, tipo, esegui)
    # 4) per i nomi semplici, prima le app e i siti (come sempre)
    if tipo is None and not luogo and not esplicito:
        import comandi
        forti = [c for c in comandi._candidati(nome_n) if c[0] >= comandi.SOGLIA_SICURA and c[1] != 2]
        if forti:
            return None
    # 5) indice: prima nel luogo indicato, poi dappertutto
    ind = _gestore.indice
    if ind is None:
        if esplicito:
            return _r("non_trovato", f"apri {nome}", "Indice non ancora pronto",
                      _testo("indice_in_preparazione",
                             "Sto ancora preparando l'indice delle cartelle, {nome}. Riprova tra poco."))
        return None
    if luogo and luogo in radici:
        ris = ind.cerca(nome, tipo, estensione, [radici[luogo]], soglia)
        if ris:
            return _chiedi_o_apri(ris, "", esegui, nome, tipo, f"in {luogo}")
    ris = ind.cerca(nome, tipo, estensione, None, soglia)
    if ris:
        return _chiedi_o_apri(ris, "", esegui, nome, tipo, "indice")
    # 6) ricerca di riserva sul disco (al massimo qualche secondo)
    import comandi
    ha_app = tipo is None and any(c[0] >= comandi.SOGLIA_INCERTA for c in comandi._candidati(nome_n))
    # La ricerca sul disco SOLO se hai detto "cartella" o "file" e il nome è breve: un "apri ..." qualunque,
    # o una frase lunga, non fa mai partire una ricerca sul disco (era così che le chiacchiere diventavano ordini).
    if usa_disco and esegui and esplicito and len(nome_n.split()) <= 4:
        _dire(f"Non è nell'indice: cerco '{nome}' sul disco (al massimo {IMP['TEMPO_RICERCA_DISCO']} s)...")
        ris = _cerca_disco(nome, tipo, estensione, IMP["TEMPO_RICERCA_DISCO"])
        if ris:
            return _chiedi_o_apri(ris, "", esegui, nome, tipo, "ricerca sul disco")
    # non trovato. Un nome di una sola parola potrebbe essere un programma di Windows: lascio la parola ai
    # comandi di prima; con due o più parole (e nessuna app simile) dico cosa ho capito.
    if esplicito or (tipo is None and not ha_app and len(nome_n.split()) >= 2):
        return _non_trovato(nome, tipo, esegui)
    return None


def cerca_percorsi(nome, tipo=None, luogo=None, usa_disco=True):
    """Come _risolvi, ma SENZA aprire niente: restituisce i candidati [(punteggio, percorso, tipo)],
    dal migliore, presi dalla prima fonte che ne ha (alias, cartelle speciali, ultima cartella aperta,
    indice, ricerca sul disco). Serve a Eva per copiare e spostare (vox_operazioni.py)."""
    soglia = IMP["SOGLIA_CARTELLE"]
    nome_n = _n(nome)
    if not nome_n:
        return []
    for chiave, percorso in _alias.items():                         # alias tuoi
        s = _sim(nome_n, chiave)
        if s >= 0.85 and os.path.isdir(percorso):
            return [(s, percorso, "d")]
    if tipo in (None, "d") and not luogo:                           # cartelle speciali
        sp = _cerca_speciale(nome_n, True)
        if sp and sp[2] == "d":
            return [(sp[0], sp[1], "d")]
    if _nav["ultima"] and time.time() - _nav["tempo"] < IMP["NAV_VALIDITA_SECONDI"]:
        ris = _figli_ultima(nome, tipo, "", soglia, profondo=False)  # dentro l'ultima cartella aperta
        if ris:
            return ris
    ind = _gestore.indice
    radici = _gestore.radici
    if ind is not None:
        if luogo and luogo in radici:
            ris = ind.cerca(nome, tipo, "", [radici[luogo]], soglia)
            if ris:
                return ris
        ris = ind.cerca(nome, tipo, "", None, soglia)
        if ris:
            return ris
    if usa_disco:                                                    # ricerca di riserva (max qualche secondo)
        _dire(f"Non è nell'indice: cerco '{nome}' sul disco (al massimo {IMP['TEMPO_RICERCA_DISCO']} s)...")
        return _cerca_disco(nome, tipo, "", IMP["TEMPO_RICERCA_DISCO"])
    return []


# ======================================================================
# Punto d'ingresso: una frase
# ======================================================================
def _togli_articoli(parole):
    while parole and parole[0] in ARTICOLI | {"di", "del", "della", "dello", "dei", "chiamata", "chiamato", "nome"}:
        parole = parole[1:]
    return parole


def prova(testo, esegui=True):
    """Gestisce le frasi su cartelle e file. Restituisce un RisultatoFile oppure None
    (None = non è una frase per me: continuano gli altri comandi)."""
    f = applica_correzioni(normalizza(testo))
    parole = f.split()
    if not parole:
        return None

    # a) risposta a "quale intendi?"
    if _pendente["opzioni"]:
        scaduta = time.time() > _pendente["scade"]
        opzioni = _pendente["opzioni"]
        if not scaduta:
            if _in_lista(f, ANNULLA, 0.9):
                _pendente["opzioni"] = []
                return _r("eseguito", "annulla", "Scelta annullata", _testo("annullato", "Va bene, {nome}."))
            n = _scelta_numero(f)
            if n is not None and n < len(opzioni):
                p, percorso, tp = opzioni[n]
                _pendente["opzioni"] = []
                return _apri_scelto(p, percorso, tp, esegui, f"scelta numero {n + 1}")
        _pendente["opzioni"] = []                     # altra frase o scaduta: la scelta decade

    # b) aggiorna l'indice
    if _sim(parole[0], "aggiorna") >= 0.8 or _sim(parole[0], "ricostruisci") >= 0.8:
        if any(_sim(p, "indice") >= 0.8 for p in parole[1:]) or ("elenco" in parole and "cartelle" in parole):
            if esegui:
                _gestore.richiedi_aggiornamento()
            return _r("eseguito", "aggiorna l'indice", "Aggiornamento dell'indice avviato in background",
                      _testo("indice_aggiorno", "Aggiorno l'indice, {nome}."), "indice")

    # c) impara / chiama (alias di cartelle)
    r = _alias_vocale(parole, esegui)
    if r is not None:
        return r

    # d) navigazione: su / indietro
    if _in_lista(f, SU):
        return _naviga("su", esegui)
    if _in_lista(f, INDIETRO):
        return _naviga("indietro", esegui)

    # e) apri / cerca / vai su ... (con i riempitivi tolti: "mi apre la cartella di Katia" -> "apre la cartella...")
    _ctx["frase"] = f
    pulite = _riempitivi(parole)
    vietato = [p for p in pulite if p in VERBI_VIETATI or any(_sim(p, v) >= 0.85 for v in VERBI_VIETATI if len(v) >= 5)]
    if vietato and any(_sim(p, "cartella") >= 0.85 or p in ("file", "cartelle", "folder", "documento") for p in pulite):
        v0 = vietato[0]
        if v0 in {"cancella", "cancellare", "cancello", "elimina", "eliminare", "elimino", "rimuovi", "rimuovere",
                  "rimuovo", "svuota", "formatta"}:
            chiave, predef = "op_cancella", "Per sicurezza non cancello, {nome}. Fallo tu a mano."
        elif v0 in {"copia", "copiare", "copio", "sposta", "spostare", "sposto", "taglia", "incolla"}:
            chiave, predef = "op_verbo_iniziale", ("Per copiare o spostare comincia con il verbo, {nome}: per esempio, "
                                                    "copia Katia documenti sulla chiavetta.")
        else:
            chiave, predef = "azione_vietata", "Questo non lo faccio, {nome}."
        return _r("non_trovato", f.strip(), f"Rifiutato ({v0}): non è un comando di apertura",
                  _testo(chiave, predef))
    verbo, n = _verbo(pulite)
    if verbo is None:
        return _senza_verbo(pulite, esegui)
    # "vai" senza "su / in / al...": non è un'apertura (lo gestiscono altri)
    if verbo == "vai" and n == 1:
        return None
    return _richiesta(pulite[n:], verbo, esegui)


def _riempitivi(parole):
    """Toglie le parole di riempimento: "mi apre la cartella di Katia per favore" -> "apre la cartella di Katia"."""
    s = " " + " ".join(parole) + " "
    for fr in RIEMPITIVI_FRASE:
        s = s.replace(f" {fr} ", " ")
    p = s.split()
    while len(p) > 1 and p[0] in RIEMPITIVI_INIZIO:
        p = p[1:]
    while len(p) > 1 and p[-1] in RIEMPITIVI_FINE:
        p = p[:-1]
    return p


def _senza_verbo(parole, esegui):
    """Frasi senza un verbo chiaro: "la cartella Katia", o un nome che somiglia molto a una cartella."""
    resto = _togli_articoli(parole)
    if not resto:
        return None
    for k, p in enumerate(resto[:6]):
        if _sim(p, "cartella") >= 0.85 or p in ("cartelle", "folder", "directory"):
            nome = _togli_articoli(resto[k + 1:])
            if nome:
                return _richiesta(nome, "apri", esegui, tipo="d", esplicito=True)
            return None                                  # "questa cartella" senza nome: non so quale
    ind = _gestore.indice
    if ind is not None and 2 <= len(resto) <= 6:         # almeno due parole: una sola potrebbe essere altro
        richiesta = " ".join(resto)
        ris = ind.cerca(richiesta, "d", "", None, 0.92)
        if ris:
            return _chiedi_o_apri(ris, "", esegui, richiesta, "d", "nome simile a una cartella")
    return None


def _richiesta(resto, verbo, esegui, tipo=None, esplicito=False):
    """Dalla parte dopo il verbo (marcatore, luogo, "dentro", estensione) a una ricerca vera."""
    resto = _togli_articoli(resto)
    if not resto:
        return None
    luogo = None
    m0 = RE_LUOGO_INIZIO.match(" ".join(resto) + " ")     # "nei documenti apri la cartella X"
    if m0 and len(resto) > len(m0.group(0).split()):
        luogo = LUOGHI[m0.group(1)]
        esplicito = True
        resto = _togli_articoli(" ".join(resto)[m0.end():].split())
    if (resto[0] in MARCATORI and MARCATORI[resto[0]] is not None and not (resto[0] == "documento" and len(resto) == 1)):
        tipo = MARCATORI[resto[0]]
        esplicito = True
        resto = _togli_articoli(resto[1:])
    if verbo == "cerca" and not esplicito:
        return None                                   # "cerca ricette" = ricerca su Google, come prima
    if not resto:
        return None
    # parole finali: "dentro" / luogo
    dentro = False
    for d in DENTRO:
        dp = d.split()
        if resto[-len(dp):] == dp and len(resto) > len(dp):
            dentro, resto = True, resto[:-len(dp)]
            esplicito = True
            break
    m = RE_LUOGO.search(" ".join(resto))
    if m:
        luogo = LUOGHI[m.group(1)]
        resto = " ".join(resto)[:m.start()].split()
        resto = _togli_articoli(resto)
    nome = " ".join(resto)
    estensione = ""
    m = re.search(r"\.([a-z0-9]{2,5})$", nome)
    if m and (tipo == "f" or m.group(1) in {"pdf", "doc", "docx", "xls", "xlsx", "txt", "jpg", "png", "mp3",
                                           "mp4", "csv", "ppt", "pptx", "zip", "jpeg"}):
        estensione = m.group(1)
        nome = nome[:m.start()]
        tipo = tipo or "f"
    if luogo:
        esplicito = True
    return _risolvi(nome, tipo, estensione, luogo, dentro, esplicito, esegui)


def _naviga(dove, esegui):
    ult = _nav["ultima"]
    if not ult:
        return _r("non_trovato", f"torna {dove}", "Nessuna cartella aperta di recente",
                  _testo("nessuna_cartella_aperta", "Non ho ancora aperto nessuna cartella, {nome}."))
    if dove == "su":
        padre = os.path.dirname(ult.rstrip("\\/"))
        if not padre or os.path.normcase(padre) == os.path.normcase(ult):
            return _r("non_trovato", "vai su", "Sono già alla cartella più alta",
                      _testo("gia_in_alto", "Siamo già alla cartella più alta, {nome}."))
        return _apri_scelto(1.0, padre, "d", esegui, "cartella superiore")
    if not _nav["storia"]:
        return _r("non_trovato", "torna indietro", "Nessuna cartella precedente",
                  _testo("nessuna_precedente", "Non c'è una cartella precedente, {nome}."))
    precedente = _nav["storia"][-1]
    if not esegui:
        return _r("eseguito", "torna indietro", f"(prova) aprirebbe {precedente}")
    try:
        _apri(precedente, "d")
    except OSError:
        _nav["storia"].pop()
        return _r("non_trovato", "torna indietro", f"La cartella precedente non esiste più: {precedente}",
                  _testo("non_trovato_generico", "Non riesco ad aprirla, {nome}."))
    _nav["storia"].pop()
    _nav["ultima"], _nav["tempo"] = precedente, time.time()
    cosa = _bello(precedente)
    return _r("eseguito", "torna indietro", f"Aperta la cartella precedente {precedente}",
              _testo("cartella_ok", "Apro {cosa}.", cosa=cosa), cosa)


def _trova_cartella_nota(testo_n):
    """Una cartella 'nota' (alias, speciale o indicizzata) per nome. (punteggio, percorso) o None."""
    for chiave, percorso in _alias.items():
        s = _sim(testo_n, chiave)
        if s >= 0.85 and os.path.isdir(percorso):
            return s, percorso
    sp = _cerca_speciale(testo_n, True)
    if sp and sp[2] == "d":
        return sp[0], sp[1]
    ind = _gestore.indice
    if ind:
        ris = ind.cerca(testo_n, "d", "", None, 0.85)
        if ris:
            return ris[0][0], ris[0][1]
    return None


def _alias_vocale(parole, esegui):
    """"impara questa cartella <nome> è <cartella>"  /  "chiama <cartella> <nuovo nome>"."""
    if not parole:
        return None
    if _sim(parole[0], "impara") >= 0.8:
        resto = parole[1:]
        while resto and resto[0] in {"questa", "la", "quella", "una"}:
            resto = resto[1:]
        if not resto or _sim(resto[0], "cartella") < 0.8:
            return None
        resto = resto[1:]
        # il nome e la cartella sono separati da "e" (la "è" senza accento)
        for i in range(1, len(resto) - 1):
            if resto[i] not in {"e", "uguale", "sarebbe", "significa"}:
                continue
            alias, bersaglio = " ".join(resto[:i]), " ".join(resto[i + 1:])
            trovata = _trova_cartella_nota(_n(bersaglio))
            if trovata:
                return _salva_e_conferma(alias, trovata[1], esegui)
        return _r("non_trovato", "impara cartella", "Non ho capito quale cartella",
                  _testo("alias_no", "Non ho capito quale cartella intendi, {nome}."))
    if _sim(parole[0], "chiama") >= 0.8 and len(parole) >= 3:
        resto = parole[1:]
        while resto and resto[0] in {"la", "cartella", "questa", "quella"}:
            resto = resto[1:]
        migliore = None
        for k in range(1, len(resto)):
            trovata = _trova_cartella_nota(_n(" ".join(resto[:k])))
            if trovata and (migliore is None or trovata[0] > migliore[0][0]):
                migliore = (trovata, " ".join(resto[k:]))
        if migliore and migliore[1]:
            return _salva_e_conferma(migliore[1], migliore[0][1], esegui)
        return _r("non_trovato", "chiama cartella", "Non ho capito quale cartella",
                  _testo("alias_no", "Non ho capito quale cartella intendi, {nome}."))
    return None


def _salva_e_conferma(alias, percorso, esegui):
    cosa = _bello(percorso)
    if not esegui:
        return _r("eseguito", f"impara cartella {alias}", f"(prova) salverebbe alias '{alias}' = {percorso}",
                  "", cosa)
    if not _salva_alias(alias, percorso):
        return _r("non_trovato", "impara cartella", "Non riesco a salvare alias_cartelle.json",
                  _testo("non_trovato_generico", "Non riesco a salvarlo, {nome}."))
    return _r("eseguito", f"impara cartella {alias}", f"Alias salvato in alias_cartelle.json: '{alias}' = {percorso}",
              _testo("alias_ok", "Fatto, {nome}. Ora «{alias}» è la cartella {cosa}.", alias=alias, cosa=cosa), cosa)


# ======================================================================
# Aprire più cose insieme: "apri Chrome e blocco note"
# ======================================================================
def prova_multipla(testo, esegui, interpreta):
    """"apri A e B", "apri A poi B": apre tutto in sequenza. interpreta = azioni.interpreta."""
    f = applica_correzioni(normalizza(testo))
    parole = f.split()
    verbo, n = _verbo(parole)
    if verbo != "apri" or len(parole) < 4:
        return None
    resto = _togli_articoli(parole[n:])
    marcatore = ""
    if resto and resto[0] in MARCATORI and MARCATORI[resto[0]] is not None:
        marcatore = resto[0] + " "
        resto = resto[1:]
    corpo = " ".join(resto)
    parti =[p.strip() for p in re.split(r"\s+(?:e poi|e|poi|quindi)\s+", corpo) if p.strip()]
    if len(parti) < 2:
        return None
    # se l'intera frase è UN nome vero (es. "katia e marco"), non la divido
    ind = _gestore.indice
    if ind is not None:
        intera = ind.cerca(corpo, None, "", None, 0.92)
        if intera:
            return None
    if len(parti) > 5 or not all(oggetto_noto(marcatore + p) for p in parti):
        return None             # una parte non è nota: non divido la frase in più cose da aprire
    risultati = []
    for parte in parti[:5]:
        r = interpreta(f"apri {marcatore}{parte}", esegui)
        risultati.append((parte, r))
        if getattr(r, "tipo", "") == "scelta":
            return r
    aperti, mancanti, log = [], [], []
    for parte, r in risultati:
        cosa = getattr(r, "cosa", "") or " ".join(normalizza(r.frase_corretta or parte).split()[1:]) or parte
        if r.tipo == "eseguito":
            aperti.append(cervello._bello(cosa))
        else:
            mancanti.append(parte)
        log.append(f"{parte}: {r.tipo}")
    messaggio = "Apertura in sequenza: " + " | ".join(r.messaggio.splitlines()[0] for _, r in risultati)
    corretta = "apri " + " e ".join(p for p, _ in risultati)
    if not aperti:
        return _r("non_trovato", corretta, messaggio, _testo("non_trovato_multi", "Non ho trovato niente, {nome}."))
    elenco = ", ".join(aperti[:-1]) + (" e " if len(aperti) > 1 else "") + aperti[-1]
    if mancanti:
        detto = _testo("multi_parziale", "Ho aperto {elenco}, ma non ho trovato {mancanti}, {nome}.",
                       elenco=elenco, mancanti=", ".join(mancanti))
    else:
        detto = _testo("multi_ok", "Apro {elenco}.", elenco=elenco)
    return _r("eseguito", corretta, messaggio, detto, elenco)
