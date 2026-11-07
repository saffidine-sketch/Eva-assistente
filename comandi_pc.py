"""Libreria di comandi per controllare il PC Windows con la voce (italiano, francese, spagnolo, inglese).

TABELLA = un comando per riga. Per AGGIUNGERNE UNO basta aggiungere una riga C(...) qui sotto:
    C("id_del_comando", "Categoria", "tipo:argomento",
      it="frase uno|frase due",  fr="...", es="...", en="...",
      detto=("cosa dice in it", "fr", "es", "en"))          # facoltativo (altrimenti: "Fatto.")
    pericoloso=True  -> Eva chiede PRIMA conferma a voce ("Vuoi davvero...? Dì sì") e parte solo con "sì".

Tipi di azione (il pezzo prima dei due punti):
    tasti:ctrl+c            scorciatoia da tastiera (pyautogui)        tasti_win:windows+d   tasto Windows (keyboard)
    finestra:maximize       finestra in primo piano (pywinauto): maximize, minimize, restore, close
    avvia:notepad.exe       apre un programma            url:ms-settings:   apre un indirizzo/app di Windows
    browser:                apre il browser predefinito  chiudi_prog:notepad   chiude le finestre di quel programma
    cartella:documenti      apre una cartella (come "apri documenti")    indietro_cartella / su_cartella
    scroll:600              scorre (positivo = su, negativo = giù)
    volume:su|giu|muto|togli_muto   (usa le funzioni che già c'erano in azioni.py)
    schermata, blocca, taskmgr, spegni, riavvia, sospendi, esci, elimina, svuota_cestino, annulla_spegnimento

Le frasi si scrivono come vengono dette (maiuscole, accenti e punteggiatura non contano). Prima del confronto Eva toglie
"per favore", "puoi", "can you", "peux-tu"... davanti e dietro. La frase deve combaciare per intero (o somigliare molto,
tranne che per i comandi pericolosi: quelli solo identici).

Regole di sicurezza: mai agire sulla finestra di Eva; "chiudi" da solo continua a chiudere Eva (non questa libreria).
Questo file NON cancella niente da solo: "elimina" e "svuota il cestino" partono solo dopo il "sì" a voce.
"""
import ctypes
import difflib
import os
import re
import subprocess
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path

import vox_lingue as lingue

LINGUE = ("it", "fr", "es", "en")
SOLO_PROVA = False          # True = i comandi di sistema (spegni, riavvia, blocca...) non partono davvero (solo per le prove)
CONFERMA_SECONDI = 15       # tempo per rispondere "sì" a una domanda di conferma


class Comando:
    def __init__(self, id, categoria, azione, frasi, detto, pericoloso, cosa, raw=None):
        self.id, self.categoria, self.azione = id, categoria, azione
        self.frasi, self.detto, self.pericoloso, self.cosa = frasi, detto, pericoloso, cosa
        self.raw = raw or {}          # le frasi come sono scritte (per il riepilogo a voce: aiuto.py)

    def __repr__(self):
        return f"<Comando {self.id}>"


TABELLA = []


def C(id, categoria, azione, it="", fr="", es="", en="", detto=None, pericoloso=False, cosa=None):
    """Aggiunge un comando alla tabella. it/fr/es/en: frasi separate da |."""
    frasi = {l: [f for f in (lingue.norm(x) for x in testo.split("|")) if f]
             for l, testo in zip(LINGUE, (it, fr, es, en))}
    raw = {l: [x.strip() for x in testo.split("|") if x.strip()] for l, testo in zip(LINGUE, (it, fr, es, en))}
    TABELLA.append(Comando(id, categoria, azione, frasi, detto, pericoloso, cosa, raw))


# ======================================================================
# LA TABELLA
# ======================================================================
# --- FINESTRE ---------------------------------------------------------
C("finestra_ingrandisci", "Finestre", "finestra:maximize",
  it="ingrandisci la finestra|ingrandisci questa finestra|massimizza|massimizza la finestra|massimizza questa finestra|a tutto schermo|schermo intero|puoi ingrandire la finestra",
  fr="agrandis la fenetre|agrandis cette fenetre|maximise|maximise la fenetre|maximise cette fenetre|plein ecran|mets la fenetre en plein ecran",
  es="maximiza la ventana|maximiza esta ventana|maximiza|agranda la ventana|maximizar la ventana|pantalla completa",
  en="maximize|maximize the window|maximize this window|make the window bigger|enlarge the window|full screen|make it full screen",
  detto=("Finestra ingrandita.", "Fenêtre agrandie.", "Ventana maximizada.", "Window maximized."))
C("finestra_riduci", "Finestre", "finestra:minimize",
  it="riduci a icona|riduci la finestra a icona|riduci la finestra|riduci questa finestra|minimizza|minimizza la finestra|minimizza questa finestra|metti la finestra nella barra",
  fr="reduis la fenetre|reduis cette fenetre|reduis en icone|minimise|minimise la fenetre|minimise cette fenetre",
  es="minimiza la ventana|minimiza esta ventana|minimiza|minimizar la ventana|reduce la ventana",
  en="minimize|minimize the window|minimize this window|shrink the window|send the window to the taskbar",
  detto=("Finestra ridotta a icona.", "Fenêtre réduite.", "Ventana minimizada.", "Window minimized."))
C("finestra_ripristina", "Finestre", "finestra:restore",
  it="ripristina la finestra|ripristina questa finestra|ripristina|riporta la finestra alle dimensioni normali|finestra normale",
  fr="restaure la fenetre|restaure cette fenetre|restaure|retablis la fenetre",
  es="restaura la ventana|restaura esta ventana|restaura|restablece la ventana",
  en="restore|restore the window|restore this window|unmaximize|make the window normal size",
  detto=("Finestra ripristinata.", "Fenêtre restaurée.", "Ventana restaurada.", "Window restored."))
C("finestra_chiudi", "Finestre", "finestra:close",
  it="chiudi questa finestra|chiudi la finestra attiva|chiudi la finestra corrente|chiudi la finestra in primo piano|chiudere questa finestra|puoi chiudere questa finestra",
  fr="ferme cette fenetre|ferme la fenetre active|ferme la fenetre actuelle|ferme la fenetre|ferme fenetre",
  es="cierra esta ventana|cierra la ventana activa|cierra la ventana actual|cierra la ventana|cerrar esta ventana",
  en="close this window|close the active window|close the current window|close the window|close window",
  detto=("Finestra chiusa.", "Fenêtre fermée.", "Ventana cerrada.", "Window closed."))
