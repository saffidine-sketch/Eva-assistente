"""Eva copia, taglia e sposta file e cartelle, a voce, SEMPRE con conferma.

Comandi (la frase deve INIZIARE con il verbo):
  copia <nome> sulla chiavetta / sul desktop / in documenti / in <cartella>
  sposta <nome> sulla chiavetta / in <cartella>        taglia <nome> e incolla in <cartella>
  copia <nome>   (a due tempi: poi "incolla qui" o "incolla in <cartella>")      taglia <nome>   poi   incolla
  annulla / stop      (mentre copia)        modalità sola lettura / modalità normale

REGOLE DI SICUREZZA (tutte obbligatorie):
  - ogni operazione chiede conferma a voce ("sì", "conferma", "va bene", "ok", "procedi"); con "no", "annulla",
    "lascia stare" o dopo OPERAZIONI_CONFERMA_SECONDI secondi senza risposta non si fa niente ("Annullato.");
  - MAI sovrascrivere: se il nome esiste già nella destinazione, la copia si chiama "<nome> - copia", "- copia 2"...
    (i file si creano in modalità "esclusiva": se esistono già, il programma si ferma invece di sovrascrivere);
  - MAI cancellare: nessun comando "elimina/cancella/svuota". L'UNICA rimozione possibile è l'originale di uno
    spostamento TRA DISCHI DIVERSI, dopo aver copiato tutto e verificato che numero di file e dimensione coincidano;
    nello stesso disco lo spostamento è un semplice "rename" del sistema (non copia e non cancella);
  - cartelle protette (Windows, Program Files, ProgramData, AppData, radice del disco C, cartelle di sistema) e i file di
    Eva (dettatura.py, i moduli, i .json, i registri, i backup) non si copiano, non si spostano, non sono destinazioni;
  - oltre OPERAZIONI_LIMITE_GB gigabyte o OPERAZIONI_LIMITE_FILE file serve una seconda conferma;
  - se il nome non è chiaro (più risultati simili o punteggio basso) chiedo quale; se resta incerto non faccio niente;
  - una sola operazione alla volta, ciascuna con la sua conferma;
  - ogni operazione è scritta in operazioni.log (origine, destinazione, file, dimensione, esito, ora).
"""
import collections
import ctypes
import errno
import difflib
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import eva_percorsi as percorsi
import vox_cervello as cervello
import vox_file as vf
from comandi import ARTICOLI, normalizza

AQUI = percorsi.CARTELLA_DATI
FILE_LOG_OP = percorsi.dati("operazioni.log")

IMP = {
    "OPERAZIONI_FILE": True,             # False = copia e sposta spenti
    "OPERAZIONI_LIMITE_GB": 5,           # oltre: seconda conferma
    "OPERAZIONI_LIMITE_FILE": 5000,      # oltre: seconda conferma
    "OPERAZIONI_CONFERMA_SECONDI": 15,   # tempo per rispondere "sì"
}
_log = print
_emetti = lambda evento: None             # manda un evento alla finestra (fase, nota, progresso, chiudi)
_parla = lambda testo: None               # dice una frase a voce (e la mostra)
_conversazione = lambda: False            # la conversazione continua è attiva?
_sola_lettura = False                     # "modalità sola lettura" a voce

CHIAVETTA = ["chiavetta", "chiave usb", "chiave", "pen drive", "pendrive", "penna usb", "penna", "usb",
             "disco esterno", "hard disk esterno", "disco usb", "memoria usb", "unita usb", "unita esterna"]
PREPOSIZIONI = {"sulla", "sul", "sullo", "sui", "su", "in", "nella", "nel", "nello", "nei", "negli", "dentro",
                "alla", "al", "allo", "ai"}
VERBI_OP = {"copia": "copia", "sposta": "sposta", "taglia": "taglia", "incolla": "incolla"}
VERBI_CANCELLA = {"cancella", "cancellare", "elimina", "eliminare", "svuota", "svuotare", "rimuovi", "rimuovere",
                  "formatta", "formattare", "distruggi", "butta", "straccia", "cancello", "elimino"}
CONFERMA_PAROLE = {"si", "conferma", "confermo", "procedi", "ok", "okay", "certo", "va", "bene", "pure", "eva"}
CONFERMA_CENTRO = {"si", "conferma", "confermo", "procedi", "ok", "okay"}
ANNULLA_PAROLE = {"no", "annulla", "annullo", "stop", "ferma", "fermati", "niente", "nulla", "basta"}
ANNULLA_FRASI = ["lascia stare", "lascia perdere", "non farlo", "non lo fare", "no grazie", "non adesso"]

# stato di un'operazione in attesa di risposta
_st = {"fase": None, "op": None, "opzioni": [], "scade": 0.0}
_appunti = {"percorso": None, "modo": None}
_corsa = {"attiva": False, "annulla": threading.Event(), "pct": 0}
_scrivi_lock = threading.Lock()
_cache_unita = {"t": 0.0, "dati": []}


def configura(imp, log=None, emetti=None, parla=None, conversazione=None):
    global _log, _emetti, _parla, _conversazione
    for k in IMP:
        if k in imp:
            IMP[k] = imp[k]
    if log is not None:
        _log = log
    if emetti is not None:
        _emetti = emetti
    if parla is not None:
        _parla = parla
    if conversazione is not None:
        _conversazione = conversazione


def _dire(testo):
    try:
        _log(testo)
    except Exception:
        pass


def _t(chiave, predefinito, **campi):
    return cervello.testo_fisso(chiave, predefinito, **campi)


def _r(tipo, corretta, messaggio, detto="", cosa=""):
    return vf._r(tipo, corretta, messaggio, detto, cosa)


# ======================================================================
# Stato (letto dal programma principale)
# ======================================================================
def in_corso():
    return _corsa["attiva"]


def in_attesa():
    return _st["fase"] is not None


def scaduta():
    return _st["fase"] is not None and time.time() > _st["scade"]


def secondi_attesa():
    return IMP["OPERAZIONI_CONFERMA_SECONDI"] + 7


def annulla_per_timeout():
    """Nessuna risposta in tempo: non si fa niente."""
    op = _st["op"]
    _azzera()
    _scrivi_op(f"ANNULLATA per mancata risposta | {_descrivi(op)}")
    _dire("[operazioni] Nessuna risposta: annullato, non è stato fatto niente.")
    return _t("op_annullato", "Annullato.")


def _azzera():
    _st.update({"fase": None, "op": None, "opzioni": [], "scade": 0.0})


# ======================================================================
# Registro operazioni.log
# ======================================================================
def _scrivi_op(riga):
    with _scrivi_lock:
        try:
            with open(FILE_LOG_OP, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " | " + riga + "\n")
        except OSError:
            pass


def _descrivi(op):
    if not op:
        return "(nessuna)"
    return f"{op.get('modo')} | origine: {op.get('sorgente') or op.get('nome')} | destinazione: {op.get('dest') or op.get('dest_nome')}"


