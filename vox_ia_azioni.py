"""Ripiego con Groq: quando i comandi fissi di Eva non capiscono la frase, si chiede a Groq
di trasformarla in UN'AZIONE in formato JSON, poi la si esegue con le funzioni che esistono già.

  - i comandi semplici restano locali (veloci, funzionano senza internet): qui si arriva solo
    se il codice locale ha risposto "non è un comando";
  - Groq NON esegue niente e non scrive codice: sceglie solo un'azione da un elenco fisso
    (ELENCO_AZIONI) e un nome. Qualsiasi altra cosa viene scartata;
  - ogni azione passa dalle stesse funzioni di prima (interpreta, chiudi_finestra, cerca_su_web,
    operazioni.prova): le regole di sicurezza sono quelle di sempre (mai cancellare, copia e sposta
    sempre con conferma a voce, mai chiudere Eva);
  - se internet manca o Groq non risponde, chiedi_azione() lo dice (stato "offline" / "errore")
    e Eva lo comunica a voce: niente blocchi (timeout breve, nessun nuovo tentativo).

Usa lo stesso pacchetto "groq" e la stessa variabile GROQ_API_KEY della chat (vox_cervello.py).
"""
import json
import logging
import os
import re

import vox_lingue as lingue

MODELLO = "qwen/qwen3.8-27b"        # modello veloce: per tradurre un ordine in JSON basta (mediana circa 0,2 s)
MODELLO_RISERVA = "openai/gpt-oss-120b"   # se il veloce dà errore (limite del piano gratuito...) si riprova con questo
IMP = {"NOME_UTENTE": "", "GROQ_AZIONI": True, "GROQ_AZIONI_TIMEOUT": 8, "COMPAGNA": False}      # sovrascritto da configura() con dettatura.py

ISTRUZIONI_COMPAGNA = (
    "\nModalità compagna: @NOME@ sta chiacchierando con Eva e a volte dà un ordine in mezzo al discorso. Decidi tu se "
    "la frase è un ordine per il PC (scegli l'azione) o una chiacchierata (\"nessuna\"). Usa il CONTESTO qui sotto per "
    "capire i riferimenti: «aprila», «aprilo», «mettilo», «il secondo» dopo una ricerca su YouTube = "
    '"risultato_youtube" con "nome" = il numero del risultato ("1", "2"...). '
    'Se non sei sicuro, scegli "chiarisci" e scrivi in "domanda" una domanda naturale e gentile che comincia con '
    '«Scusa @NOME@, forse intendevi ...?» (es. «Scusa @NOME@, forse intendevi aprire il primo video?»).'
)

ELENCO_AZIONI = ("apri", "apri_cartella", "chiudi", "cerca", "volume_su", "volume_giu", "muto",
                 "togli_muto", "copia", "sposta", "incolla", "chiarisci", "comando_pc", "risultato_youtube", "nessuna")
BERSAGLI_CHIUDI = {"browser": "il browser", "cartella": "la cartella", "finestra": "finestra"}

ISTRUZIONI = (
    "Sei il traduttore di comandi di Eva, l'assistente vocale del PC Windows di @NOME@. Eva può chiacchierare "
    "liberamente, ma quando @NOME@ dà un ORDINE (aprire o chiudere un programma, una cartella, un file...) tu non "
    "commenti, non rifiuti e non dici mai che non puoi: riconosci l'ordine e lo restituisci in formato fisso, "
    "che il codice esegue subito. Ricevi UNA frase in italiano "
    "(da un riconoscimento vocale, può contenere errori) e rispondi SOLO con un oggetto JSON, senza altro testo.\n"
    'Formato: {"azione": "...", "nome": "...", "destinazione": "...", "motore": "...", "domanda": "..."}\n'
    "Azioni possibili (scegline una sola):\n"
    '- "apri": apre un programma o un sito; "nome" = nome del programma o sito (es. "Blocco note", "YouTube").\n'
    '- "apri_cartella": apre una cartella; "nome" = nome della cartella (es. "Documenti", "Scaricati").\n'
    '- "chiudi": chiude una finestra; "nome" = "browser" (anche Chrome, Edge, Firefox), "cartella" oppure "finestra".\n'
    '- "chiarisci": sembra un ordine ma la frase è poco chiara o incompleta; "domanda" = UNA frase brevissima in '
    'italiano che chiede conferma, per esempio "Intendevi aprire Chrome?". Mai rifiuti.\n'
    '- "cerca": ricerca web; "nome" = cosa cercare; "motore" = "google" o "youtube" (predefinito google).\n'
    '- "volume_su", "volume_giu", "muto", "togli_muto": volume (nessun altro campo).\n'
    '- "copia" / "sposta": "nome" = file o cartella; "destinazione" = dove (può mancare).\n'
    '- "incolla": "destinazione" = dove incollare (può mancare).\n'
    '- "nessuna": la frase NON è un comando per il computer (chiacchiera, domanda, dettatura, frase senza senso).\n'
    "Regole: non inventare mai nomi che non sono nella frase; se è chiaramente un ordine ma non sei sicuro "
    "scegli \"chiarisci\"; se non è un ordine scegli \"nessuna\"; "
    "non esistono azioni per cancellare, installare, spegnere o inviare messaggi: in quei casi \"nessuna\". "
    "Ometti i campi che non servono."
)