C("finestra_successiva", "Finestre", "tasti:alt+tab",
  it="passa alla finestra successiva|finestra successiva|prossima finestra|cambia finestra|passa all'altra finestra|vai alla finestra successiva",
  fr="fenetre suivante|passe a la fenetre suivante|prochaine fenetre|change de fenetre|va a la fenetre suivante",
  es="ventana siguiente|siguiente ventana|pasa a la siguiente ventana|cambia de ventana|pasa a la ventana siguiente",
  en="next window|switch window|switch to the next window|change window|go to the next window",
  detto=("Finestra successiva.", "Fenêtre suivante.", "Ventana siguiente.", "Next window."))
C("finestra_precedente", "Finestre", "tasti:alt+shift+tab",
  it="passa alla finestra precedente|finestra precedente|torna alla finestra precedente|vai alla finestra precedente",
  fr="fenetre precedente|passe a la fenetre precedente|reviens a la fenetre precedente",
  es="ventana anterior|pasa a la ventana anterior|vuelve a la ventana anterior|ventana previa",
  en="previous window|switch to the previous window|go to the previous window|go back to the previous window",
  detto=("Finestra precedente.", "Fenêtre précédente.", "Ventana anterior.", "Previous window."))
C("mostra_desktop", "Finestre", "tasti_win:windows+d",
  it="mostra il desktop|mostra la scrivania|riduci tutto|nascondi tutte le finestre|mostra il desktop per favore",
  fr="montre le bureau|affiche le bureau|reduis tout|cache toutes les fenetres",
  es="muestra el escritorio|mostrar escritorio|minimiza todo|oculta todas las ventanas",
  en="show the desktop|show desktop|minimize all windows|minimize everything|hide all windows",
  detto=("Ecco il desktop.", "Voici le bureau.", "Aquí está el escritorio.", "Here is the desktop."))
C("affianca_sinistra", "Finestre", "tasti_win:windows+left",
  it="affianca a sinistra|affianca la finestra a sinistra|metti la finestra a sinistra|finestra a sinistra|allinea a sinistra",
  fr="affiche la fenetre a gauche|fenetre a gauche|colle la fenetre a gauche|mets la fenetre a gauche",
  es="ajusta la ventana a la izquierda|ventana a la izquierda|ancla a la izquierda|pon la ventana a la izquierda",
  en="snap left|snap the window left|snap window left|put the window on the left|window to the left",
  detto=("Finestra a sinistra.", "Fenêtre à gauche.", "Ventana a la izquierda.", "Window on the left."))
C("affianca_destra", "Finestre", "tasti_win:windows+right",
  it="affianca a destra|affianca la finestra a destra|metti la finestra a destra|finestra a destra|allinea a destra",
  fr="affiche la fenetre a droite|fenetre a droite|colle la fenetre a droite|mets la fenetre a droite",
  es="ajusta la ventana a la derecha|ventana a la derecha|ancla a la derecha|pon la ventana a la derecha",
  en="snap right|snap the window right|snap window right|put the window on the right|window to the right",
  detto=("Finestra a destra.", "Fenêtre à droite.", "Ventana a la derecha.", "Window on the right."))

# --- PROGRAMMI ----------------------------------------------------------
C("apri_browser", "Programmi", "browser:",
  it="apri il browser|apri browser|avvia il browser|apri internet|apri il navigatore",
  fr="ouvre le navigateur|ouvre navigateur|lance le navigateur|ouvre internet",
  es="abre el navegador|abre navegador|inicia el navegador|abre internet",
  en="open the browser|open browser|launch the browser|start the browser|open internet",
  detto=("Apro il browser.", "J'ouvre le navigateur.", "Abro el navegador.", "Opening the browser."))
C("chiudi_browser", "Programmi", "chiudi_browser:",
  it="chiudi il browser|chiudi browser|chiudi chrome|chiudi google chrome|chiudi edge|chiudi firefox|chiudi internet",
  fr="ferme le navigateur|ferme navigateur|ferme chrome|ferme edge|ferme firefox",
  es="cierra el navegador|cierra navegador|cierra chrome|cierra edge|cierra firefox",
  en="close the browser|close browser|close chrome|close edge|close firefox",
  detto=("Chiudo il browser.", "Je ferme le navigateur.", "Cierro el navegador.", "Closing the browser."))
C("apri_esplora", "Programmi", "avvia:explorer.exe",
  it="apri esplora file|apri esplora risorse|apri esplora|apri l'esplora file|avvia esplora file",
  fr="ouvre l'explorateur de fichiers|ouvre l'explorateur|ouvre explorateur de fichiers|lance l'explorateur",
  es="abre el explorador de archivos|abre el explorador|abre explorador de archivos|inicia el explorador",
  en="open file explorer|open explorer|open the file explorer|launch file explorer|start file explorer",
  detto=("Apro Esplora file.", "J'ouvre l'explorateur.", "Abro el explorador.", "Opening File Explorer."))
C("chiudi_esplora", "Programmi", "chiudi_prog:esplora",
  it="chiudi esplora file|chiudi esplora risorse|chiudi esplora|chiudi tutte le cartelle",
  fr="ferme l'explorateur de fichiers|ferme l'explorateur|ferme toutes les fenetres de dossiers",
  es="cierra el explorador de archivos|cierra el explorador|cierra todas las carpetas",
  en="close file explorer|close explorer|close the file explorer|close all folders",
  detto=("Chiudo Esplora file.", "Je ferme l'explorateur.", "Cierro el explorador.", "Closing File Explorer."))
C("apri_blocconote", "Programmi", "avvia:notepad.exe",
  it="apri il blocco note|apri blocco note|avvia il blocco note|apri notepad",
  fr="ouvre le bloc notes|ouvre bloc notes|lance le bloc notes|ouvre notepad",
  es="abre el bloc de notas|abre bloc de notas|inicia el bloc de notas|abre notepad",
  en="open notepad|open the notepad|launch notepad|start notepad",
  detto=("Apro il Blocco note.", "J'ouvre le Bloc-notes.", "Abro el Bloc de notas.", "Opening Notepad."))
C("chiudi_blocconote", "Programmi", "chiudi_prog:notepad",
  it="chiudi il blocco note|chiudi blocco note|chiudi notepad",
  fr="ferme le bloc notes|ferme bloc notes|ferme notepad",
  es="cierra el bloc de notas|cierra bloc de notas|cierra notepad",
  en="close notepad|close the notepad",
  detto=("Chiudo il Blocco note.", "Je ferme le Bloc-notes.", "Cierro el Bloc de notas.", "Closing Notepad."))