# ======================================================================
# Utilità
# ======================================================================
def _kb(b):
    """Dimensione detta a voce: "circa 120 megabyte"."""
    if b < 1024:
        return "meno di un kilobyte"
    if b < 1024 ** 2:
        return f"circa {round(b / 1024)} kilobyte"
    if b < 1024 ** 3:
        return f"circa {round(b / 1024 ** 2)} megabyte"
    return "circa " + f"{b / 1024 ** 3:.1f}".replace(".", ",") + " gigabyte"


def _bello(p):
    return vf._bello(p)


def _nome_libero(cartella, nome, e_file):
    """Un nome che NON esiste ancora in cartella: "x", "x - copia", "x - copia 2"..."""
    if not os.path.lexists(os.path.join(cartella, nome)):
        return nome
    base, ext = os.path.splitext(nome) if e_file else (nome, "")
    n = 1
    while True:
        suffisso = " - copia" if n == 1 else f" - copia {n}"
        candidato = f"{base}{suffisso}{ext}"
        if not os.path.lexists(os.path.join(cartella, candidato)):
            return candidato
        n += 1


def _e_link(entry):
    try:
        return entry.is_symlink() or entry.is_junction()
    except OSError:
        return True


def _scandisci(percorso, limite_file, limite_byte, secondi=10.0):
    """Conta file e byte (senza seguire collegamenti). Si ferma presto se supera i limiti o il tempo."""
    info = {"file": 0, "byte": 0, "cartelle": 0, "saltati": 0, "troncato": False}
    if os.path.isfile(percorso):
        try:
            info["file"], info["byte"] = 1, os.path.getsize(percorso)
        except OSError:
            pass
        return info
    scadenza = time.time() + secondi
    pila = [percorso]
    while pila:
        base = pila.pop()
        try:
            with os.scandir(base) as it:
                voci = list(it)
        except OSError:
            info["saltati"] += 1
            continue
        for e in voci:
            if _e_link(e):
                info["saltati"] += 1
            elif e.is_dir(follow_symlinks=False):
                info["cartelle"] += 1
                pila.append(e.path)
            else:
                info["file"] += 1
                try:
                    info["byte"] += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
        if (info["file"] > limite_file * 4 or info["byte"] > limite_byte * 4 or time.time() > scadenza):
            info["troncato"] = True
            break
    return info


# ======================================================================
# Unità rimovibili (chiavetta, disco esterno)
# ======================================================================
def _etichetta_volume(radice):
    try:
        buf = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(radice, buf, 261, None, None, None, None, 0):
            return buf.value
    except Exception:
        pass
    return ""