_client = None


def _extra_pc():
    """Elenco dei comandi della libreria (comandi_pc.py) che Groq può proporre: solo quelli sicuri."""
    try:
        import comandi_pc
        return ('\n- "comando_pc": un comando di controllo del PC già pronto; "nome" = UNO di questi id: '
                + comandi_pc.elenco_per_groq() + ". Usalo quando la frase chiede chiaramente quell'azione "
                '(es. "rendi la finestra a tutto schermo" -> finestra_ingrandisci). Mai per spegnere, riavviare o eliminare.')
    except Exception:
        return ""


def _nome():
    return IMP.get("NOME_UTENTE") or "amico"


def contesto():
    """La memoria della conversazione per Groq: ultimi scambi e ultima ricerca su YouTube (vedi memoria.py)."""
    righe = []
    try:
        import memoria
        for m in memoria.messaggi_groq():
            righe.append((_nome() + ": " if m["role"] == "user" else "Eva: ") + m["content"])
    except Exception:
        pass
    try:
        import azioni
        ricerca = azioni._ricerca_youtube_recente()
        if ricerca:
            righe.append(f"[Eva ha appena cercato su YouTube: «{ricerca}»; i risultati sono numerati 1, 2, 3...]")
    except Exception:
        pass
    return "\nCONTESTO (ultimi scambi):\n" + "\n".join(righe) if righe else ""


def configura(imp):
    """Legge GROQ_AZIONI e GROQ_AZIONI_TIMEOUT da dettatura.py."""
    for k in IMP:
        if k in imp:
            IMP[k] = imp[k]


def attiva():
    return bool(IMP.get("GROQ_AZIONI")) and bool(os.environ.get("GROQ_API_KEY"))


def _crea_client():
    global _client
    if _client is None:
        from groq import Groq
        _client = Groq(api_key=os.environ.get("GROQ_API_KEY"),
                       timeout=float(IMP.get("GROQ_AZIONI_TIMEOUT", 8)), max_retries=0)
    return _client


def _pulisci(valore, massimo=80):
    """Testo corto, su una riga, senza caratteri strani: finisce solo nelle frasi dei comandi locali."""
    if not isinstance(valore, str):
        return ""
    valore = re.sub(r"[^\w\s'.\-àèéìòùÀÈÉÌÒÙ]", " ", valore, flags=re.UNICODE)
    return re.sub(r"\s+", " ", valore).strip()[:massimo]


def leggi_json(testo):
    """Estrae l'oggetto JSON dalla risposta (anche se Groq lo mette tra ``` o aggiunge testo)."""
    if not testo:
        return None
    m = re.search(r"\{.*\}", testo, re.S)
    if not m:
        return None
    try:
        dati = json.loads(m.group(0))
    except ValueError:
        return None
    return dati if isinstance(dati, dict) else None


def valida(dati):
    """Dizionario pulito {azione, nome, destinazione, motore}, oppure None se l'azione non è nell'elenco
    o mancano i dati necessari."""
    if not dati:
        return None
    azione = _pulisci(str(dati.get("azione", "")), 30).lower().replace(" ", "_")
    if azione not in ELENCO_AZIONI:
        return None
    if azione == "comando_pc":          # solo comandi sicuri della libreria (comandi_pc.py); gli altri vengono scartati
        import comandi_pc
        nome_pc = _pulisci(dati.get("nome", ""), 40).lower().replace(" ", "_")
        if nome_pc not in comandi_pc.ids_sicuri():
            return None
        return {"azione": azione, "nome": nome_pc, "destinazione": "", "motore": "google", "domanda": ""}
    nome, dest = _pulisci(dati.get("nome", "")), _pulisci(dati.get("destinazione", ""))
    motore = "youtube" if "youtube" in str(dati.get("motore", "")).lower() else "google"
    if azione in ("apri", "apri_cartella", "cerca", "copia", "sposta") and not nome:
        return None
    if azione == "risultato_youtube":
        cifre = re.sub(r"\D", "", nome)
        if not cifre or not 1 <= int(cifre) <= 5:
            return None
        nome = cifre
    domanda = _pulisci(dati.get("domanda", ""), 120)
    if azione == "chiarisci" and not domanda:
        return None
    if azione == "chiudi" and nome.lower() not in BERSAGLI_CHIUDI:
        # "chiudi Chrome" / "chiudi Edge": il nome di un browser vale "browser"; altro: chiedo, senza rifiutare
        if re.search(r"chrome|edge|firefox|brave|opera|browser|google", nome.lower()):
            nome = "browser"
        else:
            return {"azione": "chiarisci", "nome": "", "destinazione": "", "motore": "google",
                    "domanda": lingue.t("domanda_chiudi")}
    return {"azione": azione, "nome": nome, "destinazione": dest, "motore": motore, "domanda": domanda}