C("apri_calcolatrice", "Programmi", "avvia:calc.exe",
  it="apri la calcolatrice|apri calcolatrice|avvia la calcolatrice",
  fr="ouvre la calculatrice|ouvre calculatrice|lance la calculatrice",
  es="abre la calculadora|abre calculadora|inicia la calculadora",
  en="open the calculator|open calculator|launch the calculator|start the calculator",
  detto=("Apro la Calcolatrice.", "J'ouvre la Calculatrice.", "Abro la Calculadora.", "Opening the Calculator."))
C("chiudi_calcolatrice", "Programmi", "chiudi_prog:calcolatrice",
  it="chiudi la calcolatrice|chiudi calcolatrice",
  fr="ferme la calculatrice|ferme calculatrice",
  es="cierra la calculadora|cierra calculadora",
  en="close the calculator|close calculator",
  detto=("Chiudo la Calcolatrice.", "Je ferme la Calculatrice.", "Cierro la Calculadora.", "Closing the Calculator."))
C("apri_impostazioni", "Programmi", "url:ms-settings:",
  it="apri le impostazioni|apri impostazioni|avvia le impostazioni|apri le impostazioni di windows",
  fr="ouvre les parametres|ouvre parametres|lance les parametres|ouvre les parametres de windows",
  es="abre la configuracion|abre configuracion|abre los ajustes|abre la configuracion de windows",
  en="open settings|open the settings|launch settings|open windows settings",
  detto=("Apro le Impostazioni.", "J'ouvre les Paramètres.", "Abro la Configuración.", "Opening Settings."))
C("chiudi_impostazioni", "Programmi", "chiudi_prog:impostazioni",
  it="chiudi le impostazioni|chiudi impostazioni",
  fr="ferme les parametres|ferme parametres",
  es="cierra la configuracion|cierra configuracion|cierra los ajustes",
  en="close settings|close the settings",
  detto=("Chiudo le Impostazioni.", "Je ferme les Paramètres.", "Cierro la Configuración.", "Closing Settings."))

# --- CARTELLE -------------------------------------------------------------
C("apri_documenti", "Cartelle", "cartella:documenti",
  it="apri documenti|apri i documenti|apri la cartella documenti|vai ai documenti|apri la cartella dei documenti",
  fr="ouvre documents|ouvre les documents|ouvre le dossier documents|va dans les documents|ouvre mes documents",
  es="abre documentos|abre los documentos|abre la carpeta documentos|ve a documentos|abre mis documentos",
  en="open documents|open the documents|open the documents folder|go to documents|open my documents",
  detto=("Apro Documenti.", "J'ouvre Documents.", "Abro Documentos.", "Opening Documents."))
C("apri_download", "Cartelle", "cartella:scaricati",
  it="apri download|apri i download|apri scaricati|apri la cartella download|apri la cartella dei download|vai ai download",
  fr="ouvre telechargements|ouvre les telechargements|ouvre le dossier telechargements|ouvre download|va dans les telechargements",
  es="abre descargas|abre las descargas|abre la carpeta descargas|ve a descargas|abre downloads",
  en="open downloads|open the downloads|open the downloads folder|go to downloads|open download",
  detto=("Apro Download.", "J'ouvre Téléchargements.", "Abro Descargas.", "Opening Downloads."))
C("apri_desktop", "Cartelle", "cartella:desktop",
  it="apri la cartella desktop|apri la cartella scrivania|apri la scrivania|apri desktop",
  fr="ouvre le dossier bureau|ouvre le bureau|ouvre bureau",
  es="abre la carpeta escritorio|abre el escritorio|abre escritorio",
  en="open the desktop folder|open desktop folder|open desktop",
  detto=("Apro il Desktop.", "J'ouvre le Bureau.", "Abro el Escritorio.", "Opening the Desktop."))
C("cartella_indietro", "Cartelle", "indietro_cartella:",
  it="vai indietro|torna indietro|indietro|torna alla cartella precedente|cartella precedente",
  fr="reviens en arriere|retourne en arriere|va en arriere|dossier precedent",
  es="vuelve atras|ve atras|atras|carpeta anterior|vuelve a la carpeta anterior",
  en="go back|back|go to the previous folder|previous folder",
  detto=("Indietro.", "En arrière.", "Atrás.", "Going back."))
C("cartella_su", "Cartelle", "su_cartella:",
  it="vai su|sali|sali di un livello|cartella superiore|vai alla cartella superiore",
  fr="monte d'un niveau|dossier parent|va au dossier parent|remonte|monte",
  es="sube un nivel|carpeta superior|ve a la carpeta superior|sube",
  en="go up|up one level|parent folder|go up one level|go to the parent folder",
  detto=("Su di un livello.", "Un niveau plus haut.", "Un nivel arriba.", "Going up one level."))

# --- TESTO ----------------------------------------------------------------
C("testo_copia", "Testo", "tasti:ctrl+c",
  it="copia|copia questo|copia il testo|copialo|copia la selezione",
  fr="copie|copie ca|copie le texte|copie la selection",
  es="copia|copia esto|copia el texto|copia la seleccion",
  en="copy|copy this|copy the text|copy that|copy the selection",
  detto=("Copiato.", "Copié.", "Copiado.", "Copied."))
C("testo_incolla", "Testo", "tasti:ctrl+v",
  it="incolla|incolla qui|incolla il testo|incollalo",
  fr="colle|colle ici|colle le texte|colle ca",
  es="pega|pega aqui|pega el texto|pegalo",
  en="paste|paste here|paste the text|paste it",
  detto=("Incollato.", "Collé.", "Pegado.", "Pasted."))
C("testo_taglia", "Testo", "tasti:ctrl+x",
  it="taglia|taglia questo|taglia il testo|tagliala|taglia la selezione",
  fr="coupe|coupe ca|coupe le texte|coupe la selection",
  es="corta|corta esto|corta el texto|corta la seleccion",
  en="cut|cut this|cut that|cut the text|cut the selection",
  detto=("Tagliato.", "Coupé.", "Cortado.", "Cut."))
