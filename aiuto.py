"""Aiuto a voce: "cosa sai fare?" (italiano, francese, spagnolo, inglese).

Eva risponde con un BREVE riepilogo dei gruppi di comandi (non l'elenco completo: sarebbe troppo lungo da ascoltare)
e chiede "vuoi sentire i dettagli di un gruppo?". Se la persona dice il nome di un gruppo, Eva legge 3-4 esempi di frasi.

  - Il riepilogo e gli esempi sono GENERATI dalla libreria dei comandi (comandi_pc.TABELLA): se aggiungi un comando
    lì, l'aiuto si aggiorna da solo. Una categoria nuova della tabella diventa un gruppo nuovo (con il suo nome).
  - I gruppi che non vivono nella tabella (promemoria, domande e conversazione, cambio lingua) prendono gli esempi
    da ESEMPI, che sta accanto a quelle funzioni (promemoria.py, memoria.py, vox_lingue.py).
  - I comandi PERICOLOSI (spegni, riavvia, sospendi, esci, elimina, svuota il cestino) non vengono mai elencati:
    si dice solo che chiedono sempre conferma a voce.

Questo modulo non parla e non apre niente: costruisce i testi. A dirli e a mostrarli sulla sfera pensa vox_core.py.
"""
import difflib
import re

import comandi_pc
import memoria
import promemoria
import utilita
import vox_lingue as lingue

LINGUE = ("it", "fr", "es", "en")
N_ESEMPI = 4
SPESSORE_MIN_FUZZY = 8

# ----------------------------------------------------------------------
# I gruppi. "cat" = categorie di comandi_pc.TABELLA; "esempi" = dove stanno gli esempi se non sono nella tabella.
# ----------------------------------------------------------------------
GRUPPI = [
    {"id": "finestre", "cat": ("Finestre", "Programmi", "Sistema")},
    {"id": "cartelle", "cat": ("Cartelle",)},
    {"id": "testo_browser", "cat": ("Testo", "Browser", "Scorrimento")},
    {"id": "volume", "cat": ("Schermo e suono",)},
    {"id": "promemoria", "esempi": lambda: promemoria.ESEMPI},
    {"id": "utilita", "esempi": lambda: utilita.ESEMPI},
    {"id": "conversazione", "esempi": lambda: memoria.ESEMPI},
    {"id": "lingua", "esempi": lambda: lingue.ESEMPI},
]
NOMI = {
    "finestre": {"it": "finestre e programmi", "fr": "fenêtres et programmes", "es": "ventanas y programas", "en": "windows and programs"},
    "cartelle": {"it": "cartelle", "fr": "dossiers", "es": "carpetas", "en": "folders"},
    "testo_browser": {"it": "testo e browser", "fr": "texte et navigateur", "es": "texto y navegador", "en": "text and browser"},
    "volume": {"it": "volume", "fr": "volume", "es": "volumen", "en": "volume"},
    "promemoria": {"it": "promemoria", "fr": "rappels", "es": "recordatorios", "en": "reminders"},
    "utilita": {"it": "timer, appunti e utilità del PC"},          # solo italiano

    "conversazione": {"it": "domande e conversazione", "fr": "questions et conversation", "es": "preguntas y conversación", "en": "questions and conversation"},
    "lingua": {"it": "cambio lingua", "fr": "changement de langue", "es": "cambio de idioma", "en": "changing language"},
}
# nomi per eventuali categorie NUOVE della tabella (se manca, si usa il nome della categoria)
NOMI_CATEGORIE = {}
# parole con cui la persona può nominare un gruppo (senza accenti)
CHIAVI = {
    "finestre": {"it": "finestre finestra programmi programma app applicazioni sistema", "fr": "fenetres fenetre programmes programme applications systeme",
                 "es": "ventanas ventana programas programa aplicaciones sistema", "en": "windows window programs program apps system"},
    "cartelle": {"it": "cartelle cartella file", "fr": "dossiers dossier fichiers", "es": "carpetas carpeta archivos", "en": "folders folder files"},
    "testo_browser": {"it": "testo browser scorrimento scorrere internet schede scheda", "fr": "texte navigateur defilement onglets onglet internet",
                      "es": "texto navegador desplazamiento pestanas pestana internet", "en": "text browser scrolling tabs tab internet"},
    "volume": {"it": "volume suono audio schermata", "fr": "volume son audio capture", "es": "volumen sonido audio captura", "en": "volume sound audio screenshot"},
    "promemoria": {"it": "promemoria ricordi", "fr": "rappels rappel", "es": "recordatorios recordatorio", "en": "reminders reminder"},
    "utilita": {"it": "utilita timer sveglia cronometro appunti note calcoli calcolatrice batteria musica luminosita wifi bluetooth"},
    "conversazione": {"it": "domande domanda conversazione meteo chiacchiere chiacchierare", "fr": "questions question conversation meteo discussion",
                      "es": "preguntas pregunta conversacion tiempo charla", "en": "questions question conversation weather chat"},
    "lingua": {"it": "lingua lingue idioma", "fr": "langue langues", "es": "idioma idiomas lengua", "en": "language languages"},
    "sicurezza": {"it": "spegnere spegni riavviare riavvia eliminare elimina cancellare pericolosi sicurezza", "fr": "eteindre redemarrer supprimer dangereux securite",
                  "es": "apagar reiniciar eliminar peligrosos seguridad", "en": "shut restart delete dangerous safety security"},
}
# accenti persi nelle frasi della tabella (che sono scritte senza accenti per il riconoscimento): per la VOCE li rimetto
ACCENTI = {
    "it": {"giu": "giù", "attivita": "attività", "pc": "PC"},
    "fr": {"arriere": "arrière", "debut": "début", "defiler": "défiler", "fenetre": "fenêtre", "parametres": "paramètres",
           "precedent": "précédent", "precedente": "précédente", "reduis": "réduis", "selectionne": "sélectionne",
           "telechargements": "téléchargements", "taches": "tâches", "ecran": "écran", "a": "à", "pc": "PC"},
    "es": {"atras": "atrás", "configuracion": "configuración", "desplazate": "desplázate", "pagina": "página", "pestana": "pestaña",
           "pc": "PC"},
    "en": {"pc": "PC"},
}