def chiedi_azione(frase):
    """Chiede a Groq l'azione per `frase`. Restituisce (stato, azione):
      "ok"      + dizionario azione (già validato; può essere {"azione": "nessuna"});
      "spenta"  : GROQ_AZIONI è False o manca la chiave / il pacchetto (non serve dire niente);
      "offline" : internet assente o timeout;
      "errore"  : Groq ha risposto con un errore (limite, servizio) o con una risposta inutilizzabile.
    """
    if not attiva():
        return "spenta", None
    try:
        client = _crea_client()
    except Exception:
        return "spenta", None
    try:
        import groq
        errori_rete = (groq.APIConnectionError,)     # include APITimeoutError
    except Exception:
        errori_rete = ()
    testo = None
    elenco = (MODELLO, MODELLO_RISERVA)
    for modello in elenco:
        try:
            esito = client.chat.completions.create(
                model=modello, temperature=0, max_completion_tokens=300,
                **({"reasoning_effort": "low"} if "gpt-oss" in modello else {}),
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": ISTRUZIONI.replace("@NOME@", _nome()) + _extra_pc() + lingue.groq_ordini_extra()
                           + (ISTRUZIONI_COMPAGNA.replace("@NOME@", _nome()) + contesto() if IMP.get("COMPAGNA") else "")},
                          {"role": "user", "content": frase}])
            testo = (esito.choices[0].message.content or "").strip()
            break
        except errori_rete:
            return "offline", None
        except Exception:
            logging.getLogger("eva").exception(f"Groq ({modello}) non ha risposto all'ordine"
                                               + (f": riprovo con {MODELLO_RISERVA}" if modello != elenco[-1] else ""))
    if testo is None:
        return "errore", None
    azione = valida(leggi_json(testo))
    if azione is None:
        return "ok", {"azione": "nessuna", "nome": "", "destinazione": "", "motore": "google"}
    return "ok", azione


def esegui_azione(azione, nomi=()):
    """Esegue l'azione con le funzioni di azioni.py / vox_operazioni.py / vox_file.py.
    Restituisce un risultato (come interpreta()) oppure None se non è eseguibile / non è un comando."""
    import azioni
    import vox_operazioni
    a, nome, dest = azione["azione"], azione["nome"], azione["destinazione"]
    if a in ("nessuna", "comando_pc"):       # comando_pc lo esegue vox_core con la libreria dei comandi
        return None
    if a == "chiarisci":        # frase poco chiara: chiedo conferma con una frase breve (mai "non posso")
        d = azione.get("domanda") or "Non ho capito bene"
        d = d.rstrip(" .?!") + "?"
        if IMP.get("COMPAGNA"):         # domanda naturale ("Scusa <nome>, forse intendevi...?"), senza "Ripeti il comando"
            return azioni.RisultatoDetto("incerto", "(comando poco chiaro)", d, d)
        return azioni.RisultatoDetto("incerto", "(comando poco chiaro)", d, d + " " + lingue.t("ripeti_comando"))
    if a == "risultato_youtube":
        msg = azioni.falla_partire(n=int(nome))
        return azioni.Risultato("non_trovato" if msg.startswith("Non ho capito") else "eseguito",
                                f"apri il risultato {nome} di YouTube", msg)
    if a == "apri":
        return azioni.interpreta(f"apri {nome}")
    if a == "apri_cartella":
        return azioni.interpreta(f"apri la cartella {nome}")
    if a == "chiudi":
        return azioni.chiudi_finestra(f"chiudi {BERSAGLI_CHIUDI[nome.lower()]}", nomi=nomi)
    if a == "cerca":
        msg = azioni.cerca_su_web(nome, azione["motore"])
        return azioni.Risultato("eseguito", f"cerca {nome}", msg)
    if a == "volume_su":
        return azioni.Risultato("eseguito", "alza il volume", azioni.alza_volume())
    if a == "volume_giu":
        return azioni.Risultato("eseguito", "abbassa il volume", azioni.abbassa_volume())
    if a == "muto":
        return azioni.Risultato("eseguito", "muto", azioni.metti_muto())
    if a == "togli_muto":
        return azioni.Risultato("eseguito", "togli il muto", azioni.togli_muto())
    if a in ("copia", "sposta"):          # stessa strada del comando a voce: con conferma, mai sovrascrive
        frase = f"{a} {nome}" + (f" in {dest}" if dest else "")
        return vox_operazioni.prova(frase, True, consentito=True)
    if a == "incolla":
        return vox_operazioni.prova("incolla" + (f" in {dest}" if dest else ""), True, consentito=True)
    return None