C("testo_seleziona_tutto", "Testo", "tasti:ctrl+a",
  it="seleziona tutto|seleziona tutto il testo|seleziona ogni cosa|selezionare tutto",
  fr="selectionne tout|tout selectionner|selectionne tout le texte",
  es="selecciona todo|seleccionar todo|selecciona todo el texto",
  en="select all|select everything|select all the text",
  detto=("Tutto selezionato.", "Tout sélectionné.", "Todo seleccionado.", "Everything selected."))
C("testo_annulla", "Testo", "tasti:ctrl+z",
  it="annulla|annulla l'ultima azione|annulla l'ultima modifica|annulla questo|torna all'azione precedente",
  fr="annule|annule la derniere action|annule la derniere modification|annuler",
  es="deshaz|deshacer|deshaz la ultima accion|deshaz el ultimo cambio",
  en="undo|undo that|undo the last action|undo the last change",
  detto=("Annullato.", "Annulé.", "Deshecho.", "Undone."))
C("testo_ripeti", "Testo", "tasti:ctrl+y",
  it="ripeti l'azione|ripeti l'ultima azione|rifai|rifai l'azione|ripristina l'azione",
  fr="refais|refais l'action|retablis l'action|repete l'action",
  es="rehaz|rehacer|rehaz la accion|repite la accion",
  en="redo|redo that|redo the last action|redo the action",
  detto=("Rifatto.", "Rétabli.", "Rehecho.", "Redone."))
C("testo_salva", "Testo", "tasti:ctrl+s",
  it="salva|salva il file|salva il documento|salva questo file|salva questo documento",
  fr="enregistre|sauvegarde|enregistre le fichier|sauvegarde le document|enregistre le document",
  es="guarda|guarda el archivo|guardar|guarda el documento|guarda este archivo",
  en="save|save the file|save this file|save the document|save this document",
  detto=("Salvo.", "J'enregistre.", "Guardo.", "Saving."))

# --- BROWSER --------------------------------------------------------------
C("scheda_nuova", "Browser", "tasti:ctrl+t",
  it="nuova scheda|apri una nuova scheda|apri una scheda|apri una nuova tab|nuova tab",
  fr="nouvel onglet|ouvre un nouvel onglet|ouvre un onglet",
  es="nueva pestana|abre una nueva pestana|abre una pestana",
  en="new tab|open a new tab|open new tab",
  detto=("Nuova scheda.", "Nouvel onglet.", "Nueva pestaña.", "New tab."))
C("scheda_chiudi", "Browser", "tasti:ctrl+w",
  it="chiudi scheda|chiudi questa scheda|chiudi la scheda|chiudi la scheda attiva|chiudi la tab",
  fr="ferme l'onglet|ferme cet onglet|ferme l'onglet actuel|ferme onglet",
  es="cierra la pestana|cierra esta pestana|cierra pestana|cierra la pestana actual",
  en="close tab|close this tab|close the tab|close the current tab",
  detto=("Scheda chiusa.", "Onglet fermé.", "Pestaña cerrada.", "Tab closed."))
C("scheda_successiva", "Browser", "tasti:ctrl+tab",
  it="scheda successiva|prossima scheda|passa alla scheda successiva|vai alla scheda successiva",
  fr="onglet suivant|passe a l'onglet suivant|prochain onglet",
  es="pestana siguiente|siguiente pestana|pasa a la pestana siguiente",
  en="next tab|switch to the next tab|go to the next tab",
  detto=("Scheda successiva.", "Onglet suivant.", "Pestaña siguiente.", "Next tab."))
C("scheda_precedente", "Browser", "tasti:ctrl+shift+tab",
  it="scheda precedente|passa alla scheda precedente|vai alla scheda precedente",
  fr="onglet precedent|passe a l'onglet precedent",
  es="pestana anterior|anterior pestana|pasa a la pestana anterior",
  en="previous tab|go to the previous tab|switch to the previous tab",
  detto=("Scheda precedente.", "Onglet précédent.", "Pestaña anterior.", "Previous tab."))
C("browser_ricarica", "Browser", "tasti:f5",
  it="ricarica|ricarica la pagina|aggiorna la pagina|aggiorna pagina|ricarica pagina",
  fr="recharge la page|actualise la page|recharge|actualise",
  es="recarga la pagina|actualiza la pagina|recarga|actualiza",
  en="reload|reload the page|refresh|refresh the page",
  detto=("Ricarico.", "Je recharge.", "Recargo.", "Reloading."))
C("browser_indietro", "Browser", "tasti:alt+left",
  it="pagina indietro|torna alla pagina precedente|indietro di una pagina|pagina precedente",
  fr="page precedente|reviens a la page precedente|retour a la page precedente",
  es="pagina anterior|vuelve a la pagina anterior|pagina atras",
  en="previous page|go back a page|page back|go back one page",
  detto=("Pagina precedente.", "Page précédente.", "Página anterior.", "Previous page."))
C("browser_avanti", "Browser", "tasti:alt+right",
  it="pagina avanti|vai avanti|avanti|vai alla pagina successiva|pagina successiva",
  fr="page suivante|avance|va a la page suivante",
  es="pagina siguiente|ve adelante|avanza|ve a la pagina siguiente",
  en="next page|go forward|page forward|go forward one page",
  detto=("Pagina successiva.", "Page suivante.", "Página siguiente.", "Next page."))

# --- SCHERMO E SUONO ---------------------------------------------------------
C("volume_su", "Schermo e suono", "volume:su",
  it="alza il volume|aumenta il volume|volume su|piu forte|alza il suono",
  fr="monte le volume|augmente le volume|plus fort|monte le son",
  es="sube el volumen|aumenta el volumen|mas fuerte|sube el sonido",
  en="volume up|turn up the volume|raise the volume|louder|turn the volume up",
  detto=("Volume alzato.", "Volume augmenté.", "Volumen subido.", "Volume up."))
C("volume_giu", "Schermo e suono", "volume:giu",
  it="abbassa il volume|diminuisci il volume|volume giu|piu piano|abbassa il suono",
  fr="baisse le volume|diminue le volume|moins fort|baisse le son",
  es="baja el volumen|reduce el volumen|mas bajo|baja el sonido",
  en="volume down|turn down the volume|lower the volume|quieter|turn the volume down",
  detto=("Volume abbassato.", "Volume baissé.", "Volumen bajado.", "Volume down."))
C("audio_muto", "Schermo e suono", "volume:muto",
  it="silenzia|silenzia l'audio|muto|metti in muto|disattiva l'audio|silenzia il suono",
  fr="coupe le son|mets en sourdine|muet|coupe l'audio",
  es="silencia|silencia el audio|quita el sonido|silenciar|silencia el sonido",
  en="mute|mute the sound|mute audio|mute the audio|silence the audio",
  detto=("Audio disattivato.", "Son coupé.", "Audio silenciado.", "Sound muted."))
