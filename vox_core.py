"""Il cuore di Eva: ascolto del microfono, parola "Eva", tasto F9, trascrizione.

Non disegna niente. Comunica con la finestra (vox_ui.py) tramite una coda di
eventi (Eva.eventi) e il volume del microfono (Eva.livello):
    ("mostra",)           la finestra deve comparire
    ("fase", "IN ASCOLTO" | "ELABORO" | "ESEGUO")
    ("frase", testo)      ultima frase riconosciuta
    ("nota", testo)       messaggio sotto la sfera (es. "Non ho capito")
    ("chiudi", secondi)   la finestra si chiude con dissolvenza dopo N secondi

Sicurezza: i comandi sono quelli di azioni.py / comandi.py, che SOLO aprono,
cercano, regolano il volume e preparano bozze.
"""
import collections
import difflib
import datetime as dt
import os
import queue
import re
import threading
import time

import numpy as np

import eva_registro as registro
import vox_cervello as cervello
import vox_file
import vox_ia_azioni as ia_azioni
import aiuto
import comandi_pc
import memoria
import promemoria
import utilita
import vox_lingue as lingue
import vox_operazioni as operazioni
from azioni import MAX_PAROLE_OGGETTO, Risultato, _norm_token, chiudi_finestra, interpreta
from comandi import ARTICOLI, normalizza
from vox_voce import Voce

FREQ = 16000            # campioni al secondo
BLOCCO = 480            # 30 millisecondi


# ----------------------------------------------------------------------
# Parola di attivazione "Eva"
# ----------------------------------------------------------------------
PREFISSI_WAKE = {"ehi", "ei", "hey", "ok", "okay", "ciao", "salve"}
PREFISSI_CHIAMATA = {"ehi", "ei", "hey", "ok", "okay"}      # "ehi va" = "ehi Eva" (non "ciao va")
# "e va" / "ehi va" potrebbero essere italiano normale ("e va bene"): dopo queste parole NON è il nome
DOPO_E_VA_NO = {"bene", "male", "tutto", "ok", "cosi", "via", "su", "gia", "spesso", "sempre", "a", "in", "di",
                "che", "ma", "per", "meglio", "pure", "avanti", "dai", "piano", "forte", "benissimo", "la", "li",
                "tranquillo", "così"}


def _simile_a(parola, varianti, lunghezza_minima, soglia=0.8):
    """difflib: la parola somiglia ad almeno una variante (solo varianti abbastanza lunghe,
    perché con 3 lettere un solo errore fa scendere la somiglianza troppo)."""
    return any(len(v) >= lunghezza_minima and difflib.SequenceMatcher(None, parola, v).ratio() >= soglia
               for v in varianti)


def riconosci_nome(testo, varianti_nome, varianti_vecchio=()):
    """Se la frase INIZIA con il nome "Eva" (anche storpiato: eba, ieva, ewa...), oppure è solo il nome,
    restituisce (True, resto della frase), altrimenti (False, "").

    Il nome in mezzo alla frase (detta a un'altra persona) NON attiva niente.
    varianti_vecchio: il vecchio nome ("Vox" e le sue storpiature), se ACCETTA_VECCHIO_NOME.
    """
    parole = testo.split()
    norm = [_norm_token(p) for p in parole]
    i = 0
    while i < len(norm) and i < 2 and norm[i] in PREFISSI_WAKE:
        i += 1
    if i >= len(norm) or not norm[i]:
        return False, ""
    nomi = {" ".join(n for n in (_norm_token(x) for x in v.split()) if n) for v in varianti_nome}
    nomi.discard("")
    singoli = {n for n in nomi if " " not in n}
    lunghi = [n for n in singoli if len(n) >= 4]

    def trovato(quante):
        resto = " ".join(parole[i + quante:]).lstrip(",.;:!?- ").strip()
        return True, resto

    def dopo_ok(quante):
        """Per le forme ambigue ("e va"): la frase non deve sembrare italiano normale."""
        seguenti = norm[i + quante:]
        return len(seguenti) <= 6 and (not seguenti or seguenti[0] not in DOPO_E_VA_NO)

    tok = norm[i]
    if tok in singoli or (len(tok) >= 4 and _simile_a(tok, lunghi, 4)):
        return trovato(1)
    # due parole che unite fanno il nome: "e va" -> "eva"
    if i + 1 < len(norm):
        due = tok + " " + norm[i + 1]
        if (due in nomi or tok + norm[i + 1] in singoli) and dopo_ok(2):
            return trovato(2)
    # "ehi va" = "ehi Eva"
    if tok == "va" and i > 0 and norm[i - 1] in PREFISSI_CHIAMATA and "ehi va" in nomi and dopo_ok(1):
        return trovato(1)
    # il vecchio nome (Vox): stessa regola di prima, solo somiglianza di lettere
    if len(tok) >= 3 and _simile_a(tok, [_norm_token(v) for v in varianti_vecchio], 3):
        return trovato(1)
    return False, ""


# ----------------------------------------------------------------------
# Rilevatore di voce leggero (a volume, con soglia che si adatta al rumore)
# ----------------------------------------------------------------------
class RilevatoreVoce:
    """Raccoglie i brevi pezzi in cui si sente parlare.

    blocco() riceve un blocco di audio alla volta e restituisce l'audio di un
    pezzo di parlato quando questo è finito (poi silenzio), altrimenti None.
    """

    def __init__(self, soglia_min=0.006, blocchi_inizio=3, blocchi_fine=20,
                 blocchi_minimi=8, blocchi_massimi=400):
        self.soglia_min = soglia_min
        self.blocchi_inizio = blocchi_inizio      # 90 ms di voce per partire
        self.blocchi_fine = blocchi_fine          # 600 ms di silenzio per finire
        self.blocchi_minimi = blocchi_minimi      # con meno di ~0,25 s di voce: scartato
        self.blocchi_massimi = blocchi_massimi    # un pezzo dura al massimo 12 s
        self.rumore = 0.002
        self.reset()

    def reset(self):
        self.parlando = False
        self.conta_voce = 0
        self.conta_silenzio = 0
        self.blocchi_con_voce = 0
        self.segmento = []
        self.precedenti = collections.deque(maxlen=10)    # 300 ms prima della voce

    def blocco(self, dati, rms):
        soglia_on = max(self.soglia_min, self.rumore * 3.5)
        soglia_off = max(self.soglia_min * 0.7, self.rumore * 2.2)
        if not self.parlando:
            self.precedenti.append(dati)
            if rms > soglia_on:
                self.conta_voce += 1
            else:
                self.conta_voce = 0
                # il rumore di fondo si aggiorna solo quando non si parla
                self.rumore = min(0.02, 0.98 * self.rumore + 0.02 * max(rms, 0.0003))
            if self.conta_voce >= self.blocchi_inizio:
                self.parlando = True
                self.segmento = list(self.precedenti)
                self.conta_silenzio = 0
                self.blocchi_con_voce = self.conta_voce
            return None
        self.segmento.append(dati)
        if rms < soglia_off:
            self.conta_silenzio += 1
        else:
            self.conta_silenzio = 0
            self.blocchi_con_voce += 1
        if self.conta_silenzio >= self.blocchi_fine or len(self.segmento) >= self.blocchi_massimi:
            togli = max(0, self.conta_silenzio - 3)         # tengo un po' di silenzio
            blocchi = self.segmento[:len(self.segmento) - togli]
            lunghi = self.blocchi_con_voce >= self.blocchi_minimi
            self.reset()
            return np.concatenate(blocchi) if lunghi else None
        return None


