"""Multilingua di Eva: italiano (it), francese (fr), spagnolo (es), inglese (en).

Per ogni lingua cambiano INSIEME:
  - la trascrizione del microfono (lingua e parole suggerite a Whisper),
  - la voce di Windows che parla (Elsa / Hortense / Helena / Zira),
  - i comandi: "open / ouvre / abre ..." vengono tradotti nei comandi interni di Eva (apri, chiudi, cerca, volume...),
  - le risposte fisse (risposte.json per l'italiano, risposte_fr/es/en.json per le altre) e le frasi del codice,
  - le istruzioni per Groq (risponde nella lingua scelta),
  - il meteo (parole, città, frasi),
  - le frasi che chiudono Eva.

La lingua scelta si salva in impostazioni_eva.json (chiave "lingua") e si cambia a voce:
"Eva, parla francese", "Eva, speak English", "Eva, habla español", "Eva, parle italien"...

Questo modulo NON apre, non scrive e non cancella niente (a parte la chiave "lingua" nelle impostazioni).
Copia, sposta, taglia e incolla restano disponibili solo in italiano.
"""
import datetime
import difflib
import json
import logging
import re
import unicodedata
from pathlib import Path

import eva_percorsi as percorsi
FILE_IMPOSTAZIONI = percorsi.dati("impostazioni_eva.json")
_log = logging.getLogger("eva")

CODICI = ("it", "fr", "es", "en")
corrente = "it"