C("audio_riattiva", "Schermo e suono", "volume:togli_muto",
  it="riattiva l'audio|togli il muto|riattiva il suono|riattiva l'audio per favore",
  fr="remets le son|reactive le son|reactive l'audio|enleve le mode muet",
  es="activa el sonido|reactiva el sonido|quita el silencio|reactiva el audio",
  en="unmute|unmute the sound|turn the sound on|unmute audio",
  detto=("Audio riattivato.", "Son réactivé.", "Audio reactivado.", "Sound back on."))
C("schermata", "Schermo e suono", "schermata:",
  it="schermata|fai una schermata|cattura lo schermo|fai uno screenshot|screenshot|fai una cattura dello schermo",
  fr="capture d'ecran|fais une capture d'ecran|prends une capture d'ecran|capture l'ecran|screenshot",
  es="captura de pantalla|haz una captura de pantalla|haz una captura|pantallazo|screenshot",
  en="screenshot|take a screenshot|take a screen capture|capture the screen|take a picture of the screen",
  detto=("Schermata salvata in Immagini, Schermate.", "Capture d'écran enregistrée dans Images, Screenshots.",
         "Captura guardada en Imágenes, Screenshots.", "Screenshot saved in Pictures, Screenshots."))

# --- SCORRIMENTO ---------------------------------------------------------------
C("scorri_su", "Scorrimento", "scroll:700",
  it="scorri su|scorri verso l'alto|scorri in alto|scorri la pagina in su",
  fr="fais defiler vers le haut|defile vers le haut|defile en haut|monte la page",
  es="desplazate hacia arriba|desplaza hacia arriba|sube la pagina|desplaza arriba",
  en="scroll up|scroll upwards|scroll the page up|page up",
  detto=("Scorro su.", "Je défile vers le haut.", "Subo.", "Scrolling up."))
C("scorri_giu", "Scorrimento", "scroll:-700",
  it="scorri giu|scorri verso il basso|scorri in basso|scorri la pagina in giu",
  fr="fais defiler vers le bas|defile vers le bas|defile en bas|descends la page",
  es="desplazate hacia abajo|desplaza hacia abajo|baja la pagina|desplaza abajo",
  en="scroll down|scroll downwards|scroll the page down|page down",
  detto=("Scorro giù.", "Je défile vers le bas.", "Bajo.", "Scrolling down."))
C("vai_inizio", "Scorrimento", "tasti:ctrl+home",
  it="vai all'inizio|torna all'inizio|vai in cima|vai all'inizio della pagina|vai all'inizio del documento",
  fr="va au debut|aller au debut|retourne au debut|va tout en haut|va en haut de la page",
  es="ve al principio|ir al principio|ve arriba del todo|ve al inicio|ve al comienzo",
  en="go to the top|go to the beginning|go to the start|go to the top of the page",
  detto=("All'inizio.", "Au début.", "Al principio.", "At the top."))
C("vai_fine", "Scorrimento", "tasti:ctrl+end",
  it="vai alla fine|vai in fondo|vai alla fine della pagina|vai alla fine del documento|vai in fondo alla pagina",
  fr="va a la fin|aller a la fin|va tout en bas|va en bas de la page",
  es="ve al final|ir al final|ve abajo del todo|ve al fondo",
  en="go to the bottom|go to the end|go to the bottom of the page|go to the end of the page",
  detto=("Alla fine.", "À la fin.", "Al final.", "At the bottom."))

# --- SISTEMA ---------------------------------------------------------------------
C("blocca_pc", "Sistema", "blocca:",
  it="blocca il pc|blocca il computer|blocca lo schermo|blocca la sessione|blocca il pc per favore",
  fr="verrouille le pc|verrouille l'ordinateur|verrouille la session|bloque l'ordinateur|verrouille l'ecran",
  es="bloquea el pc|bloquea el ordenador|bloquea la pantalla|bloquea el equipo|bloquea la sesion",
  en="lock the pc|lock the computer|lock the screen|lock my pc|lock my computer",
  detto=("Blocco il PC.", "Je verrouille le PC.", "Bloqueo el PC.", "Locking the PC."))
C("gestore_attivita", "Sistema", "taskmgr:",
  it="apri il gestore attivita|apri gestore attivita|apri la gestione attivita|apri il task manager|task manager|gestore attivita",
  fr="ouvre le gestionnaire des taches|ouvre le gestionnaire de taches|gestionnaire des taches|ouvre le task manager",
  es="abre el administrador de tareas|administrador de tareas|abre el administrador de tareas de windows",
  en="open task manager|open the task manager|task manager|launch task manager",
  detto=("Apro il Gestore attività.", "J'ouvre le Gestionnaire des tâches.", "Abro el Administrador de tareas.", "Opening Task Manager."))
C("annulla_spegnimento", "Sistema", "annulla_spegnimento:",
  it="annulla lo spegnimento|annulla spegnimento|annulla il riavvio|annulla il riavvio del pc|fermati non spegnere",
  fr="annule l'extinction|annule le redemarrage|annule l'arret|arrete l'extinction",
  es="cancela el apagado|cancela el reinicio|cancela apagado|anula el apagado",
  en="cancel the shutdown|cancel shutdown|cancel the restart|abort shutdown|stop the shutdown",
  detto=("Spegnimento annullato.", "Extinction annulée.", "Apagado cancelado.", "Shutdown cancelled."))

# --- AZIONI PERICOLOSE: chiedono conferma a voce prima di partire -------------------
C("spegni_pc", "Sistema (pericoloso)", "spegni:",
  it="spegni il pc|spegni il computer|spegni il pc per favore|arresta il pc|arresta il sistema|spegni windows",
  fr="eteins le pc|eteins l'ordinateur|arrete le pc|eteins l'ordinateur s'il te plait|eteins windows",
  es="apaga el pc|apaga el ordenador|apaga el equipo|apaga windows",
  en="shut down the pc|shut down the computer|turn off the pc|turn off the computer|shutdown|shut down",
  pericoloso=True, cosa=("spegnere il PC", "éteindre le PC", "apagar el PC", "shut down the PC"),
  detto=("Spengo il PC tra venti secondi. Dì annulla lo spegnimento per fermarmi.",
         "J'éteins le PC dans vingt secondes. Dis annule l'extinction pour m'arrêter.",
         "Apago el PC en veinte segundos. Di cancela el apagado para detenerme.",
         "Shutting down in twenty seconds. Say cancel the shutdown to stop me."))