# ----------------------------------------------------------------------
# Eva
# ----------------------------------------------------------------------
class Vox:
    def __init__(self, imp, log):
        """imp: impostazioni (dizionario, vedi dettatura.py); log: funzione di stampa."""
        self.imp = imp
        self.log = log
        lingua = lingue.inizializza(imp)                 # lingua salvata (o LINGUA): voce, parole suggerite, chiusura
        log(f"Lingua: {lingue.pacchetto()['nome']} ({lingua}) | voce {imp['VOCE_NOME']} | trascrizione {lingue.codice_whisper()}")
        self.eventi = queue.Queue()
        self.livello = 0.0                   # volume del microfono, 0..1
        self.pronto = False
        self.modo = "riposo"                 # riposo | attesa | f9
        self.fermo = threading.Event()
        self.coda = queue.Queue(maxsize=600)
        self.f9_giu = False
        self.attiva_richiesta = False
        self.f9_blocchi = []
        self.t_attesa = 0.0
        self.conversazione = False           # True = conversazione continua attiva (finestra aperta)
        registro.scrivi(f"EVA AVVIATA | versione {imp.get('VERSIONE', '?')} | registro: {registro.FILE_REGISTRO}")      # crea eva_log.txt subito all'avvio
        self._misuratore = None              # misuratore dell'audio in uscita (pycaw); False = non disponibile
        self._t_misura = 0.0
        self._t_audio_attivo = 0.0
        self._compagna = bool(imp.get("COMPAGNA", False))   # modalità compagna (dettatura.py: COMPAGNA)
        cervello.COMPAGNA = self._compagna
        self._vista = "chiusa"               # aspetto della sfera: grande | ridotta | nascosta | chiusa
        self._riduci_tra = None              # quando rimpicciolire la sfera dopo una risposta senza aperture
        self._motivo_riduci = ""
        self._fissa_grande = False           # "mettiti al centro": resta grande finché non apro qualcosa o la riduci
        self._schermo_intero = False         # benvenuto a schermo intero: resta così finché non dici "mettiti da parte"
        self.vad =RilevatoreVoce(soglia_min=imp["SOGLIA_VOCE_MIN"])
        self.suggerimento = ", ".join(imp["PAROLE_SUGGERITE"]) + "."
        self.eco = normalizza(self.suggerimento)
        self._nomi_prompt_ok = False
        self._t_fase = time.perf_counter()
        self._pr_stato = None                # domanda in corso sui promemoria (cosa / quando / mattina-pomeriggio / cancella...)
        self._pr_prossimo = 0.0              # prossimo controllo dei promemoria scaduti
        self._ut_stato = None                # domanda in corso sulle utilità (durata del timer, conferma di Wi-Fi, appunti...)
        self._aiuto_stato = None             # ho chiesto "vuoi sentire i dettagli di un gruppo?" e aspetto la risposta
        self._pc_attesa = None               # conferma a voce in sospeso per un comando pericoloso (libreria PC)
        self._ultima_conf = None             # sicurezza dell'ultima trascrizione (avg_logprob più basso dei pezzi tenuti)
        self._attesa_citta = None            # None, oppure 0/1 (oggi/domani) se ho chiesto la città del meteo
        self.false_note = [n for n in (normalizza(f) for f in imp.get("FRASI_FALSE", [])) if n]
        self.modello = None
        self.modello_wake = None
        self._thread = None
        self._stream = None
        self.voce = Voce(imp, log)           # parla in un suo thread
        cervello.configura_chiusura(imp.get("FRASI_CHIUSURA"))      # le frasi che chiudono Eva
        ia_azioni.configura(imp)                                    # ripiego con Groq (GROQ_AZIONI...)
        # copia / sposta file: gira in un thread suo e parla/aggiorna la finestra tramite queste funzioni
        operazioni.configura(imp, log, emetti=self.eventi.put, parla=self._parla_da_thread,
                             conversazione=lambda: self.conversazione)

    def _nome(self, testo):
        """Riconosce "Eva" (e, se acceso, il vecchio nome "Vox") all'inizio della frase."""
        nomi = self.imp.get("VARIANTI_NOME") or self.imp["WAKE_VARIANTI"]
        vecchio = self.imp.get("VARIANTI_VECCHIO_NOME", ()) if self.imp.get("ACCETTA_VECCHIO_NOME", False) else ()
        return riconosci_nome(testo, nomi, vecchio)

    # --- avvio e arresto -------------------------------------------------
    def avvia(self):
        import keyboard
        vox_file.avvia_indice(self.imp, self.log)      # indice di cartelle e file, in un thread suo
        keyboard.on_press_key(self.imp["TASTO"], self._tasto_premuto)
        keyboard.on_release_key(self.imp["TASTO"], self._tasto_rilasciato)
        self._thread = threading.Thread(target=self._ciclo, name="vox-ascolto", daemon=True)
        self._thread.start()

    def ferma(self):
        self.fermo.set()
        operazioni._corsa["annulla"].set()      # se sta copiando, si ferma (non si cancella niente)
        self.voce.ferma()
        vox_file.ferma_indice()
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass

    def cambia_lingua_da_menu(self, codice):
        """Dal menu accanto all'orologio: cambia lingua come il comando vocale (in un thread, senza bloccare la finestra)."""
        if codice == lingue.corrente or not self.pronto:
            return
        threading.Thread(target=self._cambia_lingua, args=(codice,), daemon=True).start()

    def attiva_ora(self):
        """Come dire "Eva": la finestra compare e Eva aspetta un comando."""
        if not self.pronto:
            self._mostra_grande()
            self.eventi.put(("nota", lingue.t("caricamento")))
            self.eventi.put(("chiudi", 1.5))
            return
        self.attiva_richiesta = True

    def _tasto_premuto(self, _evento):
        if not self.f9_giu:
            self.voce.interrompi()           # se Eva sta parlando, si ferma: ora parli tu
        self.f9_giu = True

    def _tasto_rilasciato(self, _evento):
        self.f9_giu = False

    # --- ciclo principale (thread) ---------------------------------------
    def _ciclo(self):
        try:
            try:
                import comtypes
                comtypes.CoInitialize()          # serve a pycaw (volume) in questo thread
            except Exception:
                pass
            self._t_fase = time.perf_counter()
            threading.Thread(target=self._prepara_groq, name="vox-groq", daemon=True).start()
            self._carica_modelli()
            self._apri_microfono()
            self._tempo("microfono")
            self._benvenuto()
            self._avvia_compagna()
            self._tempo("saluto (parte a voce)")
            threading.Thread(target=comandi_pc.precarica, name="vox-comandi-pc", daemon=True).start()   # dopo il saluto: non rallenta l'avvio
            while not self.fermo.is_set():
                try:
                    self._pr_tick()                     # promemoria scaduti?
                except Exception:
                    self.log.errore("Errore nel controllo dei promemoria")
                try:
                    dati = self.coda.get(timeout=0.2)
                except queue.Empty:
                    continue
                if ((self.voce.parlando() or self._audio_in_riproduzione())
                        and not self.f9_giu and self.modo != "f9"):
                    # Eva sta parlando (o ha appena finito) o il PC sta suonando un video/musica: non ascolto
                    self.vad.reset()
                    self.livello *= 0.8
                    continue
                rms = float(np.sqrt(np.mean(dati * dati)))
                self.livello = 0.8 * self.livello + 0.2 * min(1.0, rms / 0.05)
                self._passo(dati, rms)
        except Exception:
            self.log.errore("Errore nel ciclo di ascolto")
            try:
                self._parla(lingue.t("errore_ascolto", nome=cervello.nome_utente()))
            except Exception:
                pass
            self._mostra_grande()
            self.eventi.put(("nota", "Errore: vedi eva.log"))
            self.eventi.put(("chiudi", 4.0))

    def _tempo(self, fase, secondo_piano=False):
        """Scrive nel log quanto è durata una fase dell'avvio e il tempo totale dall'accensione di Eva.
        Le fasi in secondo piano (girano insieme alle altre) scrivono solo il tempo totale."""
        adesso = time.perf_counter()
        t0 = self.imp.get("_T0", adesso)
        if secondo_piano:
            self.log(f"TEMPO avvio: {fase}: pronto a {adesso - t0:.1f} s dall'accensione")
            return
        self.log(f"TEMPO avvio: {fase}: {adesso - self._t_fase:.1f} s (totale {adesso - t0:.1f} s)")
        self._t_fase = adesso

    def _prepara_groq(self):
        """In secondo piano: carica il pacchetto Groq, crea il client e risolve l'indirizzo (la prima chiamata sarà pronta)."""
        try:
            import os
            import socket
            if not os.environ.get("GROQ_API_KEY"):
                self.log("ATTENZIONE: GROQ_API_KEY non trovata: Eva non potrà chiacchierare con Groq.")
                return
            socket.getaddrinfo("api.groq.com", 443)
            cervello._modello_groq_pronto()
            self._tempo("collegamento a Groq (secondo piano)", True)
        except Exception:
            self.log.errore("Groq non è raggiungibile all'avvio (internet assente? Riprovo alla prima richiesta)")

    def _benvenuto(self):
        """All'avvio: schermo intero con il benvenuto grafico e saluto a voce; poi resto in ascolto.
        Si esce da questa modalità solo con "mettiti da parte"."""
        if not self.imp.get("BENVENUTO_SCHERMO_INTERO", True):
            return
        nome = self.imp.get("NOME_UTENTE") or cervello.nome_utente()
        saluto = lingue.t("benvenuto", nome=nome)
        self._schermo_intero = True
        self._fissa_grande = True
        self._vista = "grande"
        self.log("Benvenuto a schermo intero (resta finché non dici «mettiti da parte»)")
        self.eventi.put(("benvenuto",))
        self.eventi.put(("frase", ""))
        self._inizia_conversazione("benvenuto all'avvio")
        self.log(f"Risposta detta: {saluto}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(saluto)
        self._inizia_attesa()

    def _audio_in_riproduzione(self):
        """Compagna: True se il PC sta riproducendo audio (video, musica) o l'ha fatto da meno di 1,5 secondi.
        Legge il livello d'uscita degli altoparlanti con pycaw; se non è disponibile non blocca mai l'ascolto."""
        if not (self._compagna and self.imp.get("COMPAGNA_IGNORA_AUDIO", True)) or self._misuratore is False:
            return False
        adesso = time.time()
        if adesso - self._t_misura < 0.2:
            return adesso - self._t_audio_attivo < 1.5
        self._t_misura = adesso
        try:
            if self._misuratore is None:
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
                disp = AudioUtilities.GetSpeakers()
                disp = getattr(disp, "_dev", disp)
                self._misuratore = cast(disp.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None),
                                        POINTER(IAudioMeterInformation))
                self.log("Compagna: ignoro l'ascolto mentre il PC riproduce audio (video, musica)")
            if self._misuratore.GetPeakValue() > 0.01:
                if adesso - self._t_audio_attivo > 1.5:
                    registro.scrivi("ASCOLTO SOSPESO: il PC sta riproducendo audio")
                    self.vad.reset()
                self._t_audio_attivo = adesso
        except Exception:
            self.log("Compagna: non riesco a leggere l'audio in uscita (pycaw): ascolto sempre")
            self._misuratore = False
            return False
        return adesso - self._t_audio_attivo < 1.5

    def _avvia_compagna(self):
        """Modalità compagna: dopo l'avvio Eva resta SEMPRE in ascolto (senza F9 e senza «Eva»)."""
        if not self._compagna:
            return
        self.conversazione = True
        self.log("MODALITÀ COMPAGNA: ascolto sempre, conversazione continua (per tornare a F9: COMPAGNA = False in dettatura.py)")
        registro.scrivi("MODALITÀ COMPAGNA attiva: ascolto sempre, senza F9 e senza parola di attivazione")
        self._inizia_attesa()

    def _carica_modelli(self):
        from faster_whisper import WhisperModel
        import os
        thread = min(8, os.cpu_count() or 4)

        def carica(nome):
            self.log(f"Carico il modello '{nome}'...")
            try:
                m = WhisperModel(nome, device="cpu", compute_type="int8",
                                 cpu_threads=thread, local_files_only=True)
            except Exception:
                self.log("Modello non ancora scaricato: lo scarico (solo la prima volta)...")
                m = WhisperModel(nome, device="cpu", compute_type="int8", cpu_threads=thread)
            list(m.transcribe(np.zeros(FREQ, dtype="float32"), language=lingue.codice_whisper())[0])   # prova a vuoto
            return m

        self._tempo("librerie pesanti (faster_whisper, numpy)")
        nome_wake = self.imp["MODELLO_WAKE"]
        if self.imp["WAKE_WORD"] and nome_wake != self.imp["MODELLO"]:
            # AVVIO RAPIDO: prima il modello veloce (basta per "Eva" e per i primi comandi), così Eva saluta subito;
            # il modello preciso si carica poi in secondo piano e sostituisce quello veloce appena è pronto.
            self.modello_wake = self.modello = carica(nome_wake)
            self._tempo(f"modello veloce '{nome_wake}'")

            def carica_preciso():
                try:
                    preciso = carica(self.imp["MODELLO"])
                    self.modello = preciso
                    self._tempo(f"modello preciso '{self.imp['MODELLO']}' (secondo piano)", True)
                except Exception:
                    self.log.errore("Non riesco a caricare il modello preciso: continuo con quello veloce")
            threading.Thread(target=carica_preciso, name="vox-modello-preciso", daemon=True).start()
        else:
            self.modello = carica(self.imp["MODELLO"])
            self.modello_wake = self.modello
            self._tempo(f"modello '{self.imp['MODELLO']}'")
        import azioni
        correzioni, app, pycaw, alias = azioni.precarica()
        self.log(f"Correzioni: {correzioni} | app trovate: {app} | alias tuoi: {alias}")
        self.log("Volume: " + ("pycaw (preciso)" if pycaw else
                               "tasti multimediali (per più precisione: python -m pip install pycaw)"))
        n_intenti, nome = cervello.carica(self.log)
        cervello._dati["utente"] = self.imp.get("NOME_UTENTE", nome)
        self.log(f"Risposte: {n_intenti} gruppi in risposte.json | utente: {cervello.nome_utente()}")
        self._tempo("correzioni, app e risposte")
        self.pronto = True
        self.log(f"Pronto. Tieni premuto {self.imp['TASTO'].upper()}"
                 + (" oppure di' \"Eva\"." if self.imp["WAKE_WORD"] else "."))

    def _apri_microfono(self):
        import sounddevice as sd

        def richiamo(indata, frames, tempo, stato):
            try:
                self.coda.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass                                # meglio perdere audio che bloccarsi

        # All'accensione del PC l'audio può non essere pronto: riprovo per circa 2 minuti.
        ultimo = None
        for tentativo in range(1, 41):
            try:
                self._stream = sd.InputStream(samplerate=FREQ, channels=1, dtype="float32",
                                              blocksize=BLOCCO, callback=richiamo)
                self._stream.start()
                if tentativo > 1:
                    self.log(f"Microfono aperto al tentativo {tentativo}.")
                return
            except Exception as errore:
                ultimo = errore
                self.log(f"Microfono non pronto (tentativo {tentativo}/40): {errore}")
                try:
                    sd._terminate(); sd._initialize()      # rilegge l'elenco dei dispositivi audio
                except Exception:
                    pass
                if self.fermo.wait(3.0):
                    return
        raise RuntimeError(f"Microfono non disponibile dopo 40 tentativi: {ultimo}")

    # --- logica a stati ---------------------------------------------------
    def _passo(self, dati, rms):
        premuto = self.f9_giu
        if self.modo == "f9":
            self.f9_blocchi.append(dati)
            if not premuto:
                self._fine_f9()
            return
        if premuto:
            self._inizia_f9()
            return
        if self.modo == "riposo":
            if self.attiva_richiesta:
                self.attiva_richiesta = False
                self._mostra_grande()
                self._inizia_conversazione('"Attiva ora" (icona o secondo avvio)')
                self._inizia_attesa()
                return
            if self.imp["WAKE_WORD"]:
                audio = self.vad.blocco(dati, rms)
                if audio is not None:
                    self._gestisci_wake(audio)
        elif self.modo == "attesa":
            if operazioni.scaduta() and not self.vad.parlando:
                # una conferma di copia/spostamento è rimasta senza risposta: non si fa niente
                detta = operazioni.annulla_per_timeout()
                self.log(f"Azione eseguita: nessuna (nessuna risposta alla conferma)")
                self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
                self._parla(detta)
                self._fine_turno(1.0)
                return
            if self._riduci_tra and not self.vad.parlando and time.time() >= self._riduci_tra:
                self._riduci_tra = None
                if self.imp.get("SPARISCI_DOPO_COMANDO", True):
                    self._sparisci(self._motivo_riduci or "dopo la risposta")
                    return
                self._vista_imposta("ridotta", self._motivo_riduci or "risposta senza apertura")
            audio = self.vad.blocco(dati, rms)
            if audio is None and self.vad.parlando and self.vad.blocchi_con_voce >= 12:
                self._riduci_tra = None          # sto parlando (da più di ~0,4 s): la sfera torna grande
                if self._vista == "ridotta" and self.conversazione:
                    self._vista_imposta("grande", "nuova richiesta")
            if audio is not None:
                self._comando_da_audio(audio)
            elif self.conversazione:
                # conversazione continua: nessuna fretta, ma dopo TIMEOUT_CONVERSAZIONE secondi
                # di silenzio Eva saluta e torna a riposo
                limite = self.imp.get("TIMEOUT_CONVERSAZIONE", 300)
                if (not self._compagna and not self.vad.parlando and not self._schermo_intero
                        and time.time() - self.t_attesa > limite):
                    self._finisci_conversazione(f"timeout ({limite} secondi senza voce)",
                                                cervello.testo_fisso("timeout",
                                                                     "Torno a riposo, chiamami quando vuoi."))
            elif (not self.vad.parlando and not operazioni.in_corso() and not self._schermo_intero
                  and time.time() - self.t_attesa > self.imp["SECONDI_SILENZIO"]):
                self.log(f"Silenzio per {self.imp['SECONDI_SILENZIO']} secondi: torno a riposo (senza parlare).")
                self._chiudi(0.0)

    def _inizia_f9(self):
        self.modo = "f9"
        self.f9_blocchi = []
        self.vad.reset()
        self._mostra_grande()
        self.eventi.put(("frase", ""))
        self.eventi.put(("nota", ""))
        self.eventi.put(("fase", "IN ASCOLTO"))

    def _fine_f9(self):
        audio = np.concatenate(self.f9_blocchi) if self.f9_blocchi else np.zeros(0, np.float32)
        self.f9_blocchi = []
        if len(audio) < 0.3 * FREQ:
            self.log("Scartato: pressione troppo breve")
            self._fine_turno(0.0)
            return
        self.eventi.put(("fase", "ELABORO"))
        testo = self._trascrivi(audio, self.modello)
        if not testo:
            self.log("Scartato: nessun parlato valido (F9)")
            self._fine_turno(0.3)
            return
        trovato, resto = self._nome(testo)
        if not trovato:
            # F9 normale: dettatura. Non entra in conversazione (se non era già attiva).
            self._esegui_comando(testo, da_vox=False, originale=testo)
            return
        # F9 con "Eva" davanti: come dire "Eva"
        self.log(f'Frase riconosciuta con F9 e "Eva": {testo}')
        self._inizia_conversazione('F9 con la parola «Eva»')
        if resto:
            self._esegui_comando(resto, da_vox=True, originale=testo)
        else:
            self._vox_da_solo()

    def _gestisci_wake(self, audio):
        testo = self._trascrivi(audio, self.modello_wake, veloce=True)
        if not testo:
            return
        trovato, resto = self._nome(testo)
        self.log.debug(f"[ascolto] {testo!r} -> vox={trovato}")
        if not trovato:
            return
        self.log(f'Parola di attivazione sentita: "{testo}"')
        self._mostra_grande()
        self.eventi.put(("frase", ""))
        self.eventi.put(("nota", ""))
        self._inizia_conversazione('parola «Eva»' + (" con un comando" if resto else ""))
        if resto:
            # "Eva apri Chrome" tutto insieme: rileggo con il modello più preciso
            self.eventi.put(("fase", "ELABORO"))
            motivo = self._esito_veloce(testo) if (self.imp.get("TRASCRIZIONE_VELOCE_PRIMA", True)
                                                   and self.modello_wake is not self.modello) else "sempre"
            if motivo is None:
                self.log(f"TRASCRIZIONE: veloce sufficiente (sicurezza {self._ultima_conf:.2f}): non uso il preciso")
            elif self.modello_wake is not self.modello:
                if motivo != "sempre":
                    self.log(f"TRASCRIZIONE: la veloce non basta ({motivo}): rileggo con il preciso")
                preciso = self._trascrivi(audio, self.modello)
                t2, resto2 = self._nome(preciso) if preciso else (False, "")
                if t2 and resto2:
                    testo, resto = preciso, resto2
            self._esegui_comando(resto, da_vox=True, originale=testo)
        else:
            self._vox_da_solo()

    def _vox_da_solo(self):
        """"Eva" da solo (anche a conversazione già attiva): saluto breve, poi aspetto il comando."""
        saluto = cervello.saluto_vox_da_solo()
        if self._vista in ("ridotta", "nascosta"):
            self._mostra_grande("nuova richiesta: «Eva»")
        self.log("Frase corretta: (parola «Eva» da sola)")
        self.log("Azione eseguita: ascolto il prossimo comando")
        self.log(f"Risposta detta: {saluto}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(saluto)
        self._inizia_attesa()
        self._programma_riduzione(10.0, "chiamata senza comando")      # se non dici niente, sparisce dopo qualche secondo

    # --- aspetto della sfera (grande / ridotta / nascosta) ------------------------------------------------
    def _mostra_grande(self, motivo="nuova richiesta"):
        """Eva è stata chiamata (nome, F9, "Attiva ora"): la sfera compare o torna GRANDE al centro."""
        if self._vista in ("ridotta", "nascosta"):
            self.log(f"Sfera: si ingrandisce ({motivo})")
        self._vista = "grande"
        self._riduci_tra = None
        self.eventi.put(("mostra",))

    def _vista_imposta(self, modo, motivo, auto=True):
        """Cambia l'aspetto della sfera e lo scrive nel registro con il motivo.
        auto=True: cambio automatico (dopo un'apertura...): non avviene se RIDUCI_DOPO_APERTURA è False."""
        if auto and (self._schermo_intero or not self.imp.get("RIDUCI_DOPO_APERTURA", True)):
            return
        if self._vista == modo:
            return
        come = {"ridotta": "si riduce", "grande": "si ingrandisce", "nascosta": "si nasconde"}[modo]
        self.log(f"Sfera: {come} ({motivo})")
        self._vista = modo
        self.eventi.put(("vista", modo))

    @staticmethod
    def _apre_qualcosa(r):
        """True se il comando ha APERTO qualcosa (programma, sito, cartella, file, ricerca, bozza)."""
        if r.tipo != "eseguito":
            return False
        msg = (r.messaggio or "").lower()
        return any(k in msg for k in ("aperto", "aperta", "apertura", "cerco su", "bozza"))

    def _sparisci(self, motivo):
        """Eva SPARISCE del tutto (finestra nascosta, anche dal benvenuto a schermo intero) e torna a riposo: da quel
        momento ascolta solo la parola «Eva» o F9. Serve a non coprire quello che si è appena aperto."""
        self.log(f"Sfera: sparisce del tutto ({motivo}); torna con «Eva» o con F9")
        self._riduci_tra = None
        self._fissa_grande = False
        self._schermo_intero = False
        self.conversazione = False
        self._chiudi(0.0)

    @staticmethod
    def _cambia_qualcosa(r):
        """True se il comando ha ESEGUITO qualcosa sul PC (aprire, cercare, chiudere una finestra, volume, bozza...)."""
        if r.tipo != "eseguito":
            return False
        return not (r.frase_corretta or "").lower().startswith(("impara", "annulla"))

    def _programma_riduzione(self, secondi=3.0, motivo="risposta senza apertura"):
        """Dopo una risposta la sfera resta visibile qualche secondo, poi sparisce (SPARISCI_DOPO_COMANDO) o si riduce."""
        if self.imp.get("SPARISCI_DOPO_COMANDO", True):
            if (self.conversazione and not motivo.startswith("nessun parlato") and not operazioni.in_attesa()
                    and (not self._fissa_grande or self._schermo_intero)):
                self._riduci_tra = time.time() + max(secondi, float(self.imp.get("SECONDI_PRIMA_DI_SPARIRE", 5)))
                self._motivo_riduci = motivo
            return
        if (self.conversazione and self.imp.get("RIDUCI_DOPO_APERTURA", True) and self._vista == "grande"
                and not self._fissa_grande and not operazioni.in_attesa()):
            self._riduci_tra = time.time() + secondi
            self._motivo_riduci = motivo

    def _comando_vista(self, cv, originale):
        """"mettiti da parte", "mettiti al centro", "nasconditi", "sposta in alto a sinistra"."""
        extra = f" {cv.verticale or ''} {cv.orizzontale or ''}".rstrip() if cv.azione == "angolo" else ""
        self.log(f"Frase corretta: (sfera: {cv.azione}{extra})")
        self._riduci_tra = None
        self._fissa_grande = cv.azione == "grande"
        if cv.azione in ("ridotta", "angolo", "nascosta"):
            self._schermo_intero = False         # "mettiti da parte": finito il benvenuto a schermo intero
        if cv.azione == "angolo":
            self.log(f"Sfera: si sposta e si riduce (comando vocale: {cv.verticale or '-'} {cv.orizzontale or '-'})")
            self._vista = "ridotta"
            self.eventi.put(("angolo", cv.orizzontale, cv.verticale))
            dove = " ".join(x for x in [("in " + cv.verticale) if cv.verticale else "",
                                         ("a " + cv.orizzontale) if cv.orizzontale else ""] if x)
            detta = cervello.testo_fisso("vista_angolo", "Mi sposto {dove}.", dove=dove)
        else:
            self._vista_imposta(cv.azione, "comando vocale", auto=False)
            detta = cervello.testo_fisso({"ridotta": "vista_ridotta", "grande": "vista_grande",
                                          "nascosta": "vista_nascosta"}[cv.azione],
                                         {"ridotta": "Mi metto da parte.", "grande": "Eccomi qui.",
                                          "nascosta": "Mi nascondo, ma ti ascolto."}[cv.azione])
        self.log("Azione eseguita: sfera " + {"ridotta": "ridotta", "grande": "grande al centro", "nascosta": "nascosta",
                                              "angolo": "spostata nell'angolo"}[cv.azione])
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(detta)
        self._fine_turno(1.0)

    def _inizia_attesa(self):
        self.modo = "attesa"
        self.t_attesa = time.time()
        self.vad.reset()
        self.eventi.put(("fase", "ESEGUO" if operazioni.in_corso() else "IN ASCOLTO"))   # sta copiando: resta ESEGUO

    def _comando_da_audio(self, audio):
        self.eventi.put(("fase", "ELABORO"))
        testo = self._trascrivi_adattiva(audio)
        if not testo:
            # niente di valido (silenzio, rumore, frase falsa): non dico niente e continuo ad
            # aspettare; se i secondi di silenzio finiscono, _passo torna a riposo in silenzio
            self.log("Scartato: nessun parlato valido, aspetto ancora")
            self.vad.reset()
            self.eventi.put(("fase", "IN ASCOLTO"))
            self._programma_riduzione(1.0, "nessun parlato valido")
            return
        corto = normalizza(testo).replace(" ", "")
        if len(corto) < 2 and not corto.isdigit():          # "1", "2", "3" servono per scegliere
            self.log(f"Scartato: frase troppo corta ({testo!r})")
            self.vad.reset()
            self.eventi.put(("fase", "IN ASCOLTO"))
            return
        trovato, resto = self._nome(testo)
        if (self._compagna and not trovato and self._ultima_conf is not None
                and self._ultima_conf < float(self.imp.get("COMPAGNA_CONFIDENZA_MIN", -0.7))
                and not (operazioni.in_attesa() or vox_file.scelta_in_attesa() or self._pc_attesa is not None
                         or self._pr_stato is not None or self._aiuto_stato is not None
                         or self._ut_stato is not None or self._attesa_citta is not None)):
            self.log(f"Scartata (compagna): sicurezza {self._ultima_conf:.2f} < {self.imp.get('COMPAGNA_CONFIDENZA_MIN', -0.7)}: {testo!r}")
            registro.scrivi(f"FRASE SCARTATA (sicurezza bassa {self._ultima_conf:.2f}, nessuna risposta): {testo[:150]}")
            self.vad.reset()
            self.eventi.put(("fase", "IN ASCOLTO"))
            return
        if self._compagna and self._vista in ("chiusa", "nascosta"):
            self._mostra_grande("hai parlato (modalità compagna)")
        if trovato and not resto:
            self.log(f"Frase riconosciuta: {testo}")
            registro.frase(testo, "solo il nome di Eva (né comando né chat)")
            self.eventi.put(("frase", testo))
            self._vox_da_solo()                 # "Eva" a conversazione attiva: non è un errore
            return
        self._esegui_comando(resto if trovato and resto else testo, da_vox=True, originale=testo)

    def _reg_tipo(self, tipo):
        """Per eva_log.txt: come è stata trattata la frase in corso."""
        if getattr(self, "_reg", None) is not None:
            self._reg["tipo"] = tipo

    def _reg_groq(self, testo):
        """Per eva_log.txt: una risposta ricevuta da Groq per la frase in corso."""
        if getattr(self, "_reg", None) is not None and testo:
            self._reg["groq"].append(str(testo))

    def _esegui_comando(self, testo, da_vox, originale):
        """Esegue la frase e scrive in eva_log.txt: frase, come è stata trattata, risposta di Groq."""
        self._reg = {"tipo": "comando (eseguito in locale)", "groq": []}
        try:
            return self._esegui_comando_interno(testo, da_vox, originale)
        finally:
            reg, self._reg = self._reg, None
            registro.frase(originale, reg["tipo"], " || ".join(reg["groq"]))

    def _esegui_comando_interno(self, testo, da_vox, originale):
        self._t_cmd = time.perf_counter()
        self.log(f"Frase riconosciuta: {originale}")
        self.eventi.put(("frase", testo))
        varianti = self.imp["WAKE_VARIANTI"]

        # MEMORIA della conversazione (memoria.py): ogni frase è uno scambio; "e domani?", "ripeti", "dimentica tutto"...
        if da_vox or self.conversazione:
            memoria.nuovo_turno(originale)
            try:
                if self._mem_gestisci(originale):
                    return
            except Exception:
                self.log.errore("Errore nella memoria della conversazione")

        # AIUTO a voce (aiuto.py): "cosa sai fare?" -> riepilogo breve dei gruppi di comandi, poi i dettagli di un gruppo
        if da_vox or self.conversazione:
            try:
                if self._aiuto_gestisci(originale):
                    return
            except Exception:
                self.log.errore("Errore nell'aiuto")
                self._aiuto_stato = None

        # PROMEMORIA a voce (promemoria.py): "ricordami di... alle...", "che promemoria ho?", "cancella il promemoria"
        if da_vox or self.conversazione:
            try:
                if self._pr_gestisci(originale):
                    return
            except Exception:
                self.log.errore("Errore nei promemoria")

        # UTILITÀ (utilita.py): timer, cronometro, sveglia, appunti, calcoli, batteria, musica, luminosità, Wi-Fi, Bluetooth
        if da_vox or self.conversazione:
            try:
                if self._ut_gestisci(originale):
                    return
            except Exception:
                self.log.errore("Errore nelle utilità")
                self._ut_stato = None
                self._pr_stato = None

        # LIBRERIA COMANDI PC: finestre, programmi, cartelle, testo, browser, suono, scorrimento, sistema (comandi_pc.py).
        # Le azioni pericolose (spegni, riavvia, elimina, svuota cestino...) chiedono prima conferma a voce.
        if da_vox or self.conversazione:
            try:
                if self._pc_gestisci(originale):
                    return
            except Exception:
                self.log.errore("Errore nella libreria dei comandi del PC")
                self._pc_attesa = None

        # LINGUA: "Eva, parla francese" / "speak English" / "habla español" / "parle italien"
        if da_vox or self.conversazione:
            nuova = lingue.riconosci_cambio(originale)
            if nuova is not None:
                self._cambia_lingua(nuova)
                return

        # COMANDI in un'altra lingua ("open Chrome", "ouvre Chrome", "abre Chrome", "close Chrome"...): li traduco
        # nel comando interno di Eva (apri Chrome...). Una frase che non è un comando resta com'è (chiacchiera).
        if lingue.corrente != "it" and (da_vox or self.conversazione):
            tradotto = lingue.verso_italiano(testo)
            if tradotto:
                self.log(f"Comando tradotto ({lingue.corrente}): {testo!r} -> {tradotto!r}")
                testo = originale = tradotto

        # -2) POSIZIONE DELLA SFERA: "mettiti da parte", "mettiti al centro", "nasconditi", "sposta in alto a sinistra"
        if da_vox or self.conversazione:
            try:
                cv = cervello.comando_vista(originale, self.imp.get("VARIANTI_NOME", varianti))
            except Exception:
                self.log.errore("Errore nel comando della sfera")
                cv = None
            if cv is not None:
                self._comando_vista(cv, originale)
                return

        # -1) COPIA / SPOSTA / TAGLIA / INCOLLA (sempre con conferma) e le risposte "sì" / "annulla" / "stop".
        #     Vengono PRIMA della chiusura: "stop" mentre copia ferma la copia, non chiude Eva.
        #     Con F9 normale (dettatura, senza il nome) non si avviano operazioni nuove.
        try:
            ro = operazioni.prova(testo, True, consentito=(da_vox or self.conversazione))
        except Exception:
            self.log.errore("Errore nelle operazioni sui file")
            ro = None
        if ro is not None:
            self.log(f"Frase corretta: {ro.frase_corretta} (operazione sui file)")
            self._concludi(ro)
            return

        # 0) CHIUSURA ("chiudi", "arrivederci", "basta"...) e "spegni Eva": si controllano per prime.
        #    Con F9 normale (dettatura) no: un testo dettato come "ok grazie" non deve chiudere Eva.
        if da_vox or self.conversazione:
            try:
                ch = cervello.analizza_chiusura(originale, self.imp.get("VARIANTI_NOME", varianti),
                                                self.imp.get("FRASI_CHIUSURA"))
            except Exception:
                self.log.errore("Errore nel controllo della chiusura")
                ch = cervello.Chiusura("no", "", "errore")
            if ch.tipo in ("esci_tutto", "chiudi") and operazioni.in_corso():
                self.log(f"Non chiudo: sto ancora copiando ('{originale}')")
                detta = lingue.t("copia_in_corso", nome=cervello.nome_utente())
                self.log(f"Risposta detta: {detta}")
                self._parla(detta)
                self._inizia_attesa()
                return
            if ch.tipo == "esci_tutto":
                self.log(f"Chiusura totale: frase '{originale}' riconosciuta come spegnimento ({ch.motivo})")
                self.log("Frase corretta: (spegni Eva)")
                self._esci_del_tutto()
                return
            if ch.tipo == "chiudi":
                self.log(f"Chiusura: frase '{originale}' riconosciuta come chiusura ({ch.motivo})")
                self.log(f"Frase corretta: {ch.frase} (chiusura)")
                saluto = cervello.saluto_chiusura(originale)
                self.log("Azione eseguita: saluto e chiudo la finestra")
                self.log(f"Risposta detta: {saluto}" + ("" if self.voce.attiva else " (voce spenta)"))
                self._finisci_conversazione("comando di chiusura", saluto)
                return
            if ch.tipo == "programma":
                # "chiudi il browser" / "chiudi la cartella": chiudo solo quella finestra, mai Eva
                try:
                    rc = chiudi_finestra(originale, nomi=self.imp.get("WAKE_VARIANTI", varianti))
                except Exception:
                    self.log.errore("Errore nel chiudere la finestra")
                    rc = None
                if rc is not None:
                    self.log(f"Frase corretta: {rc.frase_corretta}")
                    self._concludi(rc)
                    return
                self.log(f"Non è chiusura: '{originale}' ({ch.motivo})")
                self.log("Frase corretta: (chiudere un programma)")
                detta = cervello.testo_fisso("chiudi_programmi", "Chiudere i programmi non lo faccio ancora, {nome}.")
                self.log("Azione eseguita: nessuna (non chiudo i programmi)")
                self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
                self._parla(detta)
                self._fine_turno(1.5)
                return
            self.log(f"Non è chiusura: '{originale}'")

        # 0b) METEO: lo cerca il codice (Open-Meteo, senza chiave); Groq lo dice solo a voce
        if da_vox or self.conversazione:
            try:
                if self._meteo(originale):
                    return
            except Exception:
                self.log.errore("Errore nel meteo")
                self._attesa_citta = None

        # 1) saluti e chiacchiere (risposte.json) e comandi della voce (zitta / parla)
        try:
            risposta = cervello.rispondi(testo, varianti)
        except Exception:
            self.log.errore("Errore nel cervello (rispondi)")
            risposta = None
        if risposta is not None:
            self._reg_tipo("chiacchiera con risposta pronta (" + risposta.nome_intento + ")")
            self.log("Frase corretta: (chiacchiera: " + risposta.nome_intento + ")")
            self._gestisci_risposta(risposta)
            return

        # 2) comandi: apri, volume, cerca, bozze di mail...
        try:
            r = interpreta(testo)
        except Exception:
            self.log.errore("Errore nell'eseguire il comando")
            self.eventi.put(("nota", "Errore: vedi eva.log"))
            self._parla(cervello.frase_errore("errore"))
            self._fine_turno(2.5)
            return
        if (self._compagna and da_vox and r.tipo in ("non_trovato", "incerto") and self.imp.get("GROQ_AZIONI", True)
                and ia_azioni.attiva()):
            # compagna: il codice locale non è sicuro, decide Groq (con la memoria della conversazione)
            registro.non_riconosciuto(originale, f"{r.tipo} in locale: {(r.messaggio.splitlines() or [''])[0]} -> chiedo a Groq")
            r = Risultato("testo", r.frase_corretta, "")
        self.log("Frase corretta: " + (r.frase_corretta if r.tipo != "testo"
                                       else "(nessuna: non è un comando)"))
        if r.tipo == "testo":
            if not da_vox:                      # con F9 il testo resta testo (dettatura), senza voce
                self._reg_tipo("testo dettato (non è un comando)")
                self.log("Azione eseguita: nessun comando, scritto il testo")
                self._fine_turno(1.4)
                return
            # RIPIEGO con Groq: il codice locale non ha capito. Chiedo a Groq un'azione in JSON e la eseguo con le
            # funzioni di sempre (apri, chiudi, copia, incolla...). Solo se non è una chiacchiera: in quel caso si
            # prosegue sotto come prima. Senza internet / senza risposta: lo dico a voce e non mi blocco.
            if self.imp.get("GROQ_AZIONI", True) and ia_azioni.attiva():
                self.log(f"Comando non capito in locale: chiedo a Groq ('{originale}')")
                _tg = time.perf_counter()
                stato, azione = ia_azioni.chiedi_azione(testo)
                self.log(f"TEMPO ordine: Groq ha tradotto la frase in {(time.perf_counter() - _tg) * 1000:.0f} ms ({stato})")
                self._reg_groq(f"[traduzione in azione] {azione}" if stato == "ok" else f"[nessuna risposta: {stato}]")
                if stato in ("offline", "errore"):
                    self._reg_tipo("comando non capito in locale, chiesto a Groq (Groq non disponibile)")
                    registro.non_riconosciuto(originale, f"Groq non disponibile ({stato})")
                    self.log(f"Groq non disponibile ({stato})")
                    detta = lingue.t("groq_offline" if stato == "offline" else "groq_errore", nome=cervello.nome_utente())
                    self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
                    self._parla(detta)
                    self._fine_turno(1.5)
                    return
                if stato == "ok":
                    self.log(f"Groq propone: {azione}")
                    if azione["azione"] != "nessuna":
                        self._reg_tipo("comando non capito in locale, tradotto da Groq")
                    if azione["azione"] == "comando_pc":
                        cmd = comandi_pc.per_id(azione["nome"])
                        if cmd is not None and not cmd.pericoloso:       # solo azioni sicure
                            self.log(f"Libreria PC: azione proposta da Groq: {cmd.id}")
                            self._pc_esegui(cmd)
                            return
                    elif azione["azione"] != "nessuna":
                        try:
                            rg = ia_azioni.esegui_azione(azione, self.imp.get("WAKE_VARIANTI", varianti))
                        except Exception:
                            self.log.errore("Errore nell'eseguire l'azione di Groq")
                            rg = None
                        if rg is not None:
                            self.log(f"Frase corretta: {rg.frase_corretta} (azione Groq: {azione['azione']})")
                            self._concludi(rg)
                            return
                        registro.non_riconosciuto(originale, f"l'azione proposta da Groq ({azione['azione']}) non è eseguibile")
                        self.log("L'azione di Groq non è eseguibile: passo alla conversazione")
            # non è un comando e non è una chiacchiera nota: risposta di Groq IN STREAMING, detta frase per frase
            if self.imp.get("GROQ_STREAMING", True):
                try:
                    if self._chat_streaming(testo):
                        return
                except Exception:
                    self.log.errore("Errore nella risposta in streaming")
            # ripiego: punto unico per le risposte libere (frasi pronte). Vedi vox_cervello.rispondi_libero
            try:
                libera = cervello.rispondi_libero(testo, varianti)
            except Exception:
                self.log.errore("Errore in rispondi_libero")
                self._fine_turno(0.5)
                return
            self._reg_tipo("conversazione libera con risposta pronta (Groq senza risposta)")
            self.log("Frase corretta: (conversazione libera: " + libera.nome_intento + ")")
            self._gestisci_risposta(libera)
            return
        self._concludi(r)

    def _meteo(self, originale):
        """Gestisce "che tempo fa (a Roma)". Restituisce True se la frase era per il meteo (già risposto)."""
        import vox_meteo
        giorno = 0
        if self._attesa_citta is not None:           # avevo chiesto la città: questa frase è la risposta
            giorno = self._attesa_citta
            self._attesa_citta = None
            parole = [p for p in re.sub(r"[^\w\s']", " ", originale).replace("'", " ").split()
                      if p.lower() not in ("eva", "vox", "a", "di", "per", "in", "a")]
            citta = " ".join(w.capitalize() for w in parole[:3]) or None
            if not citta:
                return False
            salva = True
        else:
            m = vox_meteo.riconosci(originale)
            if m is None:
                return False
            giorno, citta, salva = m["giorno"], m["citta"], False
        if not citta:
            citta = vox_meteo.citta_salvata()
            if not citta:                            # prima volta: chiedo la città una volta sola
                self.log("Meteo: nessuna città predefinita, la chiedo")
                detta = lingue.t("meteo_chiedi_citta", nome=cervello.nome_utente())
                self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
                self._attesa_citta = giorno
                self._parla(detta)
                self._inizia_attesa()
                return True
        self.log(f"Meteo: cerco le previsioni per {citta} ({'domani' if giorno else 'oggi'}) su Open-Meteo")
        stato, dati = vox_meteo.cerca(citta, giorno)
        if stato == "ok":
            if salva or not vox_meteo.citta_salvata():
                vox_meteo.salva_citta(dati["citta"])
                self.log(f"Meteo: città predefinita salvata: {dati['citta']}")
            self.log(f"Meteo: dati {dati}")
            detta = vox_meteo.frase_per_voce(dati, cervello.frase_da_groq)
            memoria.completa("meteo", {"citta": dati["citta"], "giorno": giorno})   # per "e domani?", "e a Roma?"
        elif stato == "offline":
            detta = lingue.t("meteo_offline", nome=cervello.nome_utente())
        elif stato == "non_trovata":
            detta = lingue.t("meteo_non_trovata", citta=citta)
        else:
            detta = lingue.t("meteo_errore")
        self.log(f"Meteo: esito {stato}")
        self.log("Frase corretta: (meteo)")
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(detta)
        self._fine_turno(0.6 if self._parla_davvero() else 2.0)
        return True

    # --- aiuto a voce: "cosa sai fare?" (aiuto.py) -----------------------------------------------
    def _aiuto_dire(self, testo, attesa=True):
        """Dice (e mostra sulla sfera) un testo dell'aiuto; con attesa=True resta in ascolto della risposta."""
        self.log(f"Risposta detta: {testo}" + ("" if self.voce.attiva else " (voce spenta)"))
        t0 = time.perf_counter()
        self._parla(testo)                                    # (mette lo stesso testo anche sotto la sfera)
        if self._parla_davvero():
            self.log(f"TEMPO aiuto: {len(testo.split())} parole, parlato in {time.perf_counter() - t0:.1f} s")
        if attesa:
            self._aiuto_stato = {"scade": time.time() + 40}
            self._inizia_attesa()
            self.t_attesa = time.time() + 25                  # tempo per rispondere
        else:
            self._fine_turno(1.0)
        return True

    def _aiuto_gestisci(self, frase):
        """"cosa sai fare?", "aiuto", "quali comandi hai?": breve riepilogo dei gruppi di comandi (non l'elenco completo),
        poi "vuoi sentire i dettagli di un gruppo?": se si dice il nome di un gruppo, Eva legge qualche esempio."""
        if (self._pr_stato is not None or self._pc_attesa is not None or operazioni.in_attesa()
                or operazioni.in_corso() or vox_file.scelta_in_attesa()):
            return False                                      # sta rispondendo a un'altra domanda di Eva
        st, self._aiuto_stato = self._aiuto_stato, None
        if st is not None and time.time() <= st["scade"]:     # ho appena chiesto "vuoi sentire i dettagli di un gruppo?"
            si, no = comandi_pc.e_si(frase), comandi_pc.e_no(frase)
            if no and not si:
                return self._aiuto_dire(aiuto.tx("va_bene"), attesa=False)
            breve = len(frase.split()) <= 4 and comandi_pc.trova(frase) is None     # un comando vero ("alza il volume") non è un gruppo
            gruppo = aiuto.riconosci_gruppo(frase) if breve else None
            if gruppo is not None:
                testo = aiuto.dettagli(gruppo)
                if testo:
                    self.log(f"Aiuto: dettagli del gruppo «{gruppo}»")
                    return self._aiuto_dire(testo)
            if si:
                return self._aiuto_dire(aiuto.nomi_gruppi())
            # altra frase: non era una risposta all'aiuto, prosegue come sempre
        if not aiuto.riconosci(frase):
            return False
        self.log("Aiuto: riepilogo dei gruppi di comandi (generato dalla libreria dei comandi)")
        return self._aiuto_dire(aiuto.riepilogo())

    # --- memoria della conversazione (memoria.py) ------------------------------------------------
    def _mem_gestisci(self, frase):
        """"e domani?", "e a Roma?", "ripeti", "dillo più piano", "dimmi di più", "dimentica tutto": frasi che si riferiscono
        alla precedente. Restituisce True se le ha gestite. Mai indovina: senza contesto chiede di cosa si parla."""
        if (self._pr_stato is not None or self._pc_attesa is not None or operazioni.in_attesa()
                or operazioni.in_corso() or vox_file.scelta_in_attesa()):
            return False                                      # sta rispondendo a una domanda di Eva: non tocco
        r = memoria.riconosci(frase)
        if r is None:
            return False
        tipo, dati = r
        self.log(f"Memoria: «{frase}» -> {tipo}")
        if tipo not in ("ripeti", "piano"):
            self._aiuto_stato = None                          # altra cosa: la domanda "vuoi sentire i dettagli?" decade
        if tipo == "dimentica":
            memoria.svuota()
            cervello._cronologia.clear()
            self.log("Memoria: conversazione svuotata (comando vocale)")
            return self._pr_dire(memoria.tx("dimenticato"))
        ult = memoria.ultimo()
        if tipo in ("ripeti", "piano"):
            memoria.annulla_turno()                           # "ripeti" non diventa lui l'ultima risposta
            if ult is None or not ult.risposta:
                return self._pr_dire(memoria.tx("niente_da_ripetere"))
            self.log(f"Memoria: ripeto l'ultima risposta{' più piano' if tipo == 'piano' else ''}")
            self.log(f"Risposta detta: {ult.risposta}" + ("" if self.voce.attiva else " (voce spenta)"))
            self._parla(ult.risposta, velocita=-4 if tipo == "piano" else None)
            self._fine_turno(1.0)
            return True
        if tipo in ("e_giorno", "e_luogo") and ult is not None and ult.tipo == "meteo":
            citta = dati["luogo"] or ult.ctx.get("citta")
            giorno = dati["giorno"] if dati["giorno"] is not None else ult.ctx.get("giorno", 0)
            if giorno == 2:
                memoria.annulla_turno()
                return self._pr_dire(memoria.tx("solo_oggi_domani"))
            if citta:
                self.log(f"Memoria: seguito del meteo -> {citta}, {'domani' if giorno == 1 else 'oggi'} (locale, senza Groq)")
                return self._meteo(memoria.frase_meteo(citta, giorno))
        if ult is not None and ult.tipo in ("chat", "meteo"):
            self.log("Memoria: seguito della conversazione -> Groq con gli ultimi scambi")
            return self._chat_streaming(frase)                # Groq riceve anche le ultime domande e risposte
        memoria.annulla_turno()
        self.log("Memoria: frase di riferimento senza contesto: chiedo di cosa si parla")
        return self._pr_dire(memoria.tx("chiedi_argomento"), attesa=True)

    # --- promemoria a voce (promemoria.py) ------------------------------------------------------
    def _pr_dire(self, testo, attesa=False):
        """Dice una frase del promemoria; con attesa=True resta in ascolto della risposta (una domanda)."""
        self.log(f"Risposta detta: {testo}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(testo)
        if attesa:
            self._inizia_attesa()
            self.t_attesa = time.time() + 25              # tempo per rispondere
        else:
            self._fine_turno(1.0)
        return True

    def _pr_gestisci(self, frase):
        """Promemoria: crea ("ricordami..."), elenca ("che promemoria ho?"), cancella ("cancella il promemoria") e
        risponde alle domande di Eva. Restituisce True se la frase era per i promemoria."""
        st = self._pr_stato
        if st is not None:
            if time.time() > st["scade"]:
                self.log("Promemoria: la domanda è scaduta, non faccio niente")
                self._pr_stato = None
            else:
                return self._pr_risposta(st, frase)
        tipo = promemoria.riconosci(frase)
        if tipo is None:
            return False
        if operazioni.in_attesa() or operazioni.in_corso() or vox_file.scelta_in_attesa() or self._pc_attesa is not None:
            return False
        k, lg = tipo
        self.log(f"Promemoria: «{frase}» -> {k} ({lg})")
        if k == "crea":
            a = promemoria.analizza(frase, lg)
            return self._pr_nuovo(a)
        if k == "elenca":
            return self._pr_elenca()
        return self._pr_cancella(frase, lg)

    def _ut_gestisci(self, frase):
        """Utilità (utilita.py): timer, cronometro, sveglia, appunti, calcoli, batteria, musica, luminosità, Wi-Fi,
        Bluetooth. Solo in italiano. Restituisce True se la frase era per le utilità."""
        if lingue.corrente != "it":
            return False
        if (operazioni.in_attesa() or operazioni.in_corso() or vox_file.scelta_in_attesa() or self._pc_attesa is not None
                or self._pr_stato is not None):
            return False
        st, self._ut_stato = self._ut_stato, None
        if st is not None and time.time() > st.get("scade", 0):
            self.log("Utilità: la domanda è scaduta, non faccio niente")
            st = None
        r = utilita.interpreta(frase, st)
        if r is None:
            return False
        self.log(f"Utilità: «{frase}» -> {r.tipo}")
        if r.analisi is not None:                          # una sveglia: la completa il flusso dei promemoria
            return self._pr_nuovo(r.analisi)
        if r.stato:
            self._ut_stato = {**r.stato, "scade": time.time() + 30}
        self.log("Azione eseguita: " + r.tipo)
        return self._pr_dire(r.testo, attesa=bool(r.stato))

    def _pr_nuovo(self, a):
        """Ho cosa e quando? Salvo e ripeto a voce. Altrimenti CHIEDO (non indovino)."""
        adesso = dt.datetime.now()
        nome = cervello.nome_utente()
        if not a.cosa.strip():
            self._pr_stato = {"fase": "cosa", "a": a, "scade": time.time() + 40}
            return self._pr_dire(promemoria.tx("chiedi_cosa"), attesa=True)
        esito, val = promemoria.risolvi(a, adesso)
        if esito == "ok":
            return self._pr_salva(a, val, adesso)
        if esito == "chiedi_ampm":
            h, m = val
            self._pr_stato = {"fase": "ampm", "a": a, "ora": val, "scade": time.time() + 40, "tentativi": 0}
            return self._pr_dire(promemoria.tx("chiedi_ampm", ora=str(h) if m == 0 else f"{h}:{m:02d}"), attesa=True)
        if esito == "chiedi_giorno":
            h, m = val
            self._pr_stato = {"fase": "giorno", "a": a, "ora": val, "scade": time.time() + 40, "tentativi": 0}
            return self._pr_dire(promemoria.tx("chiedi_giorno", ora=f"{h}:{m:02d}"), attesa=True)
        self._pr_stato = {"fase": "quando", "a": a, "scade": time.time() + 40, "tentativi": 0}
        sveglia = getattr(a, "tipo", None) == "sveglia"
        return self._pr_dire("A che ora vuoi la sveglia?" if sveglia else promemoria.tx("chiedi_quando"), attesa=True)

    def _pr_salva(self, a, quando, adesso):
        tipo = getattr(a, "tipo", None)
        p = promemoria.aggiungi(a.cosa.strip(), quando, a.lingua, tipo=tipo)
        self.log(f"Promemoria: SALVATO «{p['cosa']}» per {p['quando']} (id {p['id']})" + (f" [{tipo}]" if tipo else ""))
        if tipo == "sveglia":
            detta = utilita.conferma_sveglia(quando, adesso, a.rel)
        else:
            detta = promemoria.tx("conferma", cosa=promemoria.cosa_parlata(p["cosa"], a.lingua),
                                  quando=promemoria.quando_parlato(quando, adesso, a.rel))
        self.log("Azione eseguita: " + ("sveglia impostata" if tipo == "sveglia" else "promemoria salvato"))
        self._pr_stato = None
        return self._pr_dire(detta)

    def _pr_risposta(self, st, frase):
        """Risposta a una domanda di Eva sui promemoria."""
        lg = lingue.corrente
        fase = st["fase"]
        adesso = dt.datetime.now()
        si, no = comandi_pc.e_si(frase), comandi_pc.e_no(frase)
        self._pr_stato = None
        if fase in ("cosa", "quando", "ampm", "giorno") and no and not si:
            self.log("Promemoria: annullato dalla persona")
            return self._pr_dire(promemoria.tx("annullato"))
        if fase == "cosa":
            b = promemoria.analizza(frase, lg)
            a = st["a"]
            a.cosa = b.cosa or frase.strip()
            for campo in ("rel", "ora", "hint", "giorno"):
                if getattr(a, campo) is None and getattr(b, campo) is not None:
                    setattr(a, campo, getattr(b, campo))
            return self._pr_nuovo(a)
        if fase == "quando":
            a = st["a"]
            b = promemoria.analizza(frase, lg, solo_quando=True)
            if b.rel is None and b.ora is None:
                if st["tentativi"] >= 1:
                    self.log("Promemoria: non ho capito il quando, rinuncio")
                    return self._pr_dire(promemoria.tx("non_capito_quando"))
                self._pr_stato = {**st, "tentativi": st["tentativi"] + 1, "scade": time.time() + 40}
                return self._pr_dire(promemoria.tx("chiedi_quando"), attesa=True)
            a.rel, a.ora = b.rel, b.ora
            a.hint = b.hint or a.hint
            a.giorno = b.giorno or a.giorno
            return self._pr_nuovo(a)
        if fase == "ampm":
            r = promemoria.riconosci_ampm(frase, lg)
            if r is None:
                if st["tentativi"] >= 1:
                    return self._pr_dire(promemoria.tx("annullato"))
                self._pr_stato = {**st, "tentativi": st["tentativi"] + 1, "scade": time.time() + 40}
                h, m = st["ora"]
                return self._pr_dire(promemoria.tx("chiedi_ampm", ora=str(h) if m == 0 else f"{h}:{m:02d}"), attesa=True)
            return self._pr_salva(st["a"], promemoria.con_ampm(st["ora"], r, adesso), adesso)
        if fase == "giorno":
            if si:
                h, m = st["ora"]
                q = dt.datetime.combine(adesso.date() + dt.timedelta(days=1), dt.time(h, m))
                return self._pr_salva(st["a"], q, adesso)
            a = st["a"]
            a.ora = a.rel = None
            a.hint = None
            self._pr_stato = {"fase": "quando", "a": a, "scade": time.time() + 40, "tentativi": 0}
            return self._pr_dire(promemoria.tx("chiedi_quando"), attesa=True)
        if fase == "cancella_quale":
            lista = st["lista"]
            scelta = self._pr_scegli(frase, lista, lg)
            if scelta == "tutti":
                self._pr_stato = {"fase": "cancella_tutti", "scade": time.time() + 30}
                return self._pr_dire(promemoria.tx("cancella_tutti", n=len(lista)), attesa=True)
            if scelta is None:
                return self._pr_dire(promemoria.tx("non_capito"))
            return self._pr_chiedi_cancella(scelta)
        if fase == "cancella_conferma":
            if si and not no:
                promemoria.rimuovi(st["p"]["id"])
                self.log(f"Promemoria: CANCELLATO «{st['p']['cosa']}» (confermato a voce)")
                return self._pr_dire(promemoria.tx("cancellato"))
            self.log("Promemoria: cancellazione NON confermata")
            return self._pr_dire(promemoria.tx("mantenuto"))
        if fase == "cancella_tutti":
            if si and not no:
                promemoria.rimuovi_tutti()
                self.log("Promemoria: CANCELLATI TUTTI (confermato a voce)")
                return self._pr_dire(promemoria.tx("cancellati"))
            return self._pr_dire(promemoria.tx("mantenuto"))
        return False

    def _pr_voce(self, p, adesso=None):
        return promemoria.quando_parlato(promemoria.quando_di(p), adesso or dt.datetime.now())

    def _pr_elenca(self):
        lista = promemoria.elenco()
        if not lista:
            return self._pr_dire(promemoria.tx("nessuno"))
        adesso = dt.datetime.now()
        parti = [promemoria.tx("elenco_uno") if len(lista) == 1 else promemoria.tx("elenco_intro", n=len(lista))]
        for i, p in enumerate(lista[:5], 1):
            parti.append(promemoria.tx("elenco_voce", i=i, cosa=p["cosa"], quando=self._pr_voce(p, adesso)))
        self.log(f"Promemoria: letti {min(5, len(lista))} di {len(lista)}")
        return self._pr_dire(" ".join(parti))

    def _pr_scegli(self, frase, lista, lg):
        """Quale promemoria intende la persona? "tutti", un numero, "il primo", o una parola del promemoria."""
        f = promemoria.piega(frase)
        if re.search(r"\b(?:" + promemoria.TUTTI[lg] + r")\b", f):
            return "tutti"
        for parola, n in promemoria.ORDINALI[lg].items():
            if re.search(r"\b" + parola + r"\b", f):
                return lista[n - 1] if 0 < n <= len(lista) else (lista[-1] if n == -1 else None)
        m = re.search(r"\b(" + promemoria._NUM_RE[lg] + r")\b", f)
        if m:
            n = promemoria.valore(m.group(1), lg)
            if n and 0 < n <= len(lista):
                return lista[n - 1]
        trovati = [p for p in lista if any(len(w) > 3 and w in promemoria.piega(p["cosa"]).split() for w in f.split())]
        return trovati[0] if len(trovati) == 1 else None

    def _pr_chiedi_cancella(self, p):
        self._pr_stato = {"fase": "cancella_conferma", "p": p, "scade": time.time() + 30}
        return self._pr_dire(promemoria.tx("cancella_conferma", cosa=p["cosa"], quando=self._pr_voce(p)), attesa=True)

    def _pr_cancella(self, frase, lg):
        lista = promemoria.elenco()
        if not lista:
            return self._pr_dire(promemoria.tx("nessuno"))
        scelta = self._pr_scegli(frase, lista, lg) if len(lista) > 1 else lista[0]
        if scelta == "tutti":
            self._pr_stato = {"fase": "cancella_tutti", "scade": time.time() + 30}
            return self._pr_dire(promemoria.tx("cancella_tutti", n=len(lista)), attesa=True)
        if scelta is not None:
            return self._pr_chiedi_cancella(scelta)
        adesso = dt.datetime.now()
        elenco = " ".join(promemoria.tx("elenco_voce", i=i, cosa=p["cosa"], quando=self._pr_voce(p, adesso))
                          for i, p in enumerate(lista[:5], 1))
        self._pr_stato = {"fase": "cancella_quale", "lista": lista[:5], "scade": time.time() + 40}
        return self._pr_dire(promemoria.tx("cancella_quale", elenco=elenco), attesa=True)

    def _pr_tick(self):
        """Ogni secondo: c'è un promemoria scaduto? (anche uno scaduto mentre Eva era spenta). Lo dice appena può:
        non interrompe una dettatura (F9), una frase di Eva o una domanda in corso."""
        ora = time.time()
        if ora < self._pr_prossimo:
            return
        self._pr_prossimo = ora + 1.0
        if self.f9_giu or self.modo == "f9" or self.voce.parlando():
            return
        if (self._pr_stato is not None or self._ut_stato is not None or self._pc_attesa is not None
                or operazioni.in_attesa() or operazioni.in_corso()):
            return
        if self.modo != "riposo" and not self.conversazione:
            return
        p = promemoria.prossimo_scaduto()
        if p is not None:
            self._pr_annuncia(p)

    def _pr_annuncia(self, p):
        """All'ora giusta: lo dice a voce ("<nome>, ti ricordo di ...") e lo mostra sulla sfera."""
        adesso = dt.datetime.now()
        quando = promemoria.quando_di(p)
        ritardo = (adesso - quando).total_seconds()
        lg = p.get("lingua", lingue.corrente)
        nome = cervello.nome_utente()
        cosa = promemoria.cosa_parlata(p["cosa"], lg)
        # timer e sveglia (utilita.py) hanno la loro frase; i promemoria normali quella di sempre
        detta = utilita.annuncio(p, nome, ritardo, promemoria.quando_parlato(quando, adesso), cervello.frase_ora(adesso))
        if detta is not None:
            pass
        elif ritardo > 120:                                # Eva era spenta (o occupata) all'ora del promemoria
            detta = promemoria.tx("annuncio_ritardo", lg=lg, nome=nome, cosa=cosa,
                                  quando=promemoria.quando_parlato(quando, adesso))
        else:
            detta = promemoria.tx("annuncio", lg=lg, nome=nome, cosa=cosa)
        self.log(f"Promemoria: SCADUTO «{p['cosa']}» (era per {p['quando']}, ritardo {ritardo:.0f} s)")
        promemoria.rimuovi(p["id"])                        # prima di parlare: non può ripetersi se qualcosa va storto
        self._mostra_grande("promemoria")                  # la sfera compare e mostra il promemoria
        self.eventi.put(("frase", "PROMEMORIA"))
        self.eventi.put(("fase", "ESEGUO"))
        self.log("Azione eseguita: promemoria annunciato")
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        cambiata = lg != lingue.corrente and self._parla_davvero()
        if cambiata:                                       # creato in un'altra lingua: uso la voce di quella lingua
            pk = lingue.PACCHETTI[lg]
            self.voce.imposta_lingua(lingue.nome_voce(self.imp, lg), pk["cultura"], pk["lcid"])
        with memoria.fuori_turno():
            self._parla(detta)                             # (mette la frase anche sotto la sfera)
        if cambiata:                                       # e poi rimetto la voce della lingua corrente
            pk = lingue.PACCHETTI[lingue.corrente]
            self.voce.imposta_lingua(lingue.nome_voce(self.imp), pk["cultura"], pk["lcid"])
        self._fine_turno(6.0)

    def _pc_gestisci(self, frase):
        """Libreria dei comandi del PC. Restituisce True se la frase è stata gestita qui."""
        att, self._pc_attesa = self._pc_attesa, None
        if att is not None:                                   # c'era una domanda di conferma in sospeso
            cmd = comandi_pc.per_id(att["id"])
            if time.time() > att["scade"]:
                self.log(f"Libreria PC: conferma scaduta per «{att['id']}»: non faccio niente")
                detta = comandi_pc._l(comandi_pc.SCADUTA)
                self.log(f"Risposta detta: {detta}")
                self._parla(detta)
                self._fine_turno(1.0)
                return True
            if comandi_pc.e_si(frase):
                self.log(f"Libreria PC: CONFERMATO a voce («{frase}»): eseguo {att['id']}")
                return self._pc_esegui(cmd)
            self.log(f"Libreria PC: NON confermato («{frase}»): «{att['id']}» annullato")
            if comandi_pc.e_no(frase):
                detta = comandi_pc._l(comandi_pc.ANNULLATO)
                self.log(f"Risposta detta: {detta}")
                self._parla(detta)
                self._fine_turno(1.0)
                return True
            # altra frase: la domanda decade e la frase prosegue normalmente
        if operazioni.in_attesa() or operazioni.in_corso() or vox_file.scelta_in_attesa():
            return False                                      # sta rispondendo a copia/sposta o a una scelta: non tocco
        cmd = comandi_pc.trova(frase)
        if cmd is None:
            return False
        if cmd.id in ("testo_incolla", "testo_taglia") and operazioni._appunti.get("percorso"):
            return False                                      # "incolla"/"taglia" dopo "copia <file>": è per i file di Eva
        self.log(f"Libreria PC: «{frase}» -> {cmd.id} [{cmd.categoria}]")
        if cmd.pericoloso:
            self._pc_attesa = {"id": cmd.id, "scade": time.time() + comandi_pc.CONFERMA_SECONDI}
            domanda = comandi_pc.domanda_conferma(cmd)
            self.log(f"Libreria PC: azione PERICOLOSA, chiedo conferma a voce: {cmd.id}")
            self.log("Azione eseguita: nessuna (in attesa di conferma a voce)")
            self.log(f"Risposta detta: {domanda}" + ("" if self.voce.attiva else " (voce spenta)"))
            self._parla(domanda)
            self._inizia_attesa()
            self.t_attesa = time.time() + comandi_pc.CONFERMA_SECONDI
            return True
        return self._pc_esegui(cmd)

    def _pc_esegui(self, cmd):
        """Esegue un comando della libreria, lo registra nel log e dice l'esito."""
        t0 = time.perf_counter()
        ok, detta = comandi_pc.esegui(cmd)
        self.log(f"Libreria PC: {cmd.id} [{cmd.categoria}] -> {'RIUSCITO' if ok else 'NON RIUSCITO'} "
                 f"({(time.perf_counter() - t0) * 1000:.0f} ms)")
        self.log("Frase corretta: (comando PC: " + cmd.id + ")")
        self.log("Azione eseguita: " + ("comando PC " + cmd.id if ok else "nessuna (comando PC non riuscito)"))
        if ok and self.imp.get("SPARISCI_DOPO_COMANDO", True):
            self._sparisci("dopo comando PC")                 # via subito: si deve vedere quello che ho fatto
        elif ok and cmd.categoria in ("Programmi", "Cartelle") and cmd.azione.split(":")[0] in (
                "avvia", "url", "browser", "cartella") and self.conversazione and self._vista != "nascosta":
            self._fissa_grande = False                        # ho aperto qualcosa: la sfera si rimpicciolisce
            self._riduci_tra = None
            self._vista_imposta("ridotta", "dopo comando PC")
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(detta)
        self._fine_turno(0.6 if self._parla_davvero() else 1.3)
        return True

    def _cambia_lingua(self, nuova):
        """Cambia lingua: trascrizione, voce, risposte, comandi, Groq e frasi di chiusura insieme; la salva nelle
        impostazioni e lo dice a voce con la voce della nuova lingua."""
        vecchia = lingue.corrente
        self.log(f"LINGUA: cambio da {vecchia} a {nuova} (comando vocale)")
        lingue.imposta(nuova)                                   # salva anche in impostazioni_eva.json
        lingue.applica_a_impostazioni(self.imp)                 # voce, parole suggerite, frasi di chiusura
        cervello.configura_chiusura(self.imp.get("FRASI_CHIUSURA"))
        n_intenti, nome = cervello.carica(self.log)             # risposte nella nuova lingua
        cervello._dati["utente"] = self.imp.get("NOME_UTENTE", nome)
        cervello._cronologia.clear()                            # la chat riparte nella nuova lingua
        memoria.svuota()                                        # e la memoria della conversazione con lei
        self.suggerimento = ", ".join(self.imp["PAROLE_SUGGERITE"]) + "."
        self.eco = normalizza(self.suggerimento)
        self._nomi_prompt_ok = False
        trovata = self.voce.imposta_lingua(self.imp["VOCE_NOME"], self.imp["VOCE_CULTURA"], self.imp.get("VOCE_LCID", ()))
        self.log(f"LINGUA: ora {lingue.pacchetto()['nome']} | voce: {trovata or 'NON TROVATA'} | trascrizione "
                 f"{lingue.codice_whisper()} | risposte: {n_intenti} gruppi")
        detta = lingue.t("lingua_cambiata")
        if not trovata:
            detta += " (voice not installed)"
        self.log("Azione eseguita: lingua cambiata")
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(detta)
        self._fine_turno(1.5)

    def _chat_streaming(self, testo):
        """Chiacchiera con Groq in streaming: la voce comincia dalla PRIMA frase, mentre le altre stanno ancora
        arrivando. Restituisce True se ha risposto; False se Groq non ha dato niente (si usa il ripiego)."""
        if not os.environ.get("GROQ_API_KEY"):
            return False
        t0 = time.perf_counter()
        parlo = self._parla_davvero()
        dette, t_prima, t_testo = [], None, None
        pezzi = cervello.chat_stream(testo)
        try:
            for frase in cervello.frasi_da_stream(pezzi):
                if self.fermo.is_set() or self.f9_giu:        # F9 premuto: smetto, ora parli tu
                    break
                if t_prima is None:
                    t_prima = time.perf_counter() - t0
                    self.log(f"TEMPO chat: prima frase pronta dopo {t_prima:.2f} s dall'invio a Groq")
                dette.append(frase)
                self.eventi.put(("nota", " ".join(dette)))
                if parlo:
                    if len(dette) == 1:
                        self.eventi.put(("fase", "ESEGUO"))
                    self.voce.parla(frase)                    # in coda: parte subito, le altre frasi seguono
        finally:
            pezzi.close()
        t_testo = time.perf_counter() - t0
        if not dette:
            self.log(f"TEMPO chat: Groq non ha risposto ({t_testo:.2f} s): uso la risposta di ripiego "
                     f"(fermo={self.fermo.is_set()}, F9 premuto={self.f9_giu})")
            registro.scrivi(f"CHAT SENZA RISPOSTA da Groq ({t_testo:.2f} s): {testo[:120]}")
            return False
        completa = " ".join(dette)
        self._reg_tipo("chat inviata a Groq (non è un comando)")
        self._reg_groq(completa)
        memoria.completa("chat", risposta=completa)           # lo scambio entra in memoria (solo RAM)
        self.log("Frase corretta: (conversazione libera: modello, streaming)")
        self.log("Azione eseguita: risposta a voce" if parlo else "Azione eseguita: risposta scritta")
        self.log(f"Risposta detta: {completa}" + ("" if self.voce.attiva else " (voce spenta)"))
        self.log(f"TEMPO chat: testo completo dopo {t_testo:.2f} s ({len(completa.split())} parole, {len(dette)} frasi)")
        if parlo:
            t1 = time.perf_counter()
            limite = time.time() + 60
            while self.voce._occupata > 0 and time.time() < limite and not self.fermo.is_set():
                time.sleep(0.05)
            self.log(f"TEMPO chat: voce finita {time.perf_counter() - t0:.2f} s dopo l'invio "
                     f"(ha parlato per {time.perf_counter() - t1:.1f} s dopo la fine del testo)")
            self._svuota_audio()
        self._inizia_attesa()                                 # resto in ascolto: l'utente può rispondere
        self._programma_riduzione()
        return True

    def _concludi(self, r):
        """Dice e registra il risultato di un comando (apri, copia, sposta...) e decide cosa fare dopo."""
        if getattr(self, "_t_cmd", None) is not None:
            self.log(f"TEMPO ordine: azione pronta e eseguita dopo {(time.perf_counter() - self._t_cmd) * 1000:.0f} ms "
                     f"dalla frase riconosciuta")
        if r.tipo == "eseguito":
            self.log("Azione eseguita: " + r.messaggio)
        else:                                   # incerto, non trovato o in attesa di risposta
            self.log("Azione eseguita: nessuna (" + (r.messaggio.splitlines() or [""])[0] + ")")
            if r.tipo in ("non_trovato", "incerto"):
                registro.non_riconosciuto(r.frase_corretta, f"{r.tipo}: {(r.messaggio.splitlines() or [''])[0]}")
        detta, riuscita = cervello.risposta_a_risultato(r)
        if self.imp.get("SPARISCI_DOPO_COMANDO", True):
            if self._cambia_qualcosa(r) and not operazioni.in_corso():
                self._sparisci("dopo il comando")             # via subito: si deve vedere quello che ho aperto
        elif self.conversazione and self._apre_qualcosa(r) and self._vista != "nascosta":
            # ho aperto qualcosa: la sfera si rimpicciolisce subito e va nell'angolo (non copre quello che si è aperto)
            self._fissa_grande = False
            self._riduci_tra = None
            self._vista_imposta("ridotta", "dopo apertura")
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(detta)
        if r.tipo == "scelta":
            # ho chiesto "quale intendi?" o "dimmi sì per confermare": resto in ascolto della risposta
            self._inizia_attesa()
            self.t_attesa = time.time() + (operazioni.secondi_attesa() if operazioni.in_attesa() else 6)
        elif operazioni.in_corso():
            self._inizia_attesa()               # sta copiando: la finestra resta aperta (ESEGUO) e posso dire "annulla"
        else:
            self._fine_turno(0.6 if self._parla_davvero() else (1.3 if riuscita else 2.2))

    def _gestisci_risposta(self, risposta):
        """Dice la risposta di una chiacchiera e applica l'azione (chiudi / voce_off / voce_on)."""
        memoria.completa("chat")
        azione = risposta.azione
        if azione == "voce_on":
            if self.voce.disponibile:
                self.voce.attiva = True          # prima la riattivo, poi parla
            self.log("Azione eseguita: voce riattivata")
        elif azione == "voce_off":
            self.log("Azione eseguita: voce spenta (dì «parla» per riattivarla)")
        elif azione == "chiudi":
            self.log("Azione eseguita: saluto e chiudo la finestra")
            self.log(f"Risposta detta: {risposta.testo}" + ("" if self.voce.attiva else " (voce spenta)"))
            self._finisci_conversazione("comando di chiusura", risposta.testo)
            return
        else:
            self.log("Azione eseguita: risposta a voce" if self.voce.attiva else "Azione eseguita: risposta scritta")
        self.log(f"Risposta detta: {risposta.testo}" + ("" if self.voce.attiva else " (voce spenta)"))
        self._parla(risposta.testo)              # con voce_off parla ancora quest'ultima volta
        if azione == "voce_off":
            self.voce.attiva = False
        if risposta.continua and azione is None:
            self._inizia_attesa()                # una domanda: aspetto la tua risposta
            self._programma_riduzione()
        else:
            self._fine_turno(0.6 if self._parla_davvero() else 1.8)

    # --- conversazione continua ----------------------------------------------
    def _inizia_conversazione(self, motivo):
        """Entra in conversazione continua (se attiva nelle impostazioni e non già in corso)."""
        if not self.imp.get("CONVERSAZIONE_CONTINUA", True) or self.conversazione:
            return
        self.conversazione = True
        self.log(f"Conversazione INIZIATA: {motivo}. Resto in ascolto finché non dici "
                 f"«chiudi», «basta», «arrivederci»... (timeout: {self.imp.get('TIMEOUT_CONVERSAZIONE', 300)} s)")

    def _finisci_conversazione(self, motivo, saluto=""):
        """Saluta (a voce) e chiude la finestra con la dissolvenza."""
        if self.conversazione:
            self.log(f"Conversazione FINITA: {motivo}")
        elif saluto:
            self.log(f"Chiusura: {motivo}")
        # la chiusura non deve MAI bloccarsi: se Eva sta ancora parlando la interrompo, il saluto
        # dura al massimo 3 secondi, e subito dopo parte la dissolvenza della finestra
        self.voce.silenzia()
        if saluto:
            if motivo.startswith("timeout"):
                self.log(f"Risposta detta: {saluto}" + ("" if self.voce.attiva else " (voce spenta)"))
            self._parla(saluto, max_secondi=1.9)     # saluto breve: al massimo ~2 secondi, poi si chiude
        self.conversazione = False
        self._chiudi(0.3 if self._parla_davvero() else 1.8)
        self.log("Eva torna a riposo: aspetto la parola di attivazione.")

    def _esci_del_tutto(self):
        """"Spegni Eva" / "esci del tutto": saluta e ferma completamente il programma."""
        detta = cervello.testo_fisso("spegni", "Spengo Eva. A presto, {nome}.")
        self.log("Azione eseguita: spengo Eva (il programma si ferma del tutto)")
        self.log(f"Risposta detta: {detta}" + ("" if self.voce.attiva else " (voce spenta)"))
        if self.conversazione:
            self.log("Conversazione FINITA: spegnimento completo")
        self.conversazione = False
        self.voce.silenzia()
        self._parla(detta, max_secondi=5)
        self.log("Eva si spegne.")
        self.eventi.put(("esci",))               # la finestra chiude l'applicazione
        self.fermo.set()                         # e il ciclo di ascolto si ferma

    def _fine_turno(self, ritardo):
        """Dopo un comando o una risposta: con la conversazione attiva torno IN ASCOLTO e la
        finestra resta aperta; altrimenti la finestra si chiude come prima."""
        if self.conversazione:
            self._inizia_attesa()
            self._programma_riduzione()
        else:
            self._chiudi(ritardo)

    # --- voce ---------------------------------------------------------------
    def _parla_davvero(self):
        return self.voce.attiva and self.voce.disponibile

    def _parla(self, testo, max_secondi=60, velocita=None):
        """Mostra il testo e lo dice a voce (se la voce è accesa). Aspetta che finisca (al massimo
        max_secondi: poi la voce viene interrotta). velocita: da -10 (lenta) a 10 solo per questa frase."""
        if not testo:
            return
        memoria.aggiungi_detta(testo)                         # entra nella risposta dello scambio in corso
        self.eventi.put(("nota", testo))
        if self._parla_davvero():
            self.eventi.put(("fase", "ESEGUO"))
            self.voce.parla(testo, aspetta=True, max_secondi=max_secondi, velocita=velocita)
            self._svuota_audio()

    def _parla_da_thread(self, testo):
        """Per il thread delle operazioni sui file: mostra la frase e la dice (senza aspettare)."""
        self.eventi.put(("nota", testo))
        if self._parla_davvero():
            self.voce.parla(testo)

    def _svuota_audio(self):
        self.vad.reset()
        while True:                             # butto l'audio accumulato mentre parlavo
            try:
                self.coda.get_nowait()
            except queue.Empty:
                break

    def _chiudi(self, ritardo):
        self.modo = "attesa" if self._compagna else "riposo"   # compagna: la finestra sparisce ma Eva ascolta
        if self._compagna:
            self.conversazione = True
            self.t_attesa = time.time()
        self.f9_blocchi = []
        self.attiva_richiesta = False
        self._vista, self._riduci_tra, self._fissa_grande = "chiusa", None, False
        self._schermo_intero = False
        self.eventi.put(("chiudi", float(ritardo)))
        self._svuota_audio()

    # --- trascrizione -------------------------------------------------------
    def _esito_veloce(self, testo):
        """Dopo una trascrizione VELOCE: restituisce None se basta, altrimenti il motivo per cui conviene
        rileggere con il modello preciso (che costa 3-4 volte di più)."""
        conf = self._ultima_conf
        minimo = float(self.imp.get("CONFIDENZA_VELOCE_MIN", -0.6))
        if conf is None or conf < minimo:
            return f"sicurezza bassa ({'-' if conf is None else f'{conf:.2f}'} < {minimo})"
        if operazioni.in_attesa() or vox_file.scelta_in_attesa() or self._pc_attesa is not None or self._pr_stato is not None \
                or self._aiuto_stato is not None or self._ut_stato is not None:
            return "aspetto una conferma o una scelta: parola breve e importante"
        if comandi_pc.trova(testo):
            return None                                      # frase intera della libreria dei comandi: la veloce basta
        trovato, resto = self._nome(testo)
        base = resto if trovato else testo
        base = lingue.verso_italiano(base) or base               # comando in un'altra lingua: lo valuto in italiano
        parole = normalizza(base).split()
        if not parole:
            return None
        if cervello.analizza_chiusura(testo, self.imp.get("VARIANTI_NOME", ()), self.imp.get("FRASI_CHIUSURA")).tipo                 in ("chiudi", "esci_tutto", "programma"):
            return None                                   # "chiudi", "basta", "spegni Eva", "chiudi il browser"
        verbo, n = vox_file._verbo(parole)
        if verbo == "apri":
            oggetto = [p for p in parole[n:] if p not in ARTICOLI]
            if len(oggetto) > MAX_PAROLE_OGGETTO or vox_file.oggetto_noto(" ".join(oggetto)):
                return None       # nome noto, oppure frase lunga (è una chiacchiera: rileggerla non serve)
            return "ordine «apri» con un nome che non conosco"
        ordini = cervello.INIZI_COMANDO | cervello.VERBI_PROGRAMMA | {"copia", "sposta", "taglia", "incolla"}
        if verbo is not None or parole[0] in ordini:
            return f"ordine «{parole[0]}»: i nomi vanno letti bene"
        return None                                       # chiacchiera: la veloce basta

    def _trascrivi_adattiva(self, audio):
        """Prima la trascrizione veloce; quella precisa solo se la veloce non basta (poco sicura, oppure un ordine
        di cui non riconosco il nome). Se il modello preciso non è ancora caricato, o è lo stesso, ce n'è una sola."""
        if not (self.imp.get("TRASCRIZIONE_VELOCE_PRIMA", True) and self.modello_wake is not None
                and self.modello is not self.modello_wake):
            return self._trascrivi(audio, self.modello)
        t0 = time.perf_counter()
        testo = self._trascrivi(audio, self.modello_wake, veloce=True)
        if not testo:
            self.log.debug("TRASCRIZIONE: la veloce non ha trovato parlato: non rileggo con il preciso")
            return ""
        motivo = self._esito_veloce(testo)
        if motivo is None:
            self.log(f"TRASCRIZIONE: veloce sufficiente (sicurezza {self._ultima_conf:.2f}, "
                     f"{time.perf_counter() - t0:.1f} s): non uso il preciso")
            return testo
        self.log(f"TRASCRIZIONE: la veloce non basta ({motivo}): rileggo con il preciso")
        return self._trascrivi(audio, self.modello)

    def _trascrivi(self, audio, modello, veloce=False):
        _t_tr = time.perf_counter()
        audio = audio.astype(np.float32)
        picco = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if 1e-4 < picco < 0.3:                  # microfono basso: alzo un po' il volume
            audio = np.clip(audio * min(8.0, 0.6 / picco), -1.0, 1.0)
        if not self._nomi_prompt_ok and self.imp.get("SUGGERISCI_NOMI_CARTELLE", True):
            nomi = vox_file.nomi_per_prompt(12)          # i nomi delle cartelle del Desktop aiutano Whisper
            if nomi:
                self._nomi_prompt_ok = True
                self.suggerimento = ", ".join(self.imp["PAROLE_SUGGERITE"]) + ". " + ", ".join(nomi) + "."
                self.eco = normalizza(self.suggerimento)
        # Whisper, davanti a silenzio o rumore, "inventa" frasi (es. "Sottotitoli creati dalla
        # comunità Amara.org"). Difese: filtro delle pause (VAD), niente memoria della frase
        # precedente, temperatura 0, e poi scarto per probabilità di non-parlato, punteggio
        # basso e frasi false note.
        segmenti, _ = modello.transcribe(
            audio, language=lingue.codice_whisper(), initial_prompt=self.suggerimento,
            beam_size=1 if veloce else 5, temperature=0.0,
            vad_filter=True,
            vad_parameters={"threshold": self.imp.get("VAD_SOGLIA", 0.5),
                            "min_speech_duration_ms": 250,
                            "min_silence_duration_ms": self.imp.get("VAD_SILENZIO_MS", 500),
                            "speech_pad_ms": 200},
            condition_on_previous_text=False)
        max_non_parlato = self.imp.get("NO_SPEECH_MAX", 0.6)
        min_punteggio = self.imp.get("LOGPROB_MIN", -1.0)
        tenuti = []
        punteggi = []
        for s in segmenti:
            if s.no_speech_prob > max_non_parlato:
                self.log(f"Scartato: probabile silenzio o rumore (no_speech_prob "
                         f"{s.no_speech_prob:.2f} > {max_non_parlato}): {s.text.strip()!r}")
            elif s.avg_logprob < min_punteggio:
                self.log(f"Scartato: trascrizione poco sicura (avg_logprob "
                         f"{s.avg_logprob:.2f} < {min_punteggio}): {s.text.strip()!r}")
            elif s.text.strip():
                tenuti.append(s.text.strip())
                punteggi.append(s.avg_logprob)
        self._ultima_conf = min(punteggi) if punteggi else None      # quanto è sicura la trascrizione (0 = sicurissima)
        testo = " ".join(tenuti).strip()
        self.log.debug(f"TEMPO trascrizione: {time.perf_counter() - _t_tr:.2f} s per {len(audio) / FREQ:.1f} s di audio "
                       f"({'veloce' if veloce else 'preciso'})")
        if not testo:
            self.log.debug("[trascrizione] vuota")        # nessun rumore sul terminale
            return ""
        pulito = normalizza(testo)
        for falsa in self.false_note:
            if falsa in pulito:
                self.log(f"Scartato: frase falsa nota di Whisper ({falsa!r}): {testo!r}")
                return ""
        # Se il modello ripete il suggerimento invece di ascoltare, lo scarto
        if len(pulito.split()) >= 5 and pulito in self.eco:
            self.log("Scartato: il modello ha ripetuto il suggerimento")
            return ""
        return testo