def _rileva_unita():
    """[{radice, lettera, nome}]: unità rimovibili (chiavette) e dischi esterni USB collegati."""
    trovate = {}
    try:
        k = ctypes.windll.kernel32
        maschera = k.GetLogicalDrives()
        for i in range(26):
            if maschera >> i & 1:
                radice = f"{chr(65 + i)}:\\"
                if k.GetDriveTypeW(radice) == 2 and os.path.isdir(radice):       # 2 = rimovibile
                    trovate[radice] = _etichetta_volume(radice)
    except Exception:
        pass
    try:   # dischi esterni "fissi" collegati via USB
        comando = ("Get-Disk | Where-Object BusType -eq 'USB' | Get-Partition | "
                   "Where-Object DriveLetter | ForEach-Object { [string]$_.DriveLetter }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                           capture_output=True, text=True, timeout=8, creationflags=0x08000000)
        for lettera in r.stdout.split():
            radice = lettera.strip()[:1].upper() + ":\\"
            if len(radice) == 3 and os.path.isdir(radice):
                trovate.setdefault(radice, _etichetta_volume(radice))
    except Exception:
        pass
    return [{"radice": r, "lettera": r[0], "nome": n} for r, n in sorted(trovate.items())]


def unita_esterne():
    if time.time() - _cache_unita["t"] > 15:
        _cache_unita["dati"] = _rileva_unita()
        _cache_unita["t"] = time.time()
    return list(_cache_unita["dati"])


# ======================================================================
# Cartelle protette
# ======================================================================
CARTELLE_RADICE_SISTEMA = {"windows", "program files", "program files (x86)", "programdata", "programmi",
                           "system volume information", "$recycle.bin", "recovery", "perflogs", "config.msi",
                           "documents and settings", "boot", "$windows.~bt", "$windows.~ws"}
ESTENSIONI_DI_EVA = {".py", ".pyw", ".json", ".log", ".bat", ".ps1", ".pyc"}


def _norm(p):
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/") or os.path.normcase(os.path.abspath(p))


def _dentro_o_uguale(p, base):
    p, base = _norm(p), _norm(base)
    return p == base or p.startswith(base + os.sep)


def _file_di_eva(percorso, ruolo="origine"):
    """File e cartelle di Eva stessa: dettatura.py, moduli, .json, registri, backup."""
    p = _norm(percorso)
    qui = _norm(AQUI)
    if ruolo == "sposta" and _dentro_o_uguale(qui, p):  # spostare la cartella di Eva (o una che la contiene): mai
        return "è la cartella di Eva (o la contiene)"
    if os.path.dirname(p) == qui:
        nome = os.path.basename(p)
        ext = os.path.splitext(nome)[1]
        if (ext in ESTENSIONI_DI_EVA or nome.startswith("backup") or nome in {"__pycache__", "non_trovati.txt",
                                                                               "operazioni.log"}
                or nome.startswith(("dettatura", "vox_", "eva_", "indice_cartelle", "alias_"))):
            return "è un file di Eva"
    for sotto in ("backup",):
        # dentro una cartella di backup di Eva
        rel = os.path.relpath(p, qui) if _dentro_o_uguale(p, qui) else ""
        if rel and rel.split(os.sep)[0].startswith(sotto):
            return "è dentro un backup di Eva"
    return None


def motivo_protetto(percorso, ruolo):
    """Se il percorso è protetto, dice perché (altrimenti None). ruolo: "origine", "destinazione", "sposta"."""
    p = _norm(percorso)
    disco_sistema = _norm(os.environ.get("SystemDrive", "C:") + "\\")
    protette = [os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"),
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.environ.get("ProgramData", r"C:\ProgramData")]
    for base in protette:
        if base and _dentro_o_uguale(p, base):
            return "cartella di sistema"
    if "appdata" in [c.lower() for c in Path(p).parts]:
        return "cartella AppData"
    if p == disco_sistema or p == disco_sistema.rstrip("\\") or p.rstrip("\\") == disco_sistema.rstrip("\\"):
        return "radice del disco C"
    genitore, nome = os.path.split(p)
    if os.path.normcase(genitore).rstrip("\\") == disco_sistema.rstrip("\\") and nome.lower() in CARTELLE_RADICE_SISTEMA:
        return "cartella di sistema"
    if nome.lower() in {"system volume information", "$recycle.bin"}:
        return "cartella di sistema"
    if "$recycle.bin" in [c.lower() for c in Path(p).parts]:
        return "cestino"
    if ruolo in ("origine", "sposta"):
        eva = _file_di_eva(percorso, ruolo)
        if eva:
            return eva
    if ruolo == "sposta":
        # le cartelle "radice" dell'utente non si spostano mai
        radici = list(vf.cartelle_utente().values()) + [str(Path.home())]
        if any(_norm(r) == p for r in radici):
            return "cartella principale dell'utente"
        if len(Path(p).parts) <= 1:
            return "radice di un disco"
    if ruolo == "destinazione" and _dentro_o_uguale(p, AQUI):
        rel = os.path.relpath(p, _norm(AQUI))
        if rel != "." and rel.split(os.sep)[0].startswith(("backup", "__pycache__")):
            return "è dentro un backup di Eva"
    return None


# ======================================================================
# Capire la frase
# ======================================================================
def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _verbo(tok):
    if not tok:
        return None
    migliore = max(VERBI_OP, key=lambda v: _sim(tok[0], v))
    return VERBI_OP[migliore] if _sim(tok[0], migliore) >= 0.86 else None


def _e_conferma(tok):
    if not tok or len(tok) > 3:
        return False
    if not all(t in CONFERMA_PAROLE or any(_sim(t, c) >= 0.85 for c in ("conferma", "confermo", "procedi"))
               for t in tok):
        return False
    return any(t in CONFERMA_CENTRO or any(_sim(t, c) >= 0.85 for c in ("conferma", "confermo", "procedi"))
               for t in tok) or tok == ["va", "bene"]


def _e_annulla(f, tok):
    if any(t in ANNULLA_PAROLE for t in tok) and len(tok) <= 3:
        return True
    return any(fr in f for fr in ANNULLA_FRASI)


def _senza_articoli(parole):
    parole = list(parole)
    while parole and parole[0] in ARTICOLI | {"di", "del", "della", "dello", "dei", "questa", "questo", "quella",
                                              "quello"}:
        parole = parole[1:]
    return parole


def _tipo_e_nome(parole):
    """"la cartella katia" -> ("d", ["katia"]); "il file fattura" -> ("f", ["fattura"])."""
    parole = _senza_articoli(parole)
    tipo = None
    if parole and parole[0] in ("cartella", "cartelle", "folder", "directory"):
        tipo, parole = "d", _senza_articoli(parole[1:])
    elif parole and parole[0] in ("file", "documento"):
        tipo, parole = "f", _senza_articoli(parole[1:])
    return tipo, parole


def _e_destinazione_chiavetta(tail):
    testo = " ".join(_senza_articoli(tail))
    return bool(testo) and max(_sim(testo, c) for c in CHIAVETTA) >= 0.85


def _destinazione_valida(tail):
    """Il pezzo di frase dopo "sulla / in / nella" è davvero una destinazione che conosco?"""
    tail = _senza_articoli(tail)
    if not tail:
        return False
    if _e_destinazione_chiavetta(tail):
        return True
    if tail in (["qui"], ["qua"]):
        return True
    testo = " ".join(tail)
    sp = vf._cerca_speciale(vf._n(testo), True)
    if sp and sp[2] == "d":
        return True
    for chiave in vf._alias:
        if _sim(vf._n(testo), chiave) >= 0.85:
            return True
    ind = vf._gestore.indice
    return bool(ind and ind.cerca(testo, "d", "", None, 0.85))


def _dividi(parole):
    """"prova eva sul desktop" -> (["prova","eva"], ["desktop"]). Prova l'ultima preposizione per prima."""
    if len(parole) >= 2 and parole[-1] in ("qui", "qua"):
        return parole[:-1], [parole[-1]]
    indici = [i for i, t in enumerate(parole) if t in PREPOSIZIONI and i > 0]
    for i in reversed(indici):
        coda = parole[i + 1:]
        if coda and _destinazione_valida(coda):
            return parole[:i], coda
    if indici:
        i = indici[-1]
        if parole[i + 1:]:
            return parole[:i], parole[i + 1:]
    return parole, None


# ======================================================================
# Punto d'ingresso
# ======================================================================
def prova(testo, esegui=True, consentito=True):
    """Gestisce copia, sposta, taglia, incolla (e le risposte di conferma). Restituisce un risultato oppure None
    (None = non è per me). consentito=False: non avvio operazioni nuove (es. dettatura con F9 senza il nome)."""
    f = normalizza(testo)
    tok = f.split()
    if not tok:
        return None

    # --- risposte a una domanda in sospeso, o copia in corso -----------------------------------------
    r = _gestisci_stato(f, tok, esegui)
    if r is not None:
        return r
    if not consentito:
        return None

    # --- cancellare: MAI ----------------------------------------------------------------------------------
    if (any(_sim(tok[0], v) >= 0.86 for v in VERBI_CANCELLA)
            or ("svuota" in tok and "cestino" in tok) or (tok[0] in ("togli", "butta") and "cestino" in tok)):
        _dire(f"[operazioni] Rifiutato: '{testo}' (non cancello mai niente)")
        return _r("non_trovato", f.strip(), "Rifiutato: non cancello mai niente",
                  _t("op_cancella", "Per sicurezza non cancello, {nome}. Fallo tu a mano."))

    # --- modalità sola lettura / normale ----------------------------------------------------------------
    global _sola_lettura
    frase_mod = " ".join(tok)
    if _sim(frase_mod, "modalita sola lettura") >= 0.85 or frase_mod in ("sola lettura", "modalita lettura"):
        _sola_lettura = True
        return _r("eseguito", "modalità sola lettura", "Operazioni sui file spente (sola lettura)",
                  _t("op_sola_lettura", "Modalità sola lettura attiva, {nome}. Non copio né sposto niente."))
    if _sim(frase_mod, "modalita normale") >= 0.85:
        if not IMP["OPERAZIONI_FILE"]:
            return _r("non_trovato", "modalità normale", "OPERAZIONI_FILE è False",
                      _t("op_spente", "Le operazioni sui file sono spente."))
        _sola_lettura = False
        return _r("eseguito", "modalità normale", "Operazioni sui file riaccese",
                  _t("op_normale", "Modalità normale, {nome}. Posso di nuovo copiare e spostare, sempre con conferma."))

    # --- il verbo deve essere la PRIMA parola ----------------------------------------------------------
    verbo = _verbo(tok)
    if verbo is None or len(tok) < 2 and verbo != "incolla":
        return None
    if not IMP["OPERAZIONI_FILE"] or _sola_lettura:
        return _r("non_trovato", f.strip(), "Operazioni sui file spente",
                  _t("op_spente", "Le operazioni sui file sono spente."))
    if _corsa["attiva"]:
        return _r("non_trovato", f.strip(), "Un'altra operazione è in corso",
                  _t("op_occupata", "Sto ancora lavorando su un'altra operazione, {nome}. Aspetta che finisca."))
    if _st["fase"]:
        _azzera()                                   # una domanda vecchia ancora aperta: decade
    resto = tok[1:]
    _dire(f"[operazioni] Verbo riconosciuto: {verbo} | resto: {resto}")

    if verbo == "incolla":
        return _incolla(resto, esegui)

    # "taglia X e incolla in Y" / "copia X e incolla in Y"
    testo_resto = " ".join(resto)
    m = re.search(r"\s(?:e|poi)\s+incolla(?:\s+(?:qui|qua|in|nel|nella|nello|sul|sulla)\s+(.*))?$", " " + testo_resto)
    if m:
        nome_tokens = (" " + testo_resto)[:m.start()].split()
        dest_tokens = _senza_articoli((m.group(1) or "qui").split())
        modo = "sposta" if verbo in ("sposta", "taglia") else "copia"
        return _nuova_operazione(modo, nome_tokens, dest_tokens, esegui)

    nome_tokens, dest_tokens = _dividi(resto)
    if dest_tokens is None:                          # senza destinazione: appunti ("copia X", "taglia X")
        return _memorizza(verbo, nome_tokens, esegui)
    modo = "copia" if verbo == "copia" else "sposta"
    return _nuova_operazione(modo, nome_tokens, dest_tokens, esegui)


def _gestisci_stato(f, tok, esegui):
    # copia in corso: solo "annulla" / "stop" la ferma
    if _corsa["attiva"]:
        if _e_annulla(f, tok) and len(tok) <= 3:
            _corsa["annulla"].set()
            _dire("[operazioni] Richiesto l'annullamento della copia in corso.")
            return _r("eseguito", "annulla copia", "Annullamento richiesto",
                      _t("op_fermo", "Fermo la copia, {nome}."))
        if _verbo(tok) is not None:
            return _r("non_trovato", f.strip(), "Un'altra operazione è in corso",
                      _t("op_occupata", "Sto ancora lavorando su un'altra operazione, {nome}. Aspetta che finisca."))
        return None
    fase = _st["fase"]
    if not fase:
        return None
    if time.time() > _st["scade"]:                   # troppo tardi: non si fa niente
        return _r("eseguito", "annulla", "Nessuna risposta in tempo", annulla_per_timeout())
    op, opzioni = _st["op"], list(_st["opzioni"])
    if _e_annulla(f, tok) and not _e_conferma(tok):
        _azzera()
        _scrivi_op(f"ANNULLATA a voce | {_descrivi(op)}")
        _dire("[operazioni] Annullato a voce: non è stato fatto niente.")
        return _r("eseguito", "annulla", "Annullato a voce", _t("op_annullato", "Annullato."))
    if fase in ("conferma", "seconda_conferma"):
        if _e_conferma(tok):
            return _dopo_conferma(op, fase, esegui)
    elif fase.startswith("scelta"):
        indice = _scelta_indice(f, tok, opzioni, fase)
        if indice is None and len(opzioni) == 1 and _e_conferma(tok):
            indice = 0
        if indice is not None and indice < len(opzioni):
            _azzera()
            return _dopo_scelta(op, fase, opzioni[indice], esegui)
    # qualunque altra frase: non è una conferma -> non si fa niente
    _azzera()
    _scrivi_op(f"ANNULLATA (risposta non valida: '{f}') | {_descrivi(op)}")
    _dire(f"[operazioni] Risposta non valida ('{f}'): annullato, non è stato fatto niente.")
    return _r("eseguito", "annulla", "Risposta non valida: annullato", _t("op_annullato", "Annullato."))


# ======================================================================
# Scelte (più risultati, più chiavette)
# ======================================================================
NUMERI = {"uno": 0, "1": 0, "prima": 0, "primo": 0, "due": 1, "2": 1, "seconda": 1, "secondo": 1,
          "tre": 2, "3": 2, "terza": 2, "terzo": 2}


def _scelta_indice(f, tok, opzioni, fase):
    if len(tok) > 5:
        return None
    for t in tok:
        if t in NUMERI:
            return NUMERI[t]
        if len(t) >= 4:
            for k, v in NUMERI.items():
                if len(k) >= 4 and _sim(t, k) >= 0.8:
                    return v
    if fase == "scelta_unita":                        # anche la lettera ("la E", "disco F") o il nome del volume
        for i, o in enumerate(opzioni):
            if o["lettera"].lower() in tok or (o["nome"] and _sim(" ".join(tok), vf._n(o["nome"])) >= 0.8):
                return i
    return None


def _domanda_unita(op, unita):
    ordinali = ["la prima", "la seconda", "la terza"]
    voci = [f"{['uno', 'due', 'tre'][i]}, {u['nome'] or 'senza nome'}, lettera {u['lettera']}"
            for i, u in enumerate(unita[:3])]
    quale = "Quale intendi, " + ", ".join(ordinali[:len(voci) - 1]) + " o " + ordinali[len(voci) - 1] + "?"
    _st.update({"fase": "scelta_unita", "op": op, "opzioni": unita[:3],
                "scade": time.time() + IMP["OPERAZIONI_CONFERMA_SECONDI"] + 5})
    return _r("scelta", f"{op['modo']} {op['nome']}", "Più unità collegate: " + " | ".join(u["radice"] for u in unita),
              f"Vedo {len(voci)} unità: {'; '.join(voci)}. {quale}")


def _domanda_percorsi(op, ruolo, opzioni, nome):
    """ruolo: "sorgente" o "dest". Una sola opzione = "Intendi ...? Dimmi sì o no."."""
    fase = "scelta_sorgente" if ruolo == "sorgente" else "scelta_dest"
    padri = [_bello(os.path.dirname(o[1])) for o in opzioni]
    voci = []
    for i, (p, percorso, tp) in enumerate(opzioni):
        dove = padri[i]
        if padri.count(dove) > 1:
            dove = f"{dove}, dentro {_bello(os.path.dirname(os.path.dirname(percorso)))}"
        voci.append(f"{['uno', 'due', 'tre'][i]}, {vf._stem(percorso, tp)}, in {dove}")
    _st.update({"fase": fase, "op": op, "opzioni": [{"percorso": o[1], "tipo": o[2], "score": o[0]} for o in opzioni],
                "scade": time.time() + IMP["OPERAZIONI_CONFERMA_SECONDI"] + 5})
    if len(opzioni) == 1:
        detto = f"Intendi {vf._stem(opzioni[0][1], opzioni[0][2])}, in {padri[0]}? Dimmi sì o no."
    else:
        ordinali = ["la prima", "la seconda", "la terza"][:len(opzioni)]
        detto = (f"Ho trovato più risultati simili: {'; '.join(voci)}. "
                 f"Quale intendi, {', '.join(ordinali[:-1])} o {ordinali[-1]}?")
    return _r("scelta", f"{op['modo']} {nome}", "Più risultati simili: " + " | ".join(
        f"{o[1]} ({o[0]:.2f})" for o in opzioni), detto)


def _dopo_scelta(op, fase, scelta, esegui):
    if op.get("modo") == "memorizza":                 # "copia X" / "taglia X" con più risultati: ora ho scelto
        return _salva_appunti(op["verbo"], scelta["percorso"], scelta["tipo"], esegui)
    if fase == "scelta_unita":
        op["dest"], op["dest_desc"] = scelta["radice"], f"sulla chiavetta {scelta['nome']}".strip()
        op["dest_tipo"] = "unita"
    elif fase == "scelta_sorgente":
        op["sorgente"], op["tipo_s"] = scelta["percorso"], scelta["tipo"]
    else:
        op["dest"], op["dest_desc"] = scelta["percorso"], _frase_dest(scelta["percorso"])
    return _avanza(op, esegui)


def _frase_dest(percorso):
    nome = _bello(percorso)
    if nome == "Desktop":
        return "sul desktop"
    return f"in {nome}"


# ======================================================================
# Costruire l'operazione
# ======================================================================
def _nuova_operazione(modo, nome_tokens, dest_tokens, esegui, sorgente=None, appunti=False):
    tipo, nome_tokens = _tipo_e_nome(nome_tokens)
    nome = " ".join(nome_tokens)
    if not nome and sorgente is None:
        return _r("non_trovato", modo, "Manca il nome", _t("op_non_capito", "Non ho capito cosa {verbo}, {nome}.",
                                                              verbo=modo))
    op = {"modo": modo, "nome": nome, "tipo_s": tipo, "sorgente": sorgente, "dest_tokens": dest_tokens,
          "dest": None, "dest_desc": "", "dest_tipo": None, "info": None, "seconda": False,
          "appunti": appunti}
    return _avanza(op, esegui)


def _memorizza(verbo, nome_tokens, esegui):
    """"copia X" / "taglia X" senza destinazione: lo ricordo (appunti interni). Non tocca niente."""
    tipo, nome_tokens = _tipo_e_nome(nome_tokens)
    nome = " ".join(nome_tokens)
    if not nome:
        return None
    ris = _trova_sorgente_candidati(nome, tipo)
    if isinstance(ris, vf.RisultatoFile):
        return ris
    cand = ris
    op = {"modo": "memorizza", "verbo": verbo, "nome": nome, "tipo_s": tipo, "sorgente": None}
    if cand[0][0] >= 0.90 and (len(cand) == 1 or cand[1][0] < cand[0][0] - 0.04):
        return _salva_appunti(verbo, cand[0][1], cand[0][2], esegui)
    return _domanda_percorsi(op, "sorgente", cand[:3], nome)


def _salva_appunti(verbo, percorso, tipo, esegui):
    motivo = motivo_protetto(percorso, "sposta" if verbo == "taglia" else "origine")
    if motivo:
        return _r("non_trovato", f"{verbo} {percorso}", f"Protetto: {motivo}",
                  _t("op_protetta", "Questa cartella è protetta, {nome}."))
    cosa = vf._stem(percorso, tipo)
    if esegui:
        _appunti.update({"percorso": percorso, "modo": "taglia" if verbo == "taglia" else "copia"})
    _scrivi_op(f"APPUNTI | {verbo} | {percorso}")
    if verbo == "taglia":
        return _r("eseguito", f"taglia {cosa}", f"Negli appunti (taglia): {percorso}",
                  _t("op_tagliato", "Tagliato {cosa}. Non si sposta finché non dici incolla, e te lo chiedo di nuovo.",
                     cosa=cosa), cosa)
    return _r("eseguito", f"copia {cosa}", f"Negli appunti (copia): {percorso}",
              _t("op_copiato", "Copiato {cosa}.", cosa=cosa), cosa)


def _incolla(resto, esegui):
    if not _appunti["percorso"]:
        return _r("non_trovato", "incolla", "Appunti vuoti",
                  _t("op_appunti_vuoti", "Non ho niente da incollare, {nome}."))
    if not os.path.exists(_appunti["percorso"]):
        _appunti.update({"percorso": None, "modo": None})
        return _r("non_trovato", "incolla", "L'elemento negli appunti non esiste più",
                  _t("op_appunti_spariti", "L'elemento che avevo copiato non c'è più, {nome}."))
    resto = _senza_articoli(resto)
    while resto and resto[0] in PREPOSIZIONI:
        resto = _senza_articoli(resto[1:])
    dest_tokens = resto or ["qui"]
    modo = "sposta" if _appunti["modo"] == "taglia" else "copia"
    return _nuova_operazione(modo, [], dest_tokens, esegui, sorgente=_appunti["percorso"], appunti=True)


def _trova_sorgente_candidati(nome, tipo):
    cand = vf.cerca_percorsi(nome, tipo)
    unici, visti = [], set()
    for c in cand:
        k = os.path.normcase(c[1])
        if k not in visti:
            visti.add(k)
            unici.append(c)
    if not unici:
        return _r("non_trovato", f"copia {nome}", f"Non ho trovato: {nome}",
                  _t("op_non_trovato", "Non ho trovato {cosa}, {nome}.", cosa=nome))
    if unici[0][0] < 0.80:
        return _r("non_trovato", f"copia {nome}", f"Somiglianza troppo bassa ({unici[0][0]:.2f}): {nome}",
                  _t("op_incerto", "Non sono sicura di quale intendi, {nome}. Non faccio niente."))
    return unici


# ======================================================================
# Passo per passo: origine -> destinazione -> controlli -> conferma
# ======================================================================
def _avanza(op, esegui):
    # 0) se la destinazione è "la chiavetta" e non ce n'è nessuna, lo dico subito
    if op["dest"] is None and op.get("dest_tokens") and _e_destinazione_chiavetta(_senza_articoli(op["dest_tokens"])):
        if not unita_esterne():
            return _r("non_trovato", op["modo"], "Nessuna unità rimovibile collegata",
                      _t("op_nessuna_chiavetta", "Non vedo nessuna chiavetta collegata, {nome}."))
    # 1) origine
    if op["sorgente"] is None:
        cand = _trova_sorgente_candidati(op["nome"], op["tipo_s"])
        if isinstance(cand, vf.RisultatoFile):
            return cand
        chiaro = cand[0][0] >= 0.90 and (len(cand) == 1 or cand[1][0] < cand[0][0] - 0.04)
        if not chiaro:
            return _domanda_percorsi(op, "sorgente", cand[:3], op["nome"])
        op["sorgente"], op["tipo_s"] = cand[0][1], cand[0][2]
    # 2) destinazione
    if op["dest"] is None:
        r = _trova_destinazione(op)
        if r is not None:
            return r
    return _controlla_e_chiedi(op, esegui)


def _trova_destinazione(op):
    tail = _senza_articoli(op["dest_tokens"] or [])
    if tail in (["qui"], ["qua"]):
        ultima = vf._nav.get("ultima")
        if not ultima or not os.path.isdir(ultima):
            return _r("non_trovato", op["modo"], "Nessuna cartella aperta",
                      _t("op_nessuna_cartella", "Non ho una cartella aperta, {nome}. Dimmi in quale cartella."))
        op["dest"], op["dest_desc"], op["dest_tipo"] = ultima, _frase_dest(ultima), "cartella"
        return None
    if _e_destinazione_chiavetta(tail):
        unita = unita_esterne()
        if not unita:
            return _r("non_trovato", op["modo"], "Nessuna unità rimovibile collegata",
                      _t("op_nessuna_chiavetta", "Non vedo nessuna chiavetta collegata, {nome}."))
        if len(unita) > 1:
            return _domanda_unita(op, unita)
        op["dest"], op["dest_tipo"] = unita[0]["radice"], "unita"
        op["dest_desc"] = f"sulla chiavetta {unita[0]['nome']}".strip()
        return None
    nome = " ".join(tail)
    cand = vf.cerca_percorsi(nome, "d")
    unici, visti = [], set()
    for c in cand:
        if os.path.normcase(c[1]) not in visti and os.path.isdir(c[1]):
            visti.add(os.path.normcase(c[1]))
            unici.append(c)
    if not unici:
        return _r("non_trovato", op["modo"], f"Destinazione non trovata: {nome}",
                  _t("op_dest_non_trovata", "Non ho trovato la cartella {cosa}, {nome}.", cosa=nome))
    if unici[0][0] < 0.80:
        return _r("non_trovato", op["modo"], f"Destinazione incerta ({unici[0][0]:.2f}): {nome}",
                  _t("op_incerto", "Non sono sicura di quale intendi, {nome}. Non faccio niente."))
    chiaro = unici[0][0] >= 0.90 and (len(unici) == 1 or unici[1][0] < unici[0][0] - 0.04)
    if not chiaro:
        return _domanda_percorsi(op, "dest", unici[:3], nome)
    op["dest"], op["dest_desc"], op["dest_tipo"] = unici[0][1], _frase_dest(unici[0][1]), "cartella"
    return None


def _controlla_e_chiedi(op, esegui):
    src, dest = op["sorgente"], op["dest"]
    modo = op["modo"]
    cosa = vf._stem(src, op["tipo_s"] or ("d" if os.path.isdir(src) else "f"))
    op["cosa"] = cosa
    if not os.path.exists(src):
        return _r("non_trovato", modo, f"Non esiste più: {src}", _t("op_non_trovato", "Non ho trovato {cosa}, {nome}.",
                                                                       cosa=cosa))
    if not os.path.isdir(dest):
        return _r("non_trovato", modo, f"Destinazione non valida: {dest}",
                  _t("op_nessuna_chiavetta" if op.get("dest_tipo") == "unita" else "op_dest_non_trovata",
                     "Non vedo nessuna chiavetta collegata, {nome}." if op.get("dest_tipo") == "unita"
                     else "Non ho trovato la cartella {cosa}, {nome}.", cosa=op["dest_desc"]))
    for percorso, ruolo in ((src, "sposta" if modo == "sposta" else "origine"), (dest, "destinazione")):
        motivo = motivo_protetto(percorso, ruolo)
        if motivo:
            _dire(f"[operazioni] Rifiutato: {percorso} ({motivo})")
            _scrivi_op(f"RIFIUTATA (protetto: {motivo}) | {_descrivi(op)}")
            return _r("non_trovato", modo, f"Protetto ({motivo}): {percorso}",
                      _t("op_protetta", "Questa cartella è protetta, {nome}."))
    if os.path.isdir(src) and _dentro_o_uguale(dest, src):
        return _r("non_trovato", modo, "La destinazione è dentro l'origine",
                  _t("op_dentro_se_stessa", "Non posso metterla dentro se stessa, {nome}."))
    if modo == "sposta" and _norm(os.path.dirname(src)) == _norm(dest):
        return _r("non_trovato", modo, "È già in quella cartella",
                  _t("op_gia_li", "È già in quella cartella, {nome}."))
    limite_f = IMP["OPERAZIONI_LIMITE_FILE"]
    limite_b = IMP["OPERAZIONI_LIMITE_GB"] * 1024 ** 3
    info = _scandisci(src, limite_f, limite_b)
    op["info"] = info
    try:
        libero = shutil.disk_usage(dest).free
    except OSError:
        libero = None
    if libero is not None and not (modo == "sposta" and _stesso_volume(src, dest)) and info["byte"] * 1.02 + 10 * 1024 ** 2 > libero:
        return _r("non_trovato", modo, f"Spazio insufficiente: servono {info['byte']} byte, liberi {libero}",
                  _t("op_spazio", "Non c'è abbastanza spazio sulla destinazione, {nome}: servono {serve} e ne restano {resta}.",
                     serve=_kb(info["byte"]), resta=_kb(libero)))
    op["stesso_volume"] = _stesso_volume(src, dest)
    op["grande"] = info["troncato"] or info["file"] > limite_f or info["byte"] > limite_b
    nome_finale = _nome_libero(dest, os.path.basename(src), os.path.isfile(src))
    op["nome_finale"] = nome_finale
    avviso = ""
    if nome_finale != os.path.basename(src):
        avviso = f" Esiste già un elemento con questo nome: la copia si chiamerà {nome_finale}."
    dimensione = (f"più di {IMP['OPERAZIONI_LIMITE_GB']} gigabyte" if info["troncato"] and info["byte"] > limite_b
                  else _kb(info["byte"]))
    contenuto = f", {info['file']} file" if os.path.isdir(src) else ""
    if info["troncato"] and info["file"] > limite_f:
        contenuto = f", più di {limite_f} file"
    if modo == "copia":
        detto = f"Copio {cosa} {op['dest_desc']}, {dimensione}{contenuto}.{avviso} Dimmi sì per confermare."
    else:
        come = ("Prima copio tutto, verifico che i conti tornino e solo dopo tolgo l'originale. "
                if not op["stesso_volume"] else "")
        detto = (f"Attenzione, questo sposta {cosa} {op['dest_desc']}: non resta più dov'è, e dopo lo spostamento non "
                 f"posso annullarlo. {come}Sono {dimensione}{contenuto}.{avviso} Dimmi sì per confermare.")
    if esegui:
        _st.update({"fase": "conferma", "op": op, "opzioni": [], "scade": time.time() + IMP["OPERAZIONI_CONFERMA_SECONDI"]})
    _dire(f"[operazioni] Da confermare: {modo} {src} -> {dest} ({info['file']} file, {info['byte']} byte, "
          f"{'stesso volume' if op['stesso_volume'] else 'volumi diversi'})")
    return _r("scelta", f"{modo} {cosa} {op['dest_desc']}", f"In attesa di conferma: {modo} {src} -> {dest} "
              f"({info['file']} file, {info['byte']} byte)", detto, cosa)


def _stesso_volume(a, b):
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _dopo_conferma(op, fase, esegui):
    if op.get("grande") and fase == "conferma":
        info = op["info"]
        _st.update({"fase": "seconda_conferma", "scade": time.time() + IMP["OPERAZIONI_CONFERMA_SECONDI"]})
        limite_f, limite_gb = IMP["OPERAZIONI_LIMITE_FILE"], IMP["OPERAZIONI_LIMITE_GB"]
        motivo = []
        if info["byte"] > limite_gb * 1024 ** 3 or info["troncato"]:
            motivo.append(f"supera i {limite_gb} gigabyte")
        if info["file"] > limite_f or info["troncato"]:
            motivo.append(f"contiene più di {limite_f} file")
        detto = (f"È molto grande: {' e '.join(motivo) or 'è enorme'}. Ci può volere parecchio. "
                 f"Dimmi sì ancora una volta per confermare, oppure annulla.")
        return _r("scelta", f"{op['modo']} {op['cosa']}", "Seconda conferma richiesta (operazione grande)", detto)
    _azzera()
    return _avvia(op, esegui)


# ======================================================================
# Esecuzione in un thread separato
# ======================================================================
def _avvia(op, esegui):
    if not esegui:
        return _r("eseguito", op["modo"], "(prova) avvierebbe l'operazione", "", op.get("cosa", ""))
    _corsa["annulla"].clear()
    _corsa.update({"attiva": True, "pct": 0})
    info = op["info"]
    _scrivi_op(f"AVVIO | {op['modo']} | origine: {op['sorgente']} | destinazione: {op['dest']} | "
               f"file: {info['file']} | byte: {info['byte']}")
    _dire(f"[operazioni] AVVIO {op['modo']}: {op['sorgente']} -> {op['dest']} ({info['file']} file, {info['byte']} byte)")
    _emetti(("fase", "ESEGUO"))
    threading.Thread(target=_lavora, args=(op,), name="eva-operazione", daemon=True).start()
    verbo = "copio" if op["modo"] == "copia" else "sposto"
    return _r("eseguito", f"{op['modo']} {op['cosa']}", f"Operazione avviata: {op['modo']} {op['sorgente']} -> {op['dest']}",
              _t("op_avvio", "Va bene, {verbo}.", verbo=verbo), op["cosa"])


class _Annullata(Exception):
    pass


class _Fatale(Exception):
    pass


def _progresso(stato, fatti, totale, ultimo):
    if totale <= 0:
        return
    pct = min(100, int(fatti * 100 / totale))
    if pct != _corsa["pct"] and (time.time() - stato["t"] > 0.4 or pct == 100):
        _corsa["pct"] = pct
        stato["t"] = time.time()
        _emetti(("progresso", pct))
        if pct // 10 != stato["decina"]:
            stato["decina"] = pct // 10
            _dire(f"[operazioni] ... {pct}%")


def _lavora(op):
    t0 = time.time()
    esito = {"stato": "errore", "motivo": "errore sconosciuto", "copiati": 0, "totale": 0, "byte": 0}
    era_cartella = os.path.isdir(op["sorgente"])
    try:
        if op["modo"] == "sposta" and op["stesso_volume"]:
            esito = _sposta_stesso_volume(op)
        else:
            esito = _copia(op)
            if op["modo"] == "sposta" and esito["stato"] == "ok":
                esito = _rimuovi_originale(op, esito)
    except Exception as errore:
        esito = {"stato": "errore", "motivo": f"errore imprevisto: {errore}", "copiati": 0, "totale": 0, "byte": 0}
    finally:
        _corsa["attiva"] = False
    esito["e_cartella"] = era_cartella
    if esito["stato"] == "ok" and op["modo"] == "sposta" and op.get("appunti"):
        _appunti.update({"percorso": None, "modo": None})          # il "taglia" è stato usato
    testo = _testo_finale(op, esito)
    durata = time.time() - t0
    _scrivi_op(f"ESITO {esito['stato'].upper()} | {op['modo']} | origine: {op['sorgente']} | destinazione: {op['dest']} | "
               f"file: {esito.get('copiati', 0)}/{esito.get('totale', 0)} | byte: {esito.get('byte', 0)} | "
               f"{esito.get('motivo', '')} | {durata:.1f} s")
    _dire(f"[operazioni] ESITO {esito['stato']}: {esito.get('motivo', '')} ({durata:.1f} s)")
    _dire(f"Risposta detta: {testo}")
    _emetti(("progresso", None))
    _parla(testo)
    if _conversazione():
        _emetti(("fase", "IN ASCOLTO"))
    else:
        _emetti(("chiudi", 4.0))


def _testo_finale(op, esito):
    cosa, dest = op.get("cosa", ""), op["dest_desc"]
    utente = cervello.nome_utente()
    n, tot = esito.get("copiati", 0), esito.get("totale", 0)
    s = esito["stato"]
    rinomina = ""
    if esito.get("nome_finale") and esito["nome_finale"] != os.path.basename(op["sorgente"]):
        rinomina = f" Esisteva già: l'ho chiamato {esito['nome_finale']}."
    if s == "ok":
        cartella = esito.get("e_cartella", False)
        if op["modo"] == "copia":
            if cartella:
                return f"Fatto, {utente}. Copiati {n} file {dest}.{rinomina}"
            return f"Fatto, {utente}. Copiato {cosa} {dest}.{rinomina}"
        if cartella:
            return f"Fatto, {utente}. Spostati {n} file {dest}.{rinomina}"
        return f"Fatto, {utente}. Spostato {cosa} {dest}.{rinomina}"
    extra = (f" L'ultimo file è rimasto incompleto: si chiama {esito['incompleto']}." if esito.get("incompleto") else "")
    if s == "annullata":
        return (f"Fermato. Ho copiato {n} file su {tot}. Quello che è già nella destinazione resta lì: "
                f"non ho cancellato niente.{extra}")
    if s == "scollegata":
        return (f"La destinazione si è scollegata durante l'operazione, {utente}: non è completata "
                f"(copiati {n} file su {tot}). Non ho rimosso niente.{extra}")
    if s == "parziale":
        return (f"Non è andata fino in fondo: {esito['motivo']}. Ho copiato {n} file su {tot}. "
                f"Non ho rimosso niente di quello che c'era.{extra}")
    return f"Non sono riuscita: {esito['motivo']}. Non ho rimosso niente."


def _sposta_stesso_volume(op):
    """Nello stesso disco lo spostamento è un rename del sistema: niente copia, niente cancellazione."""
    src, dest_dir = op["sorgente"], op["dest"]
    nome = _nome_libero(dest_dir, os.path.basename(src), os.path.isfile(src))
    dst = os.path.join(dest_dir, nome)
    try:
        if os.path.lexists(dst):
            return {"stato": "errore", "motivo": "il nome di destinazione esiste già", "copiati": 0, "totale": 1, "byte": 0}
        os.rename(src, dst)                        # su Windows NON sovrascrive: se esiste dà errore
    except PermissionError:
        return {"stato": "errore", "motivo": "accesso negato (il file o la cartella è in uso?)", "copiati": 0,
                "totale": 1, "byte": 0}
    except OSError as e:
        return {"stato": "errore", "motivo": _spiega(e), "copiati": 0, "totale": 1, "byte": 0}
    info = op["info"]
    _dire(f"[operazioni] Spostato (rename): {src} -> {dst}")
    return {"stato": "ok", "motivo": f"spostato come {nome}", "copiati": info["file"], "totale": info["file"],
            "byte": info["byte"], "nome_finale": nome}


def _spiega(e):
    if getattr(e, "errno", None) == errno.ENOSPC:
        return "il disco di destinazione è pieno"
    if isinstance(e, PermissionError) or getattr(e, "errno", None) in (errno.EACCES, errno.EPERM):
        return "accesso negato"
    return f"errore del disco ({e})"


def _piano(src, top_dest):
    """Elenco delle cartelle da creare e dei file da copiare: [(cartelle)], [(origine, destinazione, byte)]."""
    cartelle, file, saltati = [], [], 0
    if os.path.isfile(src):
        return [], [(src, top_dest, os.path.getsize(src))], 0
    cartelle.append(top_dest)
    pila = [(src, top_dest)]
    while pila:
        base_s, base_d = pila.pop()
        try:
            with os.scandir(base_s) as it:
                voci = list(it)
        except OSError:
            saltati += 1
            continue
        for e in voci:
            if _e_link(e):
                saltati += 1
                continue
            d = os.path.join(base_d, e.name)
            if e.is_dir(follow_symlinks=False):
                cartelle.append(d)
                pila.append((e.path, d))
            else:
                try:
                    file.append((e.path, d, e.stat(follow_symlinks=False).st_size))
                except OSError:
                    saltati += 1
    return cartelle, file, saltati


def _copia(op):
    src, dest_dir = op["sorgente"], op["dest"]
    radice_dest = os.path.splitdrive(os.path.abspath(dest_dir))[0] + "\\"
    e_file = os.path.isfile(src)
    nome = _nome_libero(dest_dir, os.path.basename(src), e_file)
    top = os.path.join(dest_dir, nome)
    cartelle, file, saltati = _piano(src, top)
    totale_byte = sum(b for _s, _d, b in file)
    esito = {"stato": "ok", "motivo": "", "copiati": 0, "totale": len(file), "byte": 0, "nome_finale": nome,
             "saltati": saltati, "errori": [], "copiati_lista": []}
    stato_pr = {"t": 0.0, "decina": -1}
    corrente = None
    try:
        for c in cartelle:                          # le cartelle nuove si creano "in esclusiva": mai su una esistente
            if _corsa["annulla"].is_set():
                raise _Annullata()
            try:
                os.mkdir(c)
            except FileExistsError:
                raise _Fatale("una cartella di destinazione esiste già: mi fermo per non sovrascrivere")
        piccola = len(file) <= 30
        for s, d, b in file:
            if _corsa["annulla"].is_set():
                raise _Annullata()
            if not os.path.isdir(radice_dest):
                raise OSError(errno.ENODEV, "unità scollegata")
            corrente = d
            if piccola:
                _dire(f"[operazioni] copio {s} -> {d} ({b} byte)")
            try:
                _copia_file(s, d, lambda n: _avanza_byte(esito, n, totale_byte, stato_pr))
                esito["copiati"] += 1
                esito["copiati_lista"].append((s, d, b))
            except (_Annullata, _Fatale):
                raise
            except FileExistsError:
                raise _Fatale("un file di destinazione esiste già: mi fermo per non sovrascrivere")
            except FileNotFoundError:
                if not os.path.isdir(radice_dest):
                    raise OSError(errno.ENODEV, "unità scollegata")
                esito["errori"].append(f"{os.path.basename(s)}: non trovato")
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    raise _Fatale("il disco di destinazione è pieno")
                if not os.path.isdir(radice_dest):
                    raise
                esito["errori"].append(f"{os.path.basename(s)}: {_spiega(e)}")
        _progresso(stato_pr, totale_byte, totale_byte, "")
    except _Annullata:
        esito.update({"stato": "annullata", "motivo": "fermata a voce"})
        if corrente and os.path.exists(corrente + ".incompleto"):
            esito["incompleto"] = os.path.basename(corrente) + ".incompleto"
        return esito
    except _Fatale as e:
        esito.update({"stato": "parziale", "motivo": str(e)})
        if corrente and os.path.exists(corrente + ".incompleto"):
            esito["incompleto"] = os.path.basename(corrente) + ".incompleto"
        return esito
    except OSError as e:
        if corrente and os.path.exists(corrente + ".incompleto"):
            esito["incompleto"] = os.path.basename(corrente) + ".incompleto"
        if not os.path.isdir(radice_dest):
            esito.update({"stato": "scollegata", "motivo": "unità scollegata durante la copia"})
        else:
            esito.update({"stato": "parziale", "motivo": _spiega(e)})
        return esito
    if esito["errori"] or saltati:
        motivi = esito["errori"][:2] or [f"{saltati} elementi saltati (collegamenti o cartelle illeggibili)"]
        esito.update({"stato": "parziale", "motivo": "; ".join(motivi)})
        return esito
    # verifica: stesso numero di file e stessa dimensione totale
    controllo = _scandisci(top, 10 ** 9, 10 ** 15, secondi=60)
    if controllo["file"] != len(file) or controllo["byte"] != totale_byte:
        esito.update({"stato": "parziale", "motivo": (f"la verifica non torna: origine {len(file)} file e "
                                                      f"{totale_byte} byte, destinazione {controllo['file']} file e "
                                                      f"{controllo['byte']} byte")})
        return esito
    esito["motivo"] = f"{len(file)} file, {totale_byte} byte, verifica riuscita"
    esito["byte"] = totale_byte
    esito["top"] = top
    return esito


def _avanza_byte(esito, n, totale, stato_pr):
    esito["byte"] += n
    _progresso(stato_pr, esito["byte"], totale, "")


def _copia_file(s, d, callback):
    """Copia un file in modo esclusivo (mai sovrascrivere). Se viene fermata a metà, il file parziale
    resta con il suffisso .incompleto (non si cancella niente)."""
    fi = open(s, "rb")
    try:
        fo = open(d, "xb")                        # "x": se il file esiste già, ERRORE (mai sovrascrivere)
    except Exception:
        fi.close()
        raise
    completo = False
    try:
        while True:
            if _corsa["annulla"].is_set():
                raise _Annullata()
            blocco = fi.read(1024 * 1024)
            if not blocco:
                break
            fo.write(blocco)
            callback(len(blocco))
        completo = True
    finally:
        fi.close()
        fo.close()
        if not completo:
            try:
                os.rename(d, d + ".incompleto")
            except OSError:
                pass
    try:
        shutil.copystat(s, d)       # date e attributi (solo sul file NUOVO)
    except OSError:
        pass


def _rimuovi_originale(op, esito):
    """SOLO per lo spostamento tra dischi diversi, dopo copia e verifica riuscite: toglie l'originale.
    Si rimuovono solo i file copiati (e ancora identici) e poi le cartelle rimaste vuote."""
    src = op["sorgente"]
    lista = esito.get("copiati_lista", [])
    rimossi, tenuti = 0, []
    for s, d, b in lista:
        try:
            if os.path.getsize(s) != b:
                tenuti.append(os.path.basename(s))     # cambiato dopo la copia: non lo tocco
                continue
            os.remove(s)
            rimossi += 1
            _scrivi_op(f"RIMOSSO ORIGINALE (dopo verifica) | {s}")
        except OSError:
            tenuti.append(os.path.basename(s))
    if os.path.isdir(src):
        for base, dirs, files in sorted(os.walk(src, topdown=False), key=lambda x: -len(x[0])):
            try:
                os.rmdir(base)                         # solo se è vuota: altrimenti resta lì
            except OSError:
                pass
    if tenuti:
        esito.update({"stato": "parziale", "motivo": f"alcuni originali non sono stati rimossi ({', '.join(tenuti[:2])})"})
    else:
        esito["motivo"] += f"; originale rimosso ({rimossi} file)"
    return esito