# ----------------------------------------------------------------------
# Frasi che attivano l'aiuto
# ----------------------------------------------------------------------
FRASI_AIUTO = {
    "it": "cosa sai fare|che cosa sai fare|cosa puoi fare|che cosa puoi fare|aiuto|aiutami|come puoi aiutarmi|quali comandi hai|"
          "quali sono i comandi|che comandi conosci|che comandi hai|a cosa servi|come ti uso|come posso usarti|elenco dei comandi|"
          "elenco comandi|dimmi cosa sai fare|cosa sai fare per me|cosa posso dirti",
    "fr": "que sais-tu faire|qu'est-ce que tu sais faire|que peux-tu faire|aide|aide-moi|comment peux-tu m'aider|quelles commandes as-tu|"
          "quelles commandes connais-tu|quelles sont les commandes|à quoi tu sers|à quoi sers-tu|comment t'utiliser|liste des commandes|"
          "que puis-je te dire",
    "es": "qué sabes hacer|qué puedes hacer|ayuda|ayúdame|cómo puedes ayudarme|qué comandos tienes|qué comandos conoces|"
          "cuáles son los comandos|para qué sirves|cómo te uso|lista de comandos|qué puedo decirte",
    "en": "what can you do|what do you do|help|help me|how can you help me|what commands do you have|what commands do you know|"
          "what are the commands|what are you for|how do i use you|list of commands|what can i say",
}
_FRASI = {lg: [f for f in (lingue.norm(x) for x in testo.split("|")) if f] for lg, testo in FRASI_AIUTO.items()}