C("riavvia_pc", "Sistema (pericoloso)", "riavvia:",
  it="riavvia il pc|riavvia il computer|riavvia|riavvia windows|riavvia il sistema",
  fr="redemarre le pc|redemarre l'ordinateur|redemarre|redemarre windows",
  es="reinicia el pc|reinicia el ordenador|reinicia|reinicia windows|reinicia el equipo",
  en="restart the pc|restart the computer|restart|reboot|reboot the pc",
  pericoloso=True, cosa=("riavviare il PC", "redémarrer le PC", "reiniciar el PC", "restart the PC"),
  detto=("Riavvio il PC tra venti secondi. Dì annulla il riavvio per fermarmi.",
         "Je redémarre le PC dans vingt secondes. Dis annule le redémarrage pour m'arrêter.",
         "Reinicio el PC en veinte segundos. Di cancela el reinicio para detenerme.",
         "Restarting in twenty seconds. Say cancel the restart to stop me."))
C("sospendi_pc", "Sistema (pericoloso)", "sospendi:",
  it="metti il pc in sospensione|sospendi il pc|sospendi il computer|sospendi|metti il computer in stop",
  fr="mets le pc en veille|mets l'ordinateur en veille|mets en veille|veille",
  es="suspende el pc|pon el pc en suspension|suspende el ordenador|suspende|pon el ordenador en suspension",
  en="put the pc to sleep|put the computer to sleep|sleep|suspend the pc|suspend",
  pericoloso=True, cosa=("mettere il PC in sospensione", "mettre le PC en veille", "suspender el PC", "put the PC to sleep"),
  detto=("Metto il PC in sospensione.", "Je mets le PC en veille.", "Suspendo el PC.", "Putting the PC to sleep."))
C("esci_account", "Sistema (pericoloso)", "esci:",
  it="esci dall'account|esci da windows|disconnettiti|disconnetti l'utente|chiudi la sessione di windows",
  fr="deconnecte moi|deconnecte la session|ferme la session|deconnexion",
  es="cierra la sesion|cierra sesion|desconectate|cerrar sesion",
  en="sign out|log out|log me out|sign me out",
  pericoloso=True, cosa=("uscire dall'account", "fermer la session", "cerrar la sesión", "sign out"),
  detto=("Esco dall'account.", "Je ferme la session.", "Cierro la sesión.", "Signing out."))
C("elimina", "Sistema (pericoloso)", "elimina:",
  it="elimina|elimina questo|elimina il file|elimina l'elemento selezionato|elimina la selezione|cancella|cancella questo|cancella il file",
  fr="supprime|supprime ca|supprime le fichier|supprime l'element selectionne|efface|efface ca",
  es="elimina|elimina esto|elimina el archivo|elimina el elemento seleccionado|borra|borra esto|borra el archivo",
  en="delete|delete this|delete the file|delete it|delete the selected item|delete that",
  pericoloso=True, cosa=("eliminare l'elemento selezionato", "supprimer l'élément sélectionné",
                         "eliminar el elemento seleccionado", "delete the selected item"),
  detto=("Elemento eliminato.", "Élément supprimé.", "Elemento eliminado.", "Item deleted."))
C("svuota_cestino", "Sistema (pericoloso)", "svuota_cestino:",
  it="svuota il cestino|svuota cestino|svuota il cestino per favore|vuota il cestino",
  fr="vide la corbeille|vide la corbeille s'il te plait|vider la corbeille",
  es="vacia la papelera|vaciar la papelera|vacia la papelera por favor",
  en="empty the recycle bin|empty the trash|empty recycle bin|empty the bin",
  pericoloso=True, cosa=("svuotare il cestino", "vider la corbeille", "vaciar la papelera", "empty the recycle bin"),
  detto=("Cestino svuotato.", "Corbeille vidée.", "Papelera vaciada.", "Recycle bin emptied."))


# ======================================================================
# Frasi generiche
# ======================================================================
def _l(d):
    return d.get(lingue.corrente) or d["it"]


DETTO_OK = {"it": "Fatto.", "fr": "C'est fait.", "es": "Hecho.", "en": "Done."}
DETTO_KO = {"it": "Non ci sono riuscita.", "fr": "Je n'y suis pas arrivée.", "es": "No lo he conseguido.",
            "en": "I couldn't do it."}
DETTO_NIENTE = {"it": "Non c'è nessuna finestra su cui agire.", "fr": "Il n'y a aucune fenêtre sur laquelle agir.",
                "es": "No hay ninguna ventana sobre la que actuar.", "en": "There is no window to act on."}
DETTO_NESSUNO_SPEGNIMENTO = {"it": "Non c'è nessuno spegnimento da annullare.", "fr": "Il n'y a aucune extinction à annuler.",
                             "es": "No hay ningún apagado que cancelar.", "en": "There is no shutdown to cancel."}
DETTO_NON_APERTO = {"it": "Non ho trovato niente da chiudere.", "fr": "Je n'ai rien trouvé à fermer.",
                    "es": "No he encontrado nada que cerrar.", "en": "I found nothing to close."}
DOMANDA = {"it": "Vuoi davvero {cosa}? Dì sì per confermare, oppure no.",
           "fr": "Veux-tu vraiment {cosa} ? Dis oui pour confirmer, ou non.",
           "es": "¿De verdad quieres {cosa}? Di sí para confirmar, o no.",
           "en": "Do you really want to {cosa}? Say yes to confirm, or no."}
ANNULLATO = {"it": "Va bene, non faccio niente.", "fr": "D'accord, je ne fais rien.",
             "es": "De acuerdo, no hago nada.", "en": "Alright, I'm not doing anything."}
SCADUTA = {"it": "Nessuna risposta: non faccio niente.", "fr": "Pas de réponse : je ne fais rien.",
           "es": "Sin respuesta: no hago nada.", "en": "No answer: I'm not doing anything."}


def domanda_conferma(cmd):
    i = LINGUE.index(lingue.corrente)
    return _l(DOMANDA).format(cosa=cmd.cosa[i] if cmd.cosa else cmd.id)


def detto(cmd):
    i = LINGUE.index(lingue.corrente)
    return cmd.detto[i] if cmd.detto else _l(DETTO_OK)


