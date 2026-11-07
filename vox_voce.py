"""La voce di Eva: sintesi vocale di Windows (SAPI), voce italiana femminile (Elsa).

- Parla in un thread separato: non blocca ascolto né finestra.
- Due modi, scelti da soli:
    1) SAPI diretta con pywin32 (se installato:  python -m pip install pywin32)
    2) PowerShell + System.Speech (già dentro Windows, non serve installare nulla)
- parlando() dice se Eva sta parlando o ha finito da meno di VOCE_PAUSA_DOPO secondi:
  in quel tempo il microfono va ignorato, così Eva non ascolta se stessa.

Sicurezza: questo file SOLO legge ad alta voce un testo. Non apre, non scrive, non cancella.
"""
import base64
import os
import queue
import subprocess
import threading
import time

SCRIPT_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voci = @($s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo })
$nome = $env:VOX_VOCE
$cult = $env:VOX_CULTURA; if (-not $cult) { $cult = 'it' }
$sel = $voci | Where-Object { $nome -and $_.Name -like "*$nome*" -and $_.Culture.Name -like "$cult*" } | Select-Object -First 1
if (-not $sel) { $sel = $voci | Where-Object { $_.Culture.Name -like "$cult*" -and $_.Gender -eq 'Female' } | Select-Object -First 1 }
if (-not $sel) { $sel = $voci | Where-Object { $_.Culture.Name -like "$cult*" } | Select-Object -First 1 }
if ($sel) { $s.SelectVoice($sel.Name); $info = $sel.Name + ' (' + $sel.Culture.Name + ', ' + $sel.Gender + ')' } else { $info = 'NESSUNA-ITALIANA' }
$s.Rate = [int]$env:VOX_VELOCITA
$s.Volume = [int]$env:VOX_VOLUME
[Console]::Out.WriteLine('VOCE:' + $info); [Console]::Out.Flush()
if (-not $sel) { exit 0 }
while ($true) {
    $riga = [Console]::In.ReadLine()
    if ($riga -eq $null) { break }
    $vel = [int]$env:VOX_VELOCITA
    if ($riga -match '^R(-?\d+)\|(.*)$') { $vel = [int]$Matches[1]; $riga = $Matches[2] }   # velocità di QUESTA frase
    $s.Rate = $vel
    $testo = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($riga))
    try { $s.Speak($testo) } catch { }
    [Console]::Out.WriteLine('FATTO'); [Console]::Out.Flush()
}
"""

CONSIGLIO_VOCE_ITALIANA = (
    "Non trovo nessuna voce italiana. Per installarla: Impostazioni > Ora e lingua > "
    "Lingua e area geografica > Italiano > Opzioni lingua > Sintesi vocale (scarica), "
    "poi riavvia Eva. In alternativa: Impostazioni > Accessibilità > Narratore > "
    "Aggiungi voci naturali / Aggiungi voci.")


class Voce:
    def __init__(self, imp, log):
        self.log = log
        self.attiva = bool(imp.get("VOCE_ATTIVA", True))
        self.nome_voce = imp.get("VOCE_NOME", "Elsa")
        self.usa_com = bool(imp.get("VOCE_USA_COM", False))     # False = sempre PowerShell/System.Speech (come sempre)
        self.cultura = imp.get("VOCE_CULTURA", "it")           # it, fr, es, en (vedi vox_lingue.py)
        self.lcid = tuple(imp.get("VOCE_LCID", ("410",)))
        self.velocita = max(-10, min(10, int(imp.get("VOCE_VELOCITA", 0))))
        self.volume = max(0, min(100, int(imp.get("VOCE_VOLUME", 100))))
        self.pausa_dopo = float(imp.get("VOCE_PAUSA_DOPO", 1.0))
        self.disponibile = True            # diventa False se non si riesce a parlare
        self.voce_scelta = ""
        self._coda = queue.Queue()
        self._occupata = 0                 # frasi in coda o in lettura
        self._blocco = threading.Lock()
        self._fine = 0.0                   # quando ha finito di parlare l'ultima volta
        self._interrompi = threading.Event()
        self._processo = None
        self._thread = threading.Thread(target=self._lavora, name="vox-voce", daemon=True)
        self._thread.start()

    # --- uso da fuori -----------------------------------------------------
    def parlando(self):
        """True se sta parlando, ha frasi in coda, o ha finito da meno di pausa_dopo secondi."""
        return self._occupata > 0 or time.time() < self._fine + self.pausa_dopo

    def parla(self, testo, aspetta=False, max_secondi=60, velocita=None):
        """Legge il testo. Con aspetta=True torna a lettura finita, o al massimo dopo max_secondi
        (passato il tempo, interrompe la lettura: così niente può restare bloccato)."""
        if not testo or not self.attiva or not self.disponibile:
            return
        fatto = threading.Event()
        with self._blocco:
            self._occupata += 1
        self._coda.put((testo, fatto, velocita))
        if aspetta and not fatto.wait(timeout=max_secondi):
            self.interrompi()
            fatto.wait(timeout=1.0)

    def imposta_lingua(self, nome_voce, cultura, lcid=()):
        """Cambia la voce (es. Elsa -> Hortense) senza riavviare Eva: il processo di lettura riparte da solo
        con la nuova voce alla prossima frase. Restituisce il nome della voce scelta (o "" se non trovata)."""
        self.nome_voce, self.cultura = nome_voce, cultura
        if lcid:
            self.lcid = tuple(lcid)
        self.interrompi()
        p = self._processo
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass
        self._processo = None
        self._interrompi.clear()
        # controllo subito che la voce esista (così lo dico nel log e, se manca, so cosa fare)
        try:
            nuovo, info = self._avvia_processo()
            if nuovo is None:
                self.log(f"Voce {cultura}: NON trovata ({info}). {CONSIGLIO_VOCE_ITALIANA}")
                return ""
            self._processo = nuovo
            self.voce_scelta = f"{info} (PowerShell/System.Speech)"
            self.log(f"Voce cambiata: {self.voce_scelta}")
            return info
        except Exception:
            self.log.errore("Non riesco a cambiare la voce")
            return ""

    def silenzia(self, attesa=1.0):
        """Interrompe quello che sta dicendo e torna pronta a parlare di nuovo (entro 'attesa' secondi)."""
        if self._occupata <= 0:
            return
        self.interrompi()
        fine = time.time() + attesa
        while self._occupata > 0 and time.time() < fine:
            time.sleep(0.02)
        self._interrompi.clear()

    def interrompi(self):
        """Smette subito di parlare (per esempio se premi F9)."""
        self._interrompi.set()
        p = self._processo
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def ferma(self):
        self.interrompi()
        self._coda.put(None)

    # --- thread che parla ---------------------------------------------------
    def _lavora(self):
        leggi = None
        try:
            if not self.usa_com:      # come prima dell'installazione di pywin32: sempre PowerShell (cambia voce con la lingua)
                raise RuntimeError("SAPI/pywin32 disattivato (VOCE_USA_COM = False)")
            leggi = self._prepara_com()
        except Exception as errore:
            self.log.debug(f"[voce] pywin32 non usato ({errore}): uso PowerShell")
        if leggi is None:
            leggi = self._prepara_powershell()
        if leggi is None:
            self.disponibile = False
            self.log("Voce non disponibile: " + CONSIGLIO_VOCE_ITALIANA)
        else:
            self.log(f"Voce: {self.voce_scelta} | velocità {self.velocita} | volume {self.volume}"
                     + ("" if self.attiva else " | (spenta: VOCE_ATTIVA = False)"))
        while True:
            elemento = self._coda.get()
            if elemento is None:
                return
            testo, fatto, velocita = (elemento + (None,))[:3]
            try:
                if leggi is not None and self.attiva and not self._interrompi.is_set():
                    leggi(testo, velocita)
            except Exception:
                self.log.errore("Errore mentre parlo")
            finally:
                if self._coda.empty():
                    self._interrompi.clear()
                with self._blocco:
                    self._occupata = max(0, self._occupata - 1)
                self._fine = time.time()
                fatto.set()

    # --- modo 1: SAPI con pywin32 ----------------------------------------
    def _prepara_com(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        v = win32com.client.Dispatch("SAPI.SpVoice")
        voci = v.GetVoices()
        italiane = []
        for i in range(voci.Count):
            d = voci.Item(i)
            if any(l in d.GetAttribute("Language").split(";") for l in self.lcid):      # 410 = italiano, 40C = francese...
                italiane.append(d)
        scelta = None
        for d in italiane:
            if self.nome_voce and self.nome_voce.lower() in d.GetDescription().lower():
                scelta = d
                break
        if scelta is None:
            for d in italiane:
                if d.GetAttribute("Gender").lower() == "female":
                    scelta = d
                    break
        if scelta is None and italiane:
            scelta = italiane[0]
        if scelta is None:
            return None
        v.Voice = scelta
        v.Rate = self.velocita
        v.Volume = self.volume
        self.voce_scelta = f"{scelta.GetDescription()} (SAPI/pywin32)"

        def leggi(testo, velocita=None):
            v.Speak(testo, 1)                                   # 1 = asincrono
            while not v.WaitUntilDone(100):
                if self._interrompi.is_set():
                    v.Speak("", 3)                              # 3 = asincrono + svuota
                    break
        return leggi

    # --- modo 2: PowerShell + System.Speech ---------------------------------
    def _avvia_processo(self):
        env = dict(os.environ, VOX_VOCE=self.nome_voce, VOX_CULTURA=self.cultura, VOX_VELOCITA=str(self.velocita),
                   VOX_VOLUME=str(self.volume))
        codifica = base64.b64encode(SCRIPT_POWERSHELL.encode("utf-16-le")).decode("ascii")
        p = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", codifica],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, creationflags=0x08000000)                  # niente finestra nera
        prima = p.stdout.readline().decode("utf-8", "replace").strip()
        if not prima.startswith("VOCE:") or "NESSUNA-ITALIANA" in prima:
            p.kill()
            return None, prima
        return p, prima[5:]

    def _prepara_powershell(self):
        p, info = self._avvia_processo()
        if p is None:
            return None
        self._processo = p
        self.voce_scelta = f"{info} (PowerShell/System.Speech)"

        def leggi(testo, velocita=None):
            if self._processo is None or self._processo.poll() is not None:
                self._processo, _ = self._avvia_processo()      # riparte dopo un'interruzione
                if self._processo is None:
                    return
            riga = (b"R%d|" % int(velocita) if velocita is not None else b"") + base64.b64encode(testo.encode("utf-8")) + b"\n"
            try:
                self._processo.stdin.write(riga)
                self._processo.stdin.flush()
                self._processo.stdout.readline()                # aspetta "FATTO"
            except (OSError, ValueError):
                self._processo = None
        return leggi