TX = {
    "it": {
        "intro": "Posso aiutarti con {gruppi}.",
        "e": "e",
        "sicurezza": "Per spegnere, riavviare o eliminare ti chiedo sempre conferma a voce.",
        "domanda": "Vuoi sentire i dettagli di un gruppo?",
        "dettagli": "Nel gruppo {nome} puoi dire, per esempio: {esempi}.",
        "altro": "Vuoi un altro gruppo?",
        "quale": "Quale gruppo? {nomi}.",
        "va_bene": "Va bene.",
    },
    "fr": {
        "intro": "Je peux t'aider avec : {gruppi}.",
        "e": "et",
        "sicurezza": "Pour éteindre, redémarrer ou supprimer, je te demande toujours confirmation à voix haute.",
        "domanda": "Veux-tu entendre les détails d'un groupe ?",
        "dettagli": "Dans le groupe {nome}, tu peux dire, par exemple : {esempi}.",
        "altro": "Un autre groupe ?",
        "quale": "Quel groupe ? {nomi}.",
        "va_bene": "D'accord.",
    },
    "es": {
        "intro": "Puedo ayudarte con {gruppi}.",
        "e": "y",
        "sicurezza": "Para apagar, reiniciar o eliminar, siempre te pido confirmación por voz.",
        "domanda": "¿Quieres escuchar los detalles de un grupo?",
        "dettagli": "En el grupo {nome} puedes decir, por ejemplo: {esempi}.",
        "altro": "¿Otro grupo?",
        "quale": "¿Qué grupo? {nomi}.",
        "va_bene": "De acuerdo.",
    },
    "en": {
        "intro": "I can help you with {gruppi}.",
        "e": "and",
        "sicurezza": "To shut down, restart or delete, I always ask for your confirmation out loud.",
        "domanda": "Would you like to hear the details of a group?",
        "dettagli": "In the {nome} group you can say, for example: {esempi}.",
        "altro": "Another group?",
        "quale": "Which group? {nomi}.",
        "va_bene": "Alright.",
    },
}


def tx(chiave, **campi):
    return TX[lingue.corrente][chiave].format(**campi)


# ----------------------------------------------------------------------
# Costruire i gruppi dalla libreria
# ----------------------------------------------------------------------
def _comandi_sicuri(categorie, lg):
    return [c for c in comandi_pc.TABELLA if c.categoria in categorie and not c.pericoloso and c.raw.get(lg)]


def gruppi_attivi(lg=None):
    """I gruppi che esistono adesso (con almeno un comando o un esempio): quelli fissi + una categoria nuova della
    tabella = un gruppo nuovo. Restituisce [{"id", "nome", "categorie"|None, "esempi"|None}]."""
    lg = lg or lingue.corrente
    usate = set()
    out = []
    for g in GRUPPI:
        if "cat" in g:
            usate |= set(g["cat"])
            if _comandi_sicuri(g["cat"], lg):
                out.append({"id": g["id"], "nome": NOMI[g["id"]][lg], "cat": g["cat"], "esempi": None})
        else:
            es = g["esempi"]().get(lg)
            if es:
                out.append({"id": g["id"], "nome": NOMI[g["id"]][lg], "cat": None, "esempi": es})
    nuove = []
    for c in comandi_pc.TABELLA:                                  # categoria nuova, non ancora in nessun gruppo
        if c.categoria not in usate and not c.pericoloso and c.categoria not in nuove and c.raw.get(lg):
            nuove.append(c.categoria)
    for cat in nuove:
        out.append({"id": "cat:" + cat, "nome": NOMI_CATEGORIE.get(cat, {}).get(lg, cat.lower()), "cat": (cat,), "esempi": None})
    return out


def _unisci(voci, lg):
    if len(voci) <= 1:
        return "".join(voci)
    if len(voci) == 2:
        return f"{voci[0]} {TX[lg]['e']} {voci[1]}"
    return ", ".join(voci[:-1]) + f", {TX[lg]['e']} " + voci[-1]       # la virgola prima dell'ultimo: i nomi contengono già "e"


def pronuncia(frase, lg):
    """Rimette gli accenti che la tabella toglie (per la voce): "fenetre" -> "fenêtre"."""
    fix = ACCENTI.get(lg, {})
    return re.sub(r"[A-Za-zÀ-ÿ]+", lambda m: fix.get(m.group(0).lower(), m.group(0)), frase)