# ======================================================================
# Riconoscere la frase
# ======================================================================
_INIZIO = re.compile(
    r"^(?:(?:eva|per favore|per piacere|per cortesia|puoi|potresti|puoi per favore|vorrei che tu|vorrei che|voglio che tu|"
    r"voglio che|ora|adesso|allora|dai|ecco|mi|ti|ci|"
    r"s il te plait|s il vous plait|peux tu|peux tu me|pourrais tu|pourrais tu me|tu peux|vous pouvez|pouvez vous|"
    r"pouvez vous me|pourriez vous|pourriez vous me|maintenant|alors|"
    r"por favor|puedes|podrias|me puedes|me podrias|quiero que|ahora|entonces|pues|vale|"
    r"please|can you|could you|would you|will you|i want you to|i would like you to|i d like you to|now|just|"
    r"go ahead and)\s+)+")
_FINE = re.compile(
    r"(?:\s+(?:per favore|per piacere|per cortesia|grazie|ora|adesso|s il te plait|s il vous plait|stp|svp|merci|"
    r"maintenant|por favor|gracias|ahora|please|thanks|thank you|now))+$")

_INDICE = {}                 # frase normalizzata -> comando (tutte le lingue)
_FUZZY = []                  # (frase, comando) per il confronto approssimato


def _costruisci():
    _INDICE.clear()
    _FUZZY.clear()
    doppioni = []
    for cmd in TABELLA:
        for lg in LINGUE:
            for f in cmd.frasi[lg]:
                if f in _INDICE and _INDICE[f] is not cmd:
                    doppioni.append((f, _INDICE[f].id, cmd.id))
                    continue
                _INDICE[f] = cmd
                if not cmd.pericoloso and len(f) >= 10:
                    _FUZZY.append((f, cmd))
    return doppioni


DOPPIONI = _costruisci()


def ripulisci(testo):
    t = lingue.norm(testo)
    t = _INIZIO.sub("", t)
    t = _FINE.sub("", t).strip()
    return t


def trova(testo):
    """Il comando della libreria che corrisponde alla frase (o None). Per i comandi pericolosi la frase deve
    essere identica a una della tabella; per gli altri basta che somigli molto (0,9)."""
    if not testo:
        return None
    t0 = lingue.norm(testo)
    for t in (ripulisci(testo), t0):
        if t and t in _INDICE:
            return _INDICE[t]
    t = ripulisci(testo)
    if len(t) >= 10:
        migliore, voto = None, 0.0
        for f, cmd in _FUZZY:
            if abs(len(f) - len(t)) > 4:
                continue
            r = difflib.SequenceMatcher(None, t, f).ratio()
            if r > voto:
                migliore, voto = cmd, r
        if voto >= 0.9:
            return migliore
    return None


_SI = {"si", "conferma", "confermo", "procedi", "ok", "okay", "certo", "va", "bene", "esegui", "vai", "fallo", "pure",
       "oui", "confirme", "je", "d", "accord", "vas", "y", "fais", "le", "procede", "bien", "sur",
       "vale", "confirma", "adelante", "hazlo", "de", "acuerdo", "claro", "por", "favor", "venga",
       "yes", "yeah", "yep", "confirm", "i", "go", "ahead", "do", "it", "proceed", "sure", "please", "eva"}
_SI_CENTRO = {"si", "conferma", "confermo", "procedi", "ok", "okay", "certo", "esegui", "fallo", "oui", "confirme",
              "procede", "vale", "confirma", "adelante", "hazlo", "claro", "venga", "yes", "yeah", "yep", "confirm",
              "proceed", "sure", "d", "accord"}
_NO = {"no", "non", "annulla", "annule", "anula", "cancela", "cancel", "stop", "nope", "niente", "nada", "rien",
       "nothing", "lascia", "stare", "perdere", "laisse", "tomber", "deja", "never", "mind", "ferma", "fermati",
       "arrete", "para", "detente", "annuler", "cancelar"}


def e_no(frase):
    toks = lingue.norm(frase).split()
    return any(t in _NO for t in toks)


_SI_FRASI = {"go ahead", "do it", "yes please", "vas y", "fais le", "d accord", "de acuerdo", "va bene", "ok va bene",
             "si certo", "si conferma", "oui je confirme", "yes confirm", "si por favor", "oui s il te plait",
             "si grazie", "sure go ahead"}


def e_si(frase):
    """True solo se la risposta è un chiaro "sì" (e non contiene niente che somigli a un "no")."""
    toks = lingue.norm(frase).split()
    if not toks or any(t in _NO for t in toks) or len(toks) > 5:
        return False
    if " ".join(toks) in _SI_FRASI:
        return True
    return all(t in _SI for t in toks) and any(t in _SI_CENTRO for t in toks)


def ids_sicuri():
    """Gli id dei comandi NON pericolosi (quelli che Groq può proporre)."""
    return [c.id for c in TABELLA if not c.pericoloso]


def elenco_per_groq():
    return ", ".join(ids_sicuri())


def per_id(id):
    for c in TABELLA:
        if c.id == id:
            return c
    return None


# ======================================================================
# Eseguire
# ======================================================================
# copia PRIVATA di user32: ctypes.windll.user32 è condivisa con azioni.py e cambiarle i tipi degli argomenti la rompe
_user32 = ctypes.WinDLL("user32")
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
SHELL = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
_pronte = {"pyautogui": None}


def precarica():
    """Carica in anticipo (in secondo piano) pyautogui, pywinauto e keyboard: la prima volta sono lente."""
    try:
        import keyboard  # noqa: F401
        import pyautogui
        pyautogui.FAILSAFE = False          # il mouse nell'angolo non deve bloccare i comandi
        pyautogui.PAUSE = 0.02
        _pronte["pyautogui"] = pyautogui
        from pywinauto.controls.hwndwrapper import HwndWrapper  # noqa: F401
        return True
    except Exception:
        return False


def _pag():
    if _pronte["pyautogui"] is None:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.02
        _pronte["pyautogui"] = pyautogui
    return _pronte["pyautogui"]


def _pid_eva():
    """Il processo di Eva: le sue finestre (la sfera) non vengono mai toccate dai comandi."""
    return os.getpid()


def _info(h):
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    cls, tit = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(512)
    _user32.GetClassNameW(h, cls, 256)
    _user32.GetWindowTextW(h, tit, 512)
    return pid.value, cls.value, tit.value