def norm(testo):
    """Minuscolo, senza accenti né punteggiatura (apostrofi e trattini diventano spazi)."""
    t = unicodedata.normalize("NFD", (testo or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"['’`\-]", " ", t)
    t = re.sub(r"[^a-z0-9% ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ----------------------------------------------------------------------
# Dati di ogni lingua
# ----------------------------------------------------------------------
PACCHETTI = {
    "it": {
        "nome": "italiano", "nome_in_lingua": "italiano", "whisper": "it", "voce": "Elsa", "cultura": "it",
        "lcid": ("410",), "file_risposte": "risposte.json",
        "parole_suggerite": None,           # per l'italiano vale PAROLE_SUGGERITE di dettatura.py
        "chiusura": [],                     # per l'italiano vale FRASI_CHIUSURA di dettatura.py
    },
    "fr": {
        "nome": "francese", "nome_in_lingua": "français", "whisper": "fr", "voce": "Hortense", "cultura": "fr",
        "lcid": ("40C", "80C", "C0C"), "file_risposte": "risposte_fr.json",
        "parole_suggerite": ["Eva", "ouvre", "ferme", "lance", "cherche", "Chrome", "YouTube", "Gmail", "Bloc-notes",
                             "Calculatrice", "Documents", "Téléchargements", "Bureau", "monte le volume",
                             "baisse le volume", "muet", "météo", "au revoir", "mets-toi de côté", "parle italien", "rappelle-moi", "rappel"],
        "chiusura": ["au revoir", "a bientot", "a plus tard", "a plus", "c est tout", "j ai fini", "bonne nuit",
                     "ferme", "ferme toi", "tu peux y aller", "repos", "adieu", "bonne journee"],
    },
    "es": {
        "nome": "spagnolo", "nome_in_lingua": "español", "whisper": "es", "voce": "Helena", "cultura": "es",
        "lcid": ("40A", "C0A", "80A"), "file_risposte": "risposte_es.json",
        "parole_suggerite": ["Eva", "abre", "cierra", "inicia", "busca", "Chrome", "YouTube", "Gmail", "Bloc de notas",
                             "Calculadora", "Documentos", "Descargas", "Escritorio", "sube el volumen",
                             "baja el volumen", "silencio", "tiempo", "adiós", "apártate", "habla italiano", "recuérdame", "recordatorio"],
        "chiusura": ["adios", "hasta luego", "hasta pronto", "eso es todo", "he terminado", "ya termine",
                     "buenas noches", "cierra", "cierrate", "puedes irte", "descansa", "nos vemos", "chao"],
    },
    "en": {
        "nome": "inglese", "nome_in_lingua": "English", "whisper": "en", "voce": "Zira", "cultura": "en",
        "lcid": ("409", "809"), "file_risposte": "risposte_en.json",
        "parole_suggerite": ["Eva", "open", "close", "launch", "search", "Chrome", "YouTube", "Gmail", "Notepad",
                             "Calculator", "Documents", "Downloads", "Desktop", "volume up", "volume down", "mute",
                             "weather", "goodbye", "move aside", "speak Italian", "remind me", "reminder"],
        "chiusura": ["goodbye", "bye", "bye bye", "see you", "see you later", "see you soon", "that is all",
                     "that s all", "i am done", "i m done", "good night", "you can go", "go away", "close"],
    },
}


def pacchetto(lingua=None):
    return PACCHETTI[lingua or corrente]


# ----------------------------------------------------------------------
# Frasi dette dal codice (quelle di risposte*.json sono nei file)
# ----------------------------------------------------------------------
TESTI = {
    "it": {
        "benvenuto": "Ciao {nome}, come stai? Di cosa hai bisogno oggi?",
        "caricamento": "Sto caricando...",
        "errore_ascolto": "{nome}, ho un problema e non riesco ad ascoltarti. Guarda il file eva punto log.",
        "copia_in_corso": "Sto ancora lavorando su una copia, {nome}. Dì annulla per fermarla.",
        "groq_offline": "Non riesco a collegarmi a internet, {nome}. Questo comando non lo capisco senza.",
        "groq_errore": "Il servizio di intelligenza artificiale non risponde, {nome}. Riprova tra poco.",
        "meteo_chiedi_citta": "Di quale città vuoi il meteo, {nome}? La ricorderò per la prossima volta.",
        "meteo_offline": "Non riesco a collegarmi a internet, {nome}: non posso darti il meteo adesso.",
        "meteo_non_trovata": "Non trovo la città {citta}. Riprova dicendo: che tempo fa a, e il nome della città.",
        "meteo_errore": "Il servizio del meteo non risponde. Riprova tra poco.",
        "lingua_cambiata": "D'accordo, ora parlo italiano.",
        "ripeti_comando": "Ripeti il comando.",
        "domanda_chiudi": "Vuoi che chiuda il browser, la cartella o la finestra in primo piano?",
        "chiudo_browser": "Chiudo il browser.", "chiudo_cartella": "Chiudo la cartella.",
        "chiudo_finestra": "Chiudo la finestra.", "non_riesco_chiudere": "Non riesco a chiuderla.",
        "nessuna_finestra": "Non c'è nessuna finestra da chiudere.",
    },
    "fr": {
        "benvenuto": "Bonjour {nome}, comment vas-tu ? De quoi as-tu besoin aujourd'hui ?",
        "caricamento": "Chargement...",
        "errore_ascolto": "{nome}, j'ai un problème et je n'arrive pas à t'écouter. Regarde le fichier eva point log.",
        "copia_in_corso": "Je travaille encore sur une copie, {nome}. Dis annule pour l'arrêter.",
        "groq_offline": "Je n'arrive pas à me connecter à internet, {nome}. Sans ça, je ne comprends pas cette commande.",
        "groq_errore": "Le service d'intelligence artificielle ne répond pas, {nome}. Réessaie dans un instant.",
        "meteo_chiedi_citta": "Pour quelle ville veux-tu la météo, {nome} ? Je m'en souviendrai pour la prochaine fois.",
        "meteo_offline": "Je n'arrive pas à me connecter à internet, {nome} : je ne peux pas te donner la météo maintenant.",
        "meteo_non_trovata": "Je ne trouve pas la ville {citta}. Réessaie en disant : quel temps fait-il à, et le nom de la ville.",
        "meteo_errore": "Le service météo ne répond pas. Réessaie dans un instant.",
        "lingua_cambiata": "D'accord, je parle français maintenant.",
        "ripeti_comando": "Répète la commande.",
        "domanda_chiudi": "Veux-tu que je ferme le navigateur, le dossier ou la fenêtre au premier plan ?",
        "chiudo_browser": "Je ferme le navigateur.", "chiudo_cartella": "Je ferme le dossier.",
        "chiudo_finestra": "Je ferme la fenêtre.", "non_riesco_chiudere": "Je n'arrive pas à la fermer.",
        "nessuna_finestra": "Il n'y a aucune fenêtre à fermer.",
    },
    "es": {
        "benvenuto": "Hola {nome}, ¿cómo estás? ¿Qué necesitas hoy?",
        "caricamento": "Cargando...",
        "errore_ascolto": "{nome}, tengo un problema y no consigo escucharte. Mira el archivo eva punto log.",
        "copia_in_corso": "Todavía estoy trabajando en una copia, {nome}. Di cancela para detenerla.",
        "groq_offline": "No consigo conectarme a internet, {nome}. Sin eso no entiendo este comando.",
        "groq_errore": "El servicio de inteligencia artificial no responde, {nome}. Inténtalo de nuevo en un momento.",
        "meteo_chiedi_citta": "¿De qué ciudad quieres el tiempo, {nome}? La recordaré para la próxima vez.",
        "meteo_offline": "No consigo conectarme a internet, {nome}: ahora no puedo darte el tiempo.",
        "meteo_non_trovata": "No encuentro la ciudad {citta}. Inténtalo diciendo: qué tiempo hace en, y el nombre de la ciudad.",
        "meteo_errore": "El servicio del tiempo no responde. Inténtalo de nuevo en un momento.",
        "lingua_cambiata": "De acuerdo, ahora hablo español.",
        "ripeti_comando": "Repite el comando.",
        "domanda_chiudi": "¿Quieres que cierre el navegador, la carpeta o la ventana en primer plano?",
        "chiudo_browser": "Cierro el navegador.", "chiudo_cartella": "Cierro la carpeta.",
        "chiudo_finestra": "Cierro la ventana.", "non_riesco_chiudere": "No consigo cerrarla.",
        "nessuna_finestra": "No hay ninguna ventana que cerrar.",
    },
    "en": {
        "benvenuto": "Hello {nome}, how are you? What do you need today?",
        "caricamento": "Loading...",
        "errore_ascolto": "{nome}, I have a problem and I can't listen to you. Check the file eva dot log.",
        "copia_in_corso": "I'm still working on a copy, {nome}. Say cancel to stop it.",
        "groq_offline": "I can't connect to the internet, {nome}. Without it I don't understand this command.",
        "groq_errore": "The artificial intelligence service isn't responding, {nome}. Try again shortly.",
        "meteo_chiedi_citta": "Which city do you want the weather for, {nome}? I'll remember it for next time.",
        "meteo_offline": "I can't connect to the internet, {nome}: I can't give you the weather right now.",
        "meteo_non_trovata": "I can't find the city {citta}. Try saying: what's the weather in, and the city name.",
        "meteo_errore": "The weather service isn't responding. Try again shortly.",
        "lingua_cambiata": "Alright, I'm speaking English now.",
        "ripeti_comando": "Repeat the command.",
        "domanda_chiudi": "Do you want me to close the browser, the folder or the window in front?",
        "chiudo_browser": "Closing the browser.", "chiudo_cartella": "Closing the folder.",
        "chiudo_finestra": "Closing the window.", "non_riesco_chiudere": "I can't close it.",
        "nessuna_finestra": "There is no window to close.",
    },
}
_DETTI_IT = {TESTI["it"][k]: k for k in ("chiudo_browser", "chiudo_cartella", "chiudo_finestra",
                                         "non_riesco_chiudere", "nessuna_finestra")}


def t(chiave, **campi):
    """Frase del codice nella lingua corrente (se manca, in italiano)."""
    testo = TESTI[corrente].get(chiave) or TESTI["it"].get(chiave, chiave)
    try:
        return testo.format(**campi)
    except (KeyError, IndexError):
        return testo


def rendi(detto):
    """Una frase italiana fissa di azioni.py ("Chiudo il browser.") nella lingua corrente. Altre frasi: invariate."""
    if corrente == "it":
        return detto
    chiave = _DETTI_IT.get((detto or "").strip())
    return t(chiave) if chiave else detto


# ----------------------------------------------------------------------
# Istruzioni per Groq
# ----------------------------------------------------------------------
GROQ_CHAT = {
    "fr": ("Tu es Eva, l'assistante vocale du PC Windows de @NOME@. Réponds toujours en français, de façon brève "
           "(une ou deux phrases, 40 mots au maximum), naturelle et adaptée à une lecture à voix haute. N'utilise ni "
           "listes, ni puces, ni tableaux, ni markdown, ni emojis, ni symboles. Eva PEUT ouvrir et fermer des "
           "programmes, gérer des fichiers et des dossiers (chercher, copier, déplacer) et dicter du texte avec la "
           "touche F9. Ne réponds jamais que tu ne peux pas ouvrir ou fermer des programmes ni gérer des fichiers : "
           "ces commandes sont exécutées par le PC de @NOME@. Si @NOME@ te demande d'ouvrir ou de fermer quelque chose "
           "et que la demande arrive jusqu'à toi, réponds positivement et invite-le à répéter la commande, par "
           "exemple « Bien sûr, dis : ouvre Chrome » ou « ferme Chrome ». Pour le reste, tu peux discuter librement "
           "avec @NOME@."),
    "es": ("Eres Eva, la asistente de voz del PC con Windows de @NOME@. Responde siempre en español, de forma breve "
           "(una o dos frases, máximo 40 palabras), natural y adecuada para leerse en voz alta. No uses listas, "
           "viñetas, tablas, markdown, emojis ni símbolos. Eva PUEDE abrir y cerrar programas, gestionar archivos y "
           "carpetas (buscar, copiar, mover) y dictar texto con la tecla F9. No respondas nunca que no puedes abrir o "
           "cerrar programas ni gestionar archivos: esos comandos los ejecuta el PC de @NOME@. Si @NOME@ te pide abrir "
           "o cerrar algo y la petición te llega a ti, responde de forma positiva e invítale a repetir el comando, "
           "por ejemplo «Claro, di: abre Chrome» o «cierra Chrome». Para lo demás puedes charlar libremente con @NOME@."),
    "en": ("You are Eva, the voice assistant on @NOME@'s Windows PC. Always answer in English, briefly (one or two "
           "sentences, 40 words at most), naturally and in a way that is easy to read aloud. Do not use lists, bullet "
           "points, tables, markdown, emojis or symbols. Eva CAN open and close programs, manage files and folders "
           "(search, copy, move) and dictate text with the F9 key. Never answer that you can't open or close programs "
           "or manage files: those commands are run by @NOME@'s PC. If @NOME@ asks you to open or close something and "
           "the request reaches you, answer positively and invite him to repeat the command, for example "
           "\"Sure, say: open Chrome\" or \"close Chrome\". For everything else you can chat freely with @NOME@."),
}


# Esempi per "cosa sai fare?" (aiuto.py): cambio lingua
ESEMPI = {
    "it": ["parla francese", "parla inglese", "parla spagnolo", "parla italiano"],
    "fr": ["parle français", "parle anglais", "parle espagnol", "parle italien"],
    "es": ["habla francés", "habla inglés", "habla español", "habla italiano"],
    "en": ["speak French", "speak English", "speak Spanish", "speak Italian"],
}


def groq_chat(italiano):
    """Il messaggio di sistema per la chiacchiera: nella lingua scelta. `italiano` = il testo italiano di base."""
    if corrente == "it":
        return italiano
    return GROQ_CHAT[corrente]


def groq_ordini_extra():
    """Riga da aggiungere alle istruzioni che traducono un ordine in JSON (vox_ia_azioni.py)."""
    if corrente == "it":
        return ""
    p = PACCHETTI[corrente]
    return ("\nLa frase può essere in italiano, francese, spagnolo o inglese (di solito in " + p["nome"] + "). "
            "I nomi di programmi e cartelle vanno lasciati come sono detti. Il campo \"domanda\" (azione \"chiarisci\") "
            "va scritto nella lingua " + p["nome"] + ", non in italiano.")


# ----------------------------------------------------------------------
# Traduzione dei comandi verso i comandi interni di Eva (italiano)
# ----------------------------------------------------------------------
_ART = {
    "en": r"(?:the|a|an|my|up)",
    "fr": r"(?:le|la|les|l|un|une|mon|ma|mes|du|des)",
    "es": r"(?:el|la|los|las|un|una|mi|mis)",
}
_RIEMPITIVI_INIZIO = {
    "en": r"(?:eva|please|can you|could you|would you|will you|i want you to|i would like you to|i d like you to|"
          r"i want to|i d like to|now|just|go ahead and)",
    "fr": r"(?:eva|s il te plait|s il vous plait|peux tu|peux tu me|pourrais tu|pourrais tu me|tu peux|vous pouvez|"
          r"pouvez vous|pouvez vous me|voulez vous|pourriez vous|pourriez vous me|"
          r"je veux que tu|je voudrais que tu|maintenant|alors|ensuite|dis|voudrais tu)",
    "es": r"(?:eva|por favor|puedes|podrias|me puedes|me podrias|quiero que|quisiera que|ahora|entonces|pues|vale)",
}
_RIEMPITIVI_FINE = {
    "en": r"(?:please|for me|now|thanks|thank you)",
    "fr": r"(?:s il te plait|s il vous plait|stp|svp|pour moi|maintenant|merci)",
    "es": r"(?:por favor|para mi|ahora|gracias)",
}
_VERBI = {
    "en": {"apri": r"(?:open|launch|start|run|show me|show)", "chiudi": r"(?:close|shut down|shut|quit|exit)",
           "cerca": r"(?:search for|search|look for|find|google)"},
    "fr": {"apri": r"(?:ouvre|ouvrir|ouvres|ouvrez|lance|lancer|demarre|demarrer|affiche|montre moi|montre)",
           "chiudi": r"(?:ferme|fermer|fermes|fermez|quitte|quitter)",
           "cerca": r"(?:cherche|chercher|recherche|rechercher|trouve|trouver)"},
    "es": {"apri": r"(?:abre|abrir|abres|abran|abra|lanza|lanzar|inicia|iniciar|ejecuta|ejecutar|muestra|muestrame)",
           "chiudi": r"(?:cierra|cerrar|cierras|cierre|sal de|salir de)",
           "cerca": r"(?:busca|buscar|buscame|encuentra|encontrar)"},
}
_E = {"en": r"(?:and|and then|then)", "fr": r"(?:et|et puis|puis|et ensuite)", "es": r"(?:y|e|y luego|luego|despues)"}
_CARTELLA = {"en": r"(?:folder|directory)", "fr": r"(?:dossier|repertoire)", "es": r"(?:carpeta|directorio)"}
_FILE = {"en": r"(?:file|document)", "fr": r"(?:fichier|document)", "es": r"(?:archivo|fichero|documento)"}
_BROWSER = {"en": r"(?:browser|chrome|edge|firefox|brave|opera|internet|google)",
            "fr": r"(?:navigateur|chrome|edge|firefox|brave|opera|internet|google)",
            "es": r"(?:navegador|chrome|edge|firefox|brave|opera|internet|google)"}
_FINESTRA = {"en": r"(?:window|this window|the window)", "fr": r"(?:fenetre|cette fenetre)",
             "es": r"(?:ventana|esta ventana)"}

# nomi di programmi e cartelle che Eva conosce in italiano
_ALIAS = {
    "en": {"notepad": "blocco note", "calculator": "calcolatrice", "file explorer": "esplora risorse",
           "explorer": "esplora risorse", "documents": "documenti", "downloads": "scaricati", "desktop": "desktop",
           "pictures": "immagini", "photos": "foto", "music": "musica", "videos": "video", "settings": "impostazioni",
           "calendar": "calendario", "mail": "posta", "camera": "fotocamera"},
    "fr": {"bloc notes": "blocco note", "bloc note": "blocco note", "calculatrice": "calcolatrice",
           "explorateur": "esplora risorse", "explorateur de fichiers": "esplora risorse", "documents": "documenti",
           "telechargements": "scaricati", "bureau": "desktop", "images": "immagini", "photos": "foto",
           "musique": "musica", "videos": "video", "parametres": "impostazioni", "calendrier": "calendario",
           "courrier": "posta", "messagerie": "posta", "appareil photo": "fotocamera"},
    "es": {"bloc de notas": "blocco note", "bloc notas": "blocco note", "calculadora": "calcolatrice",
           "explorador": "esplora risorse", "explorador de archivos": "esplora risorse", "documentos": "documenti",
           "descargas": "scaricati", "escritorio": "desktop", "imagenes": "immagini", "fotos": "foto",
           "musica": "musica", "videos": "video", "configuracion": "impostazioni", "calendario": "calendario",
           "correo": "posta", "camara": "fotocamera"},
}

# come Eva DICE a voce i nomi (nel catalogo i programmi hanno nome italiano)
_NOMI_VISIBILI = {
    "fr": {"blocco note": "le Bloc-notes", "calcolatrice": "la Calculatrice", "esplora risorse": "l'Explorateur",
           "documenti": "Documents", "scaricati": "Téléchargements", "desktop": "le Bureau", "immagini": "Images",
           "foto": "Photos", "musica": "Musique", "video": "Vidéos", "impostazioni": "les Paramètres",
           "calendario": "le Calendrier", "posta": "le Courrier", "fotocamera": "l'Appareil photo"},
    "es": {"blocco note": "el Bloc de notas", "calcolatrice": "la Calculadora", "esplora risorse": "el Explorador",
           "documenti": "Documentos", "scaricati": "Descargas", "desktop": "el Escritorio", "immagini": "Imágenes",
           "foto": "Fotos", "musica": "Música", "video": "Vídeos", "impostazioni": "Configuración",
           "calendario": "el Calendario", "posta": "el Correo", "fotocamera": "la Cámara"},
    "en": {"blocco note": "Notepad", "calcolatrice": "the Calculator", "esplora risorse": "File Explorer",
           "documenti": "Documents", "scaricati": "Downloads", "desktop": "the Desktop", "immagini": "Pictures",
           "foto": "Photos", "musica": "Music", "video": "Videos", "impostazioni": "Settings",
           "calendario": "the Calendar", "posta": "Mail", "fotocamera": "the Camera"},
}


def nome_visibile(nome):
    """Nome di un programma o di una cartella speciale, come lo dice Eva nella lingua corrente
    ("blocco note" -> "Notepad", "le Bloc-notes", "el Bloc de notas"). None se non cambia."""
    if corrente == "it":
        return None
    return _NOMI_VISIBILI.get(corrente, {}).get(norm(nome))


# frasi intere -> comando italiano (la frase intera deve corrispondere)
_FRASI = {
    "en": [
        (r"(?:turn up|raise|increase|put up)\s+(?:the\s+)?(?:volume|sound)|turn\s+(?:the\s+)?(?:volume|sound)\s+up|volume up|louder|turn it up", "alza il volume"),
        (r"(?:turn down|lower|decrease|reduce|put down)\s+(?:the\s+)?(?:volume|sound)|turn\s+(?:the\s+)?(?:volume|sound)\s+down|volume down|quieter|softer|turn it down", "abbassa il volume"),
        (r"mute(?:\s+(?:the\s+)?(?:sound|audio|volume))?|sound off|turn (?:the )?sound off", "muto"),
        (r"unmute(?:\s+(?:the\s+)?(?:sound|audio|volume))?|sound on|turn (?:the )?sound on", "togli il muto"),
        (r"(?:set\s+)?(?:the\s+)?volume\s+(?:to|at)?\s*(\d{1,3})(?:\s*(?:percent|%))?", "volume al {0}"),
        (r"go back|back|go to the previous folder", "torna indietro"),
        (r"go up|up one level|parent folder", "vai su"),
        (r"(?:move|step|get) (?:aside|out of the way)|move to the side|put yourself aside|make yourself small|shrink", "mettiti da parte"),
        (r"(?:come|move|go|get) (?:to )?(?:the )?(?:center|centre|middle)|come here|make yourself big|enlarge", "mettiti al centro"),
        (r"hide|hide yourself|disappear", "nasconditi"),
        (r"(?:move|go|put yourself|put it) (?:to )?(?:the )?(top|bottom|upper|lower)\s*(left|right)", "sposta in {0} a {1}"),
    ],
    "fr": [
        (r"(?:monte|augmente|hausse)\s+(?:le\s+)?(?:volume|son)|plus fort|volume plus fort|mets (?:le )?(?:son|volume) plus fort", "alza il volume"),
        (r"(?:baisse|diminue|reduis)\s+(?:le\s+)?(?:volume|son)|moins fort|volume moins fort|mets (?:le )?(?:son|volume) moins fort", "abbassa il volume"),
        (r"coupe (?:le )?son|mets (?:le )?(?:son )?en sourdine|muet|sourdine", "muto"),
        (r"(?:remets|reactive|retablis|remets en route)\s+(?:le\s+)?son|reactive l audio|enleve (?:le )?muet", "togli il muto"),
        (r"(?:mets\s+)?(?:le\s+)?volume\s+(?:a|au)?\s*(\d{1,3})(?:\s*(?:pour cent|%))?", "volume al {0}"),
        (r"reviens en arriere|retourne en arriere|retour|dossier precedent", "torna indietro"),
        (r"monte d un niveau|dossier parent|remonte", "vai su"),
        (r"(?:mets|pousse) toi (?:de cote|sur le cote|a l ecart)|pousse toi|ecarte toi|fais toi petite|reduis toi", "mettiti da parte"),
        (r"(?:mets|viens|reviens) (?:toi )?(?:au|vers le) centre|viens ici|fais toi grande|agrandis toi", "mettiti al centro"),
        (r"cache toi|disparais|masque toi", "nasconditi"),
        (r"(?:mets|deplace) toi (?:en|tout en) (haut|bas)\s*(?:a\s+)?(gauche|droite)", "sposta in {0} a {1}"),
    ],
    "es": [
        (r"(?:sube|aumenta|subir|pon mas alto)\s+(?:el\s+)?(?:volumen|sonido)|mas fuerte|volumen mas alto", "alza il volume"),
        (r"(?:baja|reduce|disminuye|bajar)\s+(?:el\s+)?(?:volumen|sonido)|mas bajo|mas flojo|volumen mas bajo", "abbassa il volume"),
        (r"silencia(?:r)?(?:\s+(?:el\s+)?(?:sonido|audio))?|quita (?:el )?sonido|apaga (?:el )?sonido|mutea(?:r)?", "muto"),
        (r"(?:activa|reactiva|pon|restablece)\s+(?:el\s+)?(?:sonido|audio)|quita (?:el )?silencio", "togli il muto"),
        (r"(?:pon\s+)?(?:el\s+)?volumen\s+(?:a|al)?\s*(\d{1,3})(?:\s*(?:por ciento|%))?", "volume al {0}"),
        (r"vuelve atras|ve atras|atras|carpeta anterior", "torna indietro"),
        (r"sube un nivel|carpeta superior|carpeta padre|sube", "vai su"),
        (r"(?:ponte|hazte) (?:a un lado|a un costado|en un lado)|apartate|quitate de en medio|hazte pequena|reducete", "mettiti da parte"),
        (r"(?:ponte|ven|vuelve) (?:al|en el) centro|ven aqui|hazte grande|agrandate", "mettiti al centro"),
        (r"escondete|ocultate|desaparece", "nasconditi"),
        (r"(?:ponte|muevete|mueve) (?:en|arriba|abajo)?\s*(arriba|abajo)\s*(?:a la\s+)?(izquierda|derecha)", "sposta in {0} a {1}"),
    ],
}
_DIREZIONI = {"top": "alto", "upper": "alto", "bottom": "basso", "lower": "basso", "left": "sinistra", "right": "destra",
              "haut": "alto", "bas": "basso", "gauche": "sinistra", "droite": "destra",
              "arriba": "alto", "abajo": "basso", "izquierda": "sinistra", "derecha": "destra"}


def _togli_articoli(s, lingua):
    return re.sub(rf"^(?:{_ART[lingua]}\s+)+", "", s).strip()


def _oggetto(o, lingua):
    """Traduce l'oggetto di "apri": cartelle, file, nomi di programmi; più cose insieme con "e"."""
    parti = [p for p in re.split(rf"\s+{_E[lingua]}\s+", o) if p.strip()]
    uscita = []
    for p in parti:
        p = _togli_articoli(p, lingua)
        m = re.match(rf"^{_CARTELLA[lingua]}\s+(?:(?:{_ART[lingua]}|de|di|of|del|de la|de l)\s+)*(.+)$", p)
        if m:
            uscita.append("la cartella " + _alias(m.group(1), lingua))
            continue
        m = re.match(rf"^{_FILE[lingua]}\s+(?:(?:{_ART[lingua]}|de|di|of|del|de la|de l)\s+)*(.+)$", p)
        if m:
            uscita.append("il file " + m.group(1))
            continue
        uscita.append(_alias(p, lingua))
    return " e ".join(uscita)


def _alias(nome, lingua):
    n = _togli_articoli(nome.strip(), lingua)
    return _ALIAS[lingua].get(n, n)


def verso_italiano(testo, lingua=None):
    """Se `testo` è un comando nella lingua scelta (open Chrome, ouvre Chrome, abre Chrome, close Chrome, volume up,
    move aside...) restituisce il comando interno in italiano ("apri Chrome"), altrimenti None.
    Una frase che non è un comando resta com'è (chiacchiera, domanda...)."""
    lingua = lingua or corrente
    if lingua == "it" or lingua not in _VERBI:
        return None
    t_ = norm(testo)
    if not t_:
        return None
    t_ = re.sub(rf"^(?:{_RIEMPITIVI_INIZIO[lingua]}\s+)+", "", t_)
    t_ = re.sub(rf"(?:\s+{_RIEMPITIVI_FINE[lingua]})+$", "", t_).strip()
    if not t_:
        return None
    for schema, uscita in _FRASI[lingua]:
        m = re.fullmatch(schema, t_)
        if m:
            gruppi = [_DIREZIONI.get(g, g) for g in m.groups()]
            return uscita.format(*gruppi) if gruppi else uscita
    V = _VERBI[lingua]
    m = re.fullmatch(rf"{V['chiudi']}(?:\s+(.+))?", t_)
    if m:
        o = _togli_articoli(m.group(1) or "", lingua)
        if not o:
            return "chiudi"
        if re.fullmatch(_CARTELLA[lingua], o):
            return "chiudi la cartella"
        if re.fullmatch(_FINESTRA[lingua], o):
            return "chiudi finestra"
        if re.fullmatch(_BROWSER[lingua], o):
            return "chiudi il browser" if o in ("browser", "navigateur", "navegador") else "chiudi " + o
        return "chiudi " + o                     # "chiudi Chrome", "chiudi Edge"...
    m = re.fullmatch(rf"{V['apri']}\s+(.+)", t_)
    if m:
        return "apri " + _oggetto(m.group(1), lingua)
    m = re.fullmatch(rf"{V['cerca']}\s+(.+)", t_)
    if m:
        o = m.group(1)
        motore = ""
        m2 = re.match(rf"^(.+?)\s+(?:on|sur|en|in)\s+(youtube|google)$", o)
        if m2:
            o, motore = m2.group(1), m2.group(2)
        m3 = re.match(rf"^(?:on|sur|en|in)\s+(youtube|google)\s+(?:for\s+|pour\s+|de\s+|sobre\s+)?(.+)$", o)
        if m3:
            motore, o = m3.group(1), m3.group(2)
        o = re.sub(rf"^(?:for|pour|de|sobre|about)\s+", "", o).strip()
        return "cerca " + (f"su {motore} " if motore else "") + o
    return None


# ----------------------------------------------------------------------
# Cambio lingua a voce
# ----------------------------------------------------------------------
_NOMI_LINGUA = {
    "it": {"italiano", "italiana", "italian", "italien", "italienne", "italiana"},
    "fr": {"francese", "french", "francais", "francaise", "frances", "francesa", "francese"},
    "es": {"spagnolo", "spanish", "espagnol", "espanol", "espanola", "castellano"},
    "en": {"inglese", "english", "anglais", "ingles", "inglesa", "anglaise"},
}
_VERBI_CAMBIO = {"parla", "parlare", "parlami", "parli", "parlate", "rispondi", "rispondimi", "cambia", "cambiare",
                 "passa", "passare", "metti", "imposta", "lingua", "usa", "usare",
                 "parle", "parlez", "reponds", "repondre", "change", "changer", "passe", "mets", "langue", "utilise",
                 "habla", "hablar", "hablame", "responde", "responder", "cambiar", "pasa", "pon", "idioma", "usa",
                 "speak", "talk", "answer", "reply", "switch", "set", "language", "respond", "use", "go", "say"}
_SALTA = {"eva", "vox", "per", "favore", "please", "por", "favor", "s", "il", "te", "plait", "vous", "pure", "ora",
          "adesso", "now", "ahora", "maintenant", "can", "you", "could", "puoi", "puedes", "peux", "tu", "pouvez",
          "to", "in", "en", "a", "au", "al", "the", "la", "le", "el", "di", "de", "del", "mi", "me", "moi", "with",
          "con", "avec", "dans"}


def _e_nome_lingua(tok, nomi):
    """Il nome della lingua, anche se Whisper lo sente un po' storpiato ("spaniolo" per "spagnolo")."""
    if tok in nomi:
        return True
    if len(tok) < 6:
        return False
    return bool(difflib.get_close_matches(tok, nomi, n=1, cutoff=0.82))


def riconosci_cambio(testo):
    """"Eva, parla francese" / "speak English" / "habla español" / "parle italien" / "cambia lingua in spagnolo".
    Restituisce il codice della lingua (it, fr, es, en) oppure None."""
    toks = norm(testo).split()
    if not toks or len(toks) > 8:
        return None
    for codice, nomi in _NOMI_LINGUA.items():
        for j, tok in enumerate(toks):
            if _e_nome_lingua(tok, nomi):
                prima = toks[:j]
                if any(p in _VERBI_CAMBIO for p in prima):
                    # imperativo: un verbo di cambio ("parla", "speak", "habla"...) tra i primi due vocaboli
                    # significativi (il secondo posto copre un'esitazione tipo "Escolta, cambia lingua...")
                    significativi = [p for p in toks if p not in _SALTA]
                    if any(p in _VERBI_CAMBIO for p in significativi[:2]):
                        return codice
                if len(toks) <= 3 and prima and prima[0] in ("in", "en", "au", "a"):
                    return codice
    return None


# ----------------------------------------------------------------------
# Impostazioni: lingua salvata
# ----------------------------------------------------------------------
def _leggi_impostazioni():
    try:
        d = json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def lingua_salvata():
    v = _leggi_impostazioni().get("lingua")
    return v if v in CODICI else None


def salva(codice):
    """Scrive la lingua in impostazioni_eva.json senza toccare le altre chiavi."""
    d = _leggi_impostazioni()
    d["lingua"] = codice
    try:
        FILE_IMPOSTAZIONI.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        _log.exception("Non riesco a salvare la lingua in impostazioni_eva.json")
        return False


def imposta(codice, salva_nel_file=True):
    """Cambia la lingua corrente (e la salva). Restituisce il codice."""
    global corrente
    if codice not in PACCHETTI:
        raise ValueError(f"lingua sconosciuta: {codice}")
    corrente = codice
    if salva_nel_file:
        salva(codice)
    return codice


def inizializza(imp):
    """All'avvio: sceglie la lingua (quella salvata, altrimenti LINGUA di dettatura.py) e adatta le impostazioni
    (voce, parole suggerite, frasi di chiusura). Va chiamata PRIMA di creare la voce."""
    global corrente
    base = imp.get("LINGUA", "it")
    corrente = lingua_salvata() or (base if base in PACCHETTI else "it")
    imp.setdefault("_FRASI_CHIUSURA_IT", list(imp.get("FRASI_CHIUSURA", [])))
    imp.setdefault("_PAROLE_SUGGERITE_IT", list(imp.get("PAROLE_SUGGERITE", [])))
    applica_a_impostazioni(imp)
    return corrente


def nome_voce(imp, lingua=None):
    lingua = lingua or corrente
    voci = imp.get("VOCI_LINGUE") or {}
    return voci.get(lingua) or PACCHETTI[lingua]["voce"]


def applica_a_impostazioni(imp):
    """Mette nelle impostazioni (imp) tutto ciò che dipende dalla lingua corrente."""
    p = PACCHETTI[corrente]
    imp["LINGUA_CORRENTE"] = corrente
    imp["VOCE_NOME"] = nome_voce(imp)
    imp["VOCE_CULTURA"] = p["cultura"]
    imp["VOCE_LCID"] = list(p["lcid"])
    base_ch = imp.get("_FRASI_CHIUSURA_IT", [])
    imp["FRASI_CHIUSURA"] = list(base_ch) + [f for f in p["chiusura"] if f not in base_ch]
    imp["PAROLE_SUGGERITE"] = list(p["parole_suggerite"] or imp.get("_PAROLE_SUGGERITE_IT", []))


def codice_whisper():
    return PACCHETTI[corrente]["whisper"]


def file_risposte(cartella):
    return Path(cartella) / PACCHETTI[corrente]["file_risposte"]


# ----------------------------------------------------------------------
# Ora, data, saluto
# ----------------------------------------------------------------------
_GIORNI = {
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
_MESI = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
           "novembre", "décembre"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
}


def frase_ora(adesso=None):
    adesso = adesso or datetime.datetime.now()
    h, m = adesso.hour, adesso.minute
    if corrente == "fr":
        if h == 0 and m == 0:
            return "Il est minuit"
        if h == 12 and m == 0:
            return "Il est midi"
        return f"Il est {h} heure{'s' if h > 1 else ''}" + (f" {m}" if m else "")
    if corrente == "es":
        if h == 0 and m == 0:
            return "Es medianoche"
        if h == 12 and m == 0:
            return "Es mediodía"
        h12 = h % 12 or 12
        return ("Es la una" if h12 == 1 else f"Son las {h12}") + (f" y {m}" if m else " en punto")
    if corrente == "en":
        if h == 0 and m == 0:
            return "It's midnight"
        if h == 12 and m == 0:
            return "It's noon"
        return f"It's {h}:{m:02d}" if m else f"It's {h} o'clock"
    return None


def frase_data(oggi=None):
    oggi = oggi or datetime.date.today()
    g, mese, a = oggi.day, oggi.month, oggi.year
    if corrente == "fr":
        return f"{_GIORNI['fr'][oggi.weekday()]} {'1er' if g == 1 else g} {_MESI['fr'][mese - 1]} {a}"
    if corrente == "es":
        return f"{_GIORNI['es'][oggi.weekday()]} {g} de {_MESI['es'][mese - 1]} de {a}"
    if corrente == "en":
        return f"{_GIORNI['en'][oggi.weekday()]}, {_MESI['en'][mese - 1]} {g}, {a}"
    return None


def saluto_per_ora(adesso=None):
    h = (adesso or datetime.datetime.now()).hour
    if corrente == "fr":
        return "Bonjour" if 5 <= h < 18 else ("Bonsoir" if 18 <= h < 22 else "Salut")
    if corrente == "es":
        return "Buenos días" if 5 <= h < 13 else ("Buenas tardes" if 13 <= h < 21 else "Hola")
    if corrente == "en":
        return "Good morning" if 5 <= h < 13 else ("Good afternoon" if 13 <= h < 18 else ("Good evening" if 18 <= h < 22 else "Hello"))
    return None


# ----------------------------------------------------------------------
# Meteo: parole e frasi
# ----------------------------------------------------------------------
METEO = {
    "fr": {"chiave": r"\b(meteo|previsions?|pleut|pleuvoir|quel temps|temps fait|il fait quel temps|temps qu il fait)\b",
           "prep": r"\b(?:a|de|pour|sur|en|au|dans)\s+([a-z]+(?:\s+[a-z]+){0,3})",
           "fine": {"aujourd", "hui", "demain", "maintenant", "ce", "ce soir", "ce matin", "ce", "pour", "s", "il", "fait"},
           "domani": "demain", "non_citta": {"quel", "temps", "il", "fait", "la", "le", "les", "mon", "ma", "ici", "moi",
                                             "aujourd", "demain", "maintenant", "chez", "pluie", "meteo"}},
    "es": {"chiave": r"\b(meteo|clima|pronostico|prevision|previsiones|llueve|llover|que tiempo|tiempo hace|como esta el tiempo)\b",
           "prep": r"\b(?:en|de|para|a)\s+([a-z]+(?:\s+[a-z]+){0,3})",
           "fine": {"hoy", "manana", "ahora", "esta", "para", "pasado", "hace"},
           "domani": "manana", "non_citta": {"que", "tiempo", "hace", "el", "la", "los", "mi", "aqui", "mi", "hoy",
                                             "manana", "ahora", "clima", "lluvia"}},
    "en": {"chiave": r"\b(weather|forecast|rain|raining|going to rain|will it rain|temperature outside)\b",
           "prep": r"\b(?:in|for|at|of|near)\s+([a-z]+(?:\s+[a-z]+){0,3})",
           "fine": {"today", "tomorrow", "now", "tonight", "this", "for", "like", "please", "right"},
           "domani": "tomorrow", "non_citta": {"the", "my", "here", "there", "it", "me", "now", "today", "tomorrow",
                                               "like", "weather", "forecast", "rain", "tonight"}},
}

_CODICE_CIELO = {0: "sereno", 1: "quasi_sereno", 2: "parz", 3: "coperto", 45: "nebbia", 48: "nebbia",
                 51: "pioggerella", 53: "pioggerella", 55: "pioggerella", 56: "pioggerella_gelata",
                 57: "pioggerella_gelata", 61: "pioggia_leggera", 63: "pioggia", 65: "pioggia_forte",
                 66: "pioggia_gelata", 67: "pioggia_gelata", 71: "neve_leggera", 73: "neve", 75: "neve_forte",
                 77: "nevischio", 80: "rovesci", 81: "rovesci", 82: "rovesci_forti", 85: "rovesci_neve",
                 86: "rovesci_neve", 95: "temporale", 96: "temporale_grandine", 99: "temporale_grandine"}
_CIELO = {
    "fr": {"sereno": "dégagé", "quasi_sereno": "plutôt dégagé", "parz": "partiellement nuageux", "coperto": "couvert",
           "nebbia": "brouillard", "pioggerella": "bruine", "pioggerella_gelata": "bruine verglaçante",
           "pioggia_leggera": "pluie légère", "pioggia": "pluie", "pioggia_forte": "forte pluie",
           "pioggia_gelata": "pluie verglaçante", "neve_leggera": "neige légère", "neve": "neige",
           "neve_forte": "forte neige", "nevischio": "grésil", "rovesci": "averses", "rovesci_forti": "fortes averses",
           "rovesci_neve": "averses de neige", "temporale": "orage", "temporale_grandine": "orage avec grêle"},
    "es": {"sereno": "despejado", "quasi_sereno": "mayormente despejado", "parz": "parcialmente nublado",
           "coperto": "cubierto", "nebbia": "niebla", "pioggerella": "llovizna", "pioggerella_gelata": "llovizna helada",
           "pioggia_leggera": "lluvia ligera", "pioggia": "lluvia", "pioggia_forte": "lluvia fuerte",
           "pioggia_gelata": "lluvia helada", "neve_leggera": "nieve ligera", "neve": "nieve",
           "neve_forte": "nieve fuerte", "nevischio": "aguanieve", "rovesci": "chubascos",
           "rovesci_forti": "chubascos fuertes", "rovesci_neve": "chubascos de nieve", "temporale": "tormenta",
           "temporale_grandine": "tormenta con granizo"},
    "en": {"sereno": "clear", "quasi_sereno": "mostly clear", "parz": "partly cloudy", "coperto": "overcast",
           "nebbia": "fog", "pioggerella": "drizzle", "pioggerella_gelata": "freezing drizzle",
           "pioggia_leggera": "light rain", "pioggia": "rain", "pioggia_forte": "heavy rain",
           "pioggia_gelata": "freezing rain", "neve_leggera": "light snow", "neve": "snow", "neve_forte": "heavy snow",
           "nevischio": "sleet", "rovesci": "showers", "rovesci_forti": "heavy showers",
           "rovesci_neve": "snow showers", "temporale": "thunderstorm", "temporale_grandine": "thunderstorm with hail"},
}


def cielo(codice, lingua=None):
    lingua = lingua or corrente
    chiave = _CODICE_CIELO.get(int(codice))
    return _CIELO.get(lingua, {}).get(chiave) if chiave else None


METEO_ISTRUZIONE = {
    "it": ("Riassumi a voce, in UNA frase breve e naturale in italiano (temperatura, cielo, pioggia), questi dati meteo "
           "già verificati. Scrivi i numeri e le unità a parole (gradi, per cento), senza simboli come ° o %. Non "
           "aggiungere dati che non ci sono e non dire che non puoi: "),
    "fr": ("Résume à voix haute, en UNE phrase brève et naturelle en français (température, ciel, pluie), ces données "
           "météo déjà vérifiées. Écris les nombres et les unités en toutes lettres (degrés, pour cent), sans symboles "
           "comme ° ou %. N'ajoute aucune donnée absente et ne dis pas que tu ne peux pas : "),
    "es": ("Resume en voz alta, en UNA frase breve y natural en español (temperatura, cielo, lluvia), estos datos del "
           "tiempo ya verificados. Escribe los números y las unidades con palabras (grados, por ciento), sin símbolos "
           "como ° o %. No añadas datos que no estén y no digas que no puedes: "),
    "en": ("Summarize aloud, in ONE short and natural sentence in English (temperature, sky, rain), this already "
           "verified weather data. Write numbers and units in words (degrees, percent), without symbols like ° or %. "
           "Do not add data that isn't there and do not say you can't: "),
}


def frase_meteo_locale(dati):
    """Frase di ripiego (se Groq non risponde) per fr, es, en. `dati` come da vox_meteo.cerca."""
    quando_ora = dati["giorno"] == "oggi"
    c, tmax, tmin, pr = dati["citta"], dati["temp_max"], dati["temp_min"], dati.get("prob_pioggia")
    ora_t = dati.get("temp_ora")
    cielo_txt = dati["cielo"]
    if corrente == "fr":
        s = f"À {c} {'aujourd’hui' if quando_ora else 'demain'} : {cielo_txt}, "
        s += (f"actuellement {ora_t} degrés, maximum {tmax}. " if ora_t is not None
              else f"entre {tmin} et {tmax} degrés. ")
        return s + ("Pluie peu probable." if pr is None or pr < 20 else f"Probabilité de pluie {pr} pour cent.")
    if corrente == "es":
        s = f"En {c} {'hoy' if quando_ora else 'mañana'}: {cielo_txt}, "
        s += (f"ahora {ora_t} grados, máxima de {tmax}. " if ora_t is not None
              else f"entre {tmin} y {tmax} grados. ")
        return s + ("Lluvia poco probable." if pr is None or pr < 20 else f"Probabilidad de lluvia {pr} por ciento.")
    s = f"In {c} {'today' if quando_ora else 'tomorrow'}: {cielo_txt}, "
    s += (f"now {ora_t} degrees, high of {tmax}. " if ora_t is not None
          else f"between {tmin} and {tmax} degrees. ")
    return s + ("Rain unlikely." if pr is None or pr < 20 else f"Chance of rain {pr} percent.")