def esempi(gruppo, lg=None, n=N_ESEMPI):
    """Fino a n frasi di esempio del gruppo (da comandi_pc.TABELLA, distribuite sul gruppo, oppure da ESEMPI)."""
    lg = lg or lingue.corrente
    if gruppo["esempi"]:
        return [pronuncia(x, lg) for x in gruppo["esempi"][:n]]
    cmds = _comandi_sicuri(gruppo["cat"], lg)
    if not cmds:
        return []
    if len(cmds) >= n:
        scelti = [cmds[i * len(cmds) // n] for i in range(n)]       # n comandi distribuiti sul gruppo
        return [pronuncia(c.raw[lg][0], lg) for c in scelti]
    frasi = [pronuncia(c.raw[lg][0], lg) for c in cmds]             # gruppo piccolo: prima una frase per comando...
    k = 1
    while len(frasi) < n and any(len(c.raw[lg]) > k for c in cmds):  # ...poi le loro varianti, finché ne ho n
        for c in cmds:
            if len(c.raw[lg]) > k and len(frasi) < n:
                frasi.append(pronuncia(c.raw[lg][k], lg))
        k += 1
    return frasi


def ha_pericolosi():
    return any(c.pericoloso for c in comandi_pc.TABELLA)


def riepilogo(lg=None):
    """Il riepilogo breve (da dire e mostrare sulla sfera)."""
    lg = lg or lingue.corrente
    nomi = [g["nome"] for g in gruppi_attivi(lg)]
    testo = TX[lg]["intro"].format(gruppi=_unisci(nomi, lg))
    if ha_pericolosi():
        testo += " " + TX[lg]["sicurezza"]
    return testo + " " + TX[lg]["domanda"]


def nomi_gruppi(lg=None):
    lg = lg or lingue.corrente
    return TX[lg]["quale"].format(nomi=_unisci([g["nome"] for g in gruppi_attivi(lg)], lg))


def dettagli(gid, lg=None):
    """Esempi del gruppo (o, per "sicurezza", la frase sui comandi che chiedono conferma). None se non esiste."""
    lg = lg or lingue.corrente
    if gid == "sicurezza":
        return TX[lg]["sicurezza"] + " " + TX[lg]["altro"]
    for g in gruppi_attivi(lg):
        if g["id"] == gid:
            ex = esempi(g, lg)
            if ex:
                return TX[lg]["dettagli"].format(nome=g["nome"], esempi=_unisci([f"«{e}»" for e in ex], lg)) + " " + TX[lg]["altro"]
    return None


# ----------------------------------------------------------------------
# Riconoscere la richiesta e il gruppo
# ----------------------------------------------------------------------
def riconosci(frase):
    """True se la frase chiede "cosa sai fare?" / "aiuto" / "quali comandi hai?" (anche detta in modo un po' diverso)."""
    lg = lingue.corrente
    pulita = comandi_pc.ripulisci(frase)
    if not pulita:
        return False
    if pulita in _FRASI[lg]:
        return True
    if len(pulita) >= SPESSORE_MIN_FUZZY:
        return any(difflib.SequenceMatcher(None, pulita, f).ratio() >= 0.88 for f in _FRASI[lg] if len(f) >= SPESSORE_MIN_FUZZY)
    return False


def riconosci_gruppo(frase, lg=None):
    """Il gruppo nominato nella frase (id) oppure "sicurezza", oppure None."""
    lg = lg or lingue.corrente
    parole = set(lingue.norm(frase).split())
    migliore, voto = None, 0
    attivi = {g["id"] for g in gruppi_attivi(lg)}
    for gid, chiavi in CHIAVI.items():
        if gid != "sicurezza" and gid not in attivi:
            continue
        n = len(parole & set(chiavi[lg].split()))
        if n > voto:
            migliore, voto = gid, n
    if migliore is None:                                          # una categoria nuova nominata col suo nome
        for g in gruppi_attivi(lg):
            if g["id"].startswith("cat:") and set(lingue.norm(g["nome"]).split()) & parole:
                return g["id"]
    return migliore