def finestra_attiva():
    """La finestra in primo piano (mai quella di Eva, mai il desktop o la barra delle applicazioni)."""
    h = _user32.GetForegroundWindow()
    if not h:
        return None
    pid, cls, _ = _info(h)
    if pid == _pid_eva() or cls in SHELL:
        return None
    return h


def _esegui_finestra(op):
    h = finestra_attiva()
    if h is None:
        return False, _l(DETTO_NIENTE)
    from pywinauto.controls.hwndwrapper import HwndWrapper
    w = HwndWrapper(h)
    {"maximize": w.maximize, "minimize": w.minimize, "restore": w.restore, "close": w.close}[op]()
    return True, None


def _finestre_visibili():
    trovate = []

    def cb(h, _):
        if _user32.IsWindowVisible(h):
            pid, cls, tit = _info(h)
            if pid != _pid_eva() and tit:
                trovate.append((h, pid, cls, tit))
        return True
    _user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return trovate


_TITOLI = {"calcolatrice": ("calcolatrice", "calculator", "calculatrice", "calculadora"),
           "impostazioni": ("impostazioni", "settings", "parametres", "paramètres", "configuracion", "configuración")}


def chiudi_programma(chi):
    """Chiude (con la richiesta normale di chiusura, non a forza) le finestre di un programma. Restituisce quante."""
    import psutil
    chiuse = 0
    for h, pid, cls, tit in _finestre_visibili():
        ok = False
        if chi == "notepad":
            try:
                ok = psutil.Process(pid).name().lower() == "notepad.exe"
            except Exception:
                ok = cls == "Notepad"
        elif chi == "esplora":
            ok = cls == "CabinetWClass"
        elif chi in _TITOLI:
            t = tit.lower()
            ok = cls == "ApplicationFrameWindow" and any(t == x or t.startswith(x) for x in _TITOLI[chi])
        if ok:
            _user32.PostMessageW(h, 0x0010, 0, 0)          # WM_CLOSE
            chiuse += 1
    return chiuse


def _cartella_screenshot():
    p = Path.home() / "Pictures" / "Screenshots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def esegui(cmd, prova=None):
    """Esegue il comando. Restituisce (riuscito, frase da dire). Con prova=True (o SOLO_PROVA) i comandi di sistema
    (spegni, riavvia, blocca, gestore attività, elimina...) NON partono davvero: si limita a dire cosa farebbe."""
    prova = SOLO_PROVA if prova is None else prova
    tipo, _, arg = cmd.azione.partition(":")
    ok_detto = detto(cmd)
    try:
        if tipo == "tasti":
            if prova and cmd.pericoloso:
                return True, "(prova) " + ok_detto
            _pag().hotkey(*arg.split("+")) if "+" in arg else _pag().press(arg)
            return True, ok_detto
        if tipo == "tasti_win":
            import keyboard
            keyboard.send(arg)
            return True, ok_detto
        if tipo == "finestra":
            ok, msg = _esegui_finestra(arg)
            return ok, (ok_detto if ok else msg)
        if tipo == "avvia":
            subprocess.Popen([arg])
            return True, ok_detto
        if tipo == "url":
            os.startfile(arg)
            return True, ok_detto
        if tipo == "browser":
            webbrowser.open("about:blank")
            return True, ok_detto
        if tipo == "chiudi_prog":
            n = chiudi_programma(arg)
            return (n > 0), (ok_detto if n else _l(DETTO_NON_APERTO))
        if tipo == "chiudi_browser":
            import azioni
            scelta, msg = azioni.chiudi_browser(prova=prova)
            return (scelta is not None), (ok_detto if scelta is not None else _l(DETTO_NON_APERTO))
        if tipo == "cartella":
            import azioni
            r = azioni.interpreta(f"apri {arg}", not prova)
            ok = getattr(r, "tipo", "") == "eseguito"
            return ok, (ok_detto if ok else _l(DETTO_KO))
        if tipo in ("indietro_cartella", "su_cartella"):
            import azioni
            r = azioni.interpreta("torna indietro" if tipo == "indietro_cartella" else "vai su", not prova)
            if getattr(r, "tipo", "") == "eseguito":
                return True, ok_detto
            if finestra_attiva() is not None:                   # non era una cartella aperta da Eva: tasti di Esplora file
                _pag().hotkey("alt", "left" if tipo == "indietro_cartella" else "up")
                return True, ok_detto
            return False, _l(DETTO_NIENTE)
        if tipo == "scroll":
            _pag().scroll(int(arg))
            return True, ok_detto
        if tipo == "volume":
            import azioni
            f = {"su": azioni.alza_volume, "giu": azioni.abbassa_volume, "muto": azioni.metti_muto,
                 "togli_muto": azioni.togli_muto}[arg]
            f()
            return True, ok_detto
        if tipo == "schermata":
            nome = _cartella_screenshot() / time.strftime("Eva_%Y%m%d_%H%M%S.png")
            _pag().screenshot(str(nome))
            return nome.exists(), ok_detto
        if prova:
            return True, "(prova) " + ok_detto
        if tipo == "blocca":
            return bool(_user32.LockWorkStation()), ok_detto
        if tipo == "taskmgr":
            subprocess.Popen(["taskmgr.exe"])
            return True, ok_detto
        if tipo == "annulla_spegnimento":
            r = subprocess.run(["shutdown", "/a"], capture_output=True)
            if r.returncode == 1116:                           # 1116 = nessuno spegnimento in corso
                return True, _l(DETTO_NESSUNO_SPEGNIMENTO)
            return r.returncode == 0, (ok_detto if r.returncode == 0 else _l(DETTO_KO))
        if tipo == "spegni":
            subprocess.run(["shutdown", "/s", "/t", "20", "/c", "Eva: spegnimento richiesto a voce"], check=True)
            return True, ok_detto
        if tipo == "riavvia":
            subprocess.run(["shutdown", "/r", "/t", "20", "/c", "Eva: riavvio richiesto a voce"], check=True)
            return True, ok_detto
        if tipo == "sospendi":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            return True, ok_detto
        if tipo == "esci":
            subprocess.run(["shutdown", "/l"], check=True)
            return True, ok_detto
        if tipo == "elimina":
            _pag().press("delete")             # nel Cestino se è un file di Esplora file
            return True, ok_detto
        if tipo == "svuota_cestino":
            r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)   # senza conferma, senza barra, senza suono
            return r in (0, 1), ok_detto                                     # 0 = ok; 1 = era già vuoto
    except Exception as e:
        return False, _l(DETTO_KO) + f" ({type(e).__name__})"
    return False, _l(DETTO_KO)
