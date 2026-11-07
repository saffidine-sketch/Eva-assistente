"""La finestra di Eva (PySide6): sfera, titolo, stato, ultima frase, icona vicino all'orologio.

La finestra ha tre aspetti:
  - GRANDE   : al centro dello schermo (dove si trova il cursore), opaca, si può trascinare col mouse;
  - RIDOTTA  : piccola (un quarto), in un angolo sopra la barra delle applicazioni, semitrasparente, i clic del
               mouse PASSANO ATTRAVERSO (non intercetta niente, non ruba il focus) e mostra solo sfera e stato;
  - NASCOSTA : invisibile, ma Eva continua ad ascoltare.
Il passaggio tra un aspetto e l'altro è un'animazione fluida di circa mezzo secondo.

Serve la libreria PySide6:   python -m pip install PySide6-Essentials
Se manca, dettatura.py parte lo stesso senza finestra.
"""
import ctypes
import json
import os
import queue
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from vox_sfera import Sfera

LARGHEZZA, ALTEZZA = 760, 600          # finestra GRANDE (la sfera è calcolata a metà e ingrandita)
DURATA_COMPARSA = 1.1                  # secondi: la sfera cresce da piccola (prima apparizione)
DURATA_DISSOLVENZA = 0.35              # secondi: la finestra sparisce (veloce: si deve vedere subito quello che Eva ha aperto)
DURATA_ANIMAZIONE = 0.5                # secondi: da grande a ridotta e viceversa
OPACITA_GRANDE = 0.999                 # (non 1.0: così la finestra resta "a strati" e i clic possono passare attraverso)
FILE_IMPOSTAZIONI = Path(__file__).with_name("impostazioni_eva.json")

AZZURRO = QColor(150, 200, 255)

# --- Windows: stili della finestra (clic che passano attraverso, mai il focus) ---------------------------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_APPWINDOW = 0x00040000


def _user32():
    return ctypes.windll.user32 if os.name == "nt" else None


def stile_esteso(hwnd):
    """Stile esteso attuale della finestra (numero) oppure None."""
    u = _user32()
    if u is None:
        return None
    try:
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongW.restype = ctypes.c_long
        return u.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    except Exception:
        return None


def modifica_stile(hwnd, aggiungi=0, togli=0):
    u = _user32()
    if u is None:
        return False
    try:
        attuale = stile_esteso(hwnd)
        if attuale is None:
            return False
        nuovo = (attuale | aggiungi) & ~togli
        if nuovo != attuale:
            u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            u.SetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE, ctypes.c_long(nuovo))
        return True
    except Exception:
        return False


def _liscia(k):
    k = max(0.0, min(1.0, k))
    return k * k * (3 - 2 * k)


def _limita(x, a, b):
    return max(a, min(b, x))


FILE_CHIUSURA_VOLONTARIA = Path(__file__).with_name("chiusura_volontaria.flag")


class BottoneX(QWidget):
    """Piccolo pulsante X, finestra a parte (la sfera ridotta lascia passare i clic, lui no).
    Un clic chiude Eva del tutto, senza conferma."""
    DIM = 22

    def __init__(self, finestra):
        super().__init__()
        self.finestra = finestra
        self._sopra = False
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Chiudi Eva")
        self.resize(self.DIM, self.DIM)

    def enterEvent(self, e):
        self._sopra = True
        self.setToolTip("Doppio clic per chiudere Eva" if self.finestra.vista == "schermo" else "Chiudi Eva")
        self.update()

    def leaveEvent(self, e):
        self._sopra = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.finestra.vista == "schermo":
                return                       # benvenuto a schermo intero: un clic solo NON chiude (serve il doppio clic)
            self.finestra.chiudi_per_utente()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.finestra.chiudi_per_utente()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(QColor(200, 50, 60, 230) if self._sopra else QColor(8, 16, 44, 200))
        p.setPen(QPen(QColor(150, 200, 255, 200), 1.2))
        p.drawEllipse(1, 1, self.DIM - 2, self.DIM - 2)
        p.setPen(QPen(QColor(235, 245, 255), 1.8))
        m = 7
        p.drawLine(m, m, self.DIM - m, self.DIM - m)
        p.drawLine(self.DIM - m, m, m, self.DIM - m)
        p.end()


class Finestra(QWidget):
    def __init__(self, vox, log=print):
        super().__init__()
        self.vox = vox
        self.log = log
        self.bottone_x = BottoneX(self)
        self._chiusura_utente = False
        imp = getattr(vox, "imp", {}) or {}
        self.opacita_ridotta = float(imp.get("OPACITA_RIDOTTA", 0.55))
        self.scala_ridotta = float(imp.get("SCALA_RIDOTTA", 0.25))
        self.margine = int(imp.get("MARGINE", 24))
        self.setWindowTitle("Eva")
        # senza bordi, sempre in primo piano, senza icona nella barra delle applicazioni, senza rubare il focus
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(LARGHEZZA, ALTEZZA)

        self.sfera = Sfera(punti=2000, larghezza=LARGHEZZA // 2, altezza=ALTEZZA // 2)
        # stessa sfera (stessi puntini) ma con un quarto dei pixel: serve quando la finestra è piccola e costa meno
        self.sfera_leggera = Sfera(punti=2000, larghezza=LARGHEZZA // 4, altezza=ALTEZZA // 4)
        self._sfera_in_uso = self.sfera
        self.sfera_schermo = None        # sfera con le proporzioni dello schermo (benvenuto a schermo intero)
        self._immagine_dati = None
        self.visibile = False
        self.vista = "nascosta"          # schermo | grande | ridotta | nascosta
        self.t_inizio = 0.0              # quando è comparsa (la sfera cresce da piccola)
        self.t_chiusura = None           # quando deve iniziare a sparire
        self.t_dissolvenza = None        # quando ha iniziato a sparire
        self.stato = "IN ASCOLTO"
        self.progresso = None            # percentuale di una copia in corso (0-100) oppure None
        self.frase = ""
        self.nota = ""
        self._anim = None                # animazione in corso (grande <-> ridotta)
        self._trascina = None            # trascinamento col mouse (finestra grande)
        self._schermo_scelto = None
        self.posizione = self._carica_posizione(imp.get("ANGOLO_PREDEFINITO", "basso_destra"))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)            # a riposo controlla gli eventi 10 volte al secondo

    # --- posizione scelta (angolo o punto) salvata in impostazioni_eva.json ---------------------------------
    @staticmethod
    def _angolo_da_nome(nome):
        parti = str(nome).split("_")
        v = "alto" if "alto" in parti else "basso"
        h = "sinistra" if "sinistra" in parti else "destra"
        return {"tipo": "angolo", "v": v, "h": h}

    def _carica_posizione(self, predefinito):
        pos = self._angolo_da_nome(predefinito)
        try:
            dati = json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
            p = dati.get("posizione", {})
            if p.get("tipo") == "angolo" and p.get("v") in ("alto", "basso") and p.get("h") in ("sinistra", "destra"):
                pos = {"tipo": "angolo", "v": p["v"], "h": p["h"]}
            elif p.get("tipo") == "punto" and 0 <= float(p.get("x", -1)) <= 1 and 0 <= float(p.get("y", -1)) <= 1:
                pos = {"tipo": "punto", "x": float(p["x"]), "y": float(p["y"])}
        except (OSError, ValueError, TypeError):
            pass
        return pos

    def _salva_posizione(self):
        try:
            try:        # unisco ai dati già salvati (es. la città del meteo): non li cancello
                dati = json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
                if not isinstance(dati, dict):
                    dati = {}
            except (OSError, ValueError):
                dati = {}
            dati["_aiuto"] = ("Impostazioni di Eva. 'posizione' = sfera ridotta (si aggiorna quando trascini la sfera "
                              "o dici 'sposta in alto a sinistra'); 'citta_meteo' = città predefinita del meteo. "
                              "Cancella 'posizione' per tornare a ANGOLO_PREDEFINITO.")
            dati["posizione"] = self.posizione
            FILE_IMPOSTAZIONI.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self.log("Non riesco a salvare impostazioni_eva.json (la posizione vale solo fino alla chiusura).")

    # --- schermo e posizioni --------------------------------------------------------------------------------
    def _schermo(self):
        """Lo schermo dove si trova il cursore del mouse (con più schermi non sempre quello principale)."""
        s = None
        try:
            s = QGuiApplication.screenAt(QCursor.pos())
        except Exception:
            pass
        return s or QGuiApplication.primaryScreen()

    def _rett_grande(self, schermo):
        g = schermo.availableGeometry()
        k = min(1.0, g.width() * 0.95 / LARGHEZZA, g.height() * 0.95 / ALTEZZA)
        w, h = int(LARGHEZZA * k), int(ALTEZZA * k)
        return QRect(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2, w, h)

    def _rett_ridotta(self, schermo):
        g = schermo.availableGeometry()           # esclude la barra delle applicazioni
        w, h = int(LARGHEZZA * self.scala_ridotta), int(ALTEZZA * self.scala_ridotta)
        m = self.margine
        if self.posizione["tipo"] == "punto":
            x = g.x() + self.posizione["x"] * g.width() - w / 2
            y = g.y() + self.posizione["y"] * g.height() - h / 2
        else:
            x = g.x() + g.width() - w - m if self.posizione["h"] == "destra" else g.x() + m
            y = g.y() + g.height() - h - m if self.posizione["v"] == "basso" else g.y() + m
        x = _limita(x, g.x() + m, max(g.x() + m, g.x() + g.width() - w - m))
        y = _limita(y, g.y() + m, max(g.y() + m, g.y() + g.height() - h - m))
        return QRect(int(x), int(y), w, h)

    # --- stili Windows ----------------------------------------------------------------------------------------
    def _applica_stili(self):
        """Niente focus e niente pulsante nella barra delle applicazioni (finestra "strumento")."""
        modifica_stile(int(self.winId()), aggiungi=WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, togli=WS_EX_APPWINDOW)

    def _clic_attraverso(self, attivo):
        """True: i clic del mouse passano al programma sotto la finestra."""
        if attivo:
            modifica_stile(int(self.winId()), aggiungi=WS_EX_TRANSPARENT)
        else:
            modifica_stile(int(self.winId()), togli=WS_EX_TRANSPARENT)

    # --- eventi dal cuore di Eva -----------------------------------------
    def _leggi_eventi(self):
        while True:
            try:
                evento = self.vox.eventi.get_nowait()
            except queue.Empty:
                return
            tipo = evento[0]
            if tipo == "mostra":
                self._mostra()
            elif tipo == "benvenuto":            # avvio: benvenuto a schermo intero
                self.imposta_vista("schermo")
            elif tipo == "vista":                # ("vista", "grande" | "ridotta" | "nascosta")
                if self.vista == "schermo" and evento[1] == "ridotta":
                    # "mettiti da parte" dopo il benvenuto: torna sempre in basso a destra (non salvato)
                    self.posizione = {"tipo": "angolo", "v": "basso", "h": "destra"}
                self.imposta_vista(evento[1])
            elif tipo == "angolo":               # ("angolo", orizzontale, verticale): sposta e riduci
                self.imposta_angolo(evento[1], evento[2])
            elif tipo == "progresso":            # percentuale di una copia/spostamento
                self.progresso = evento[1]
            elif tipo == "fase":
                self.stato = evento[1]
                if evento[1] != "ESEGUO":
                    self.progresso = None
                fase = {"IN ASCOLTO": "ascolto", "ELABORO": "elaboro", "ESEGUO": "esegui"}.get(evento[1], "ascolto")
                self.sfera.imposta_fase(fase)
                self.sfera_leggera.imposta_fase(fase)
            elif tipo == "frase":
                self.frase = evento[1]
                self.nota = ""               # nuova frase: tolgo la vecchia risposta
            elif tipo == "nota":
                self.nota = evento[1]
            elif tipo == "esci":             # "spegni Eva": chiude tutto il programma
                self.hide()
                QApplication.instance().quit()
                return
            elif tipo == "chiudi":
                self.t_chiusura = time.perf_counter() + max(0.0, evento[1])

    def _mostra(self):
        """Eva è stata chiamata: la finestra compare (o torna) GRANDE e al centro."""
        if self.vista == "schermo":              # benvenuto a schermo intero: resta così finché non si dice "mettiti da parte"
            return
        self.imposta_vista("grande")

    # --- cambi di aspetto -------------------------------------------------------------------------------------
    def imposta_vista(self, modo):
        adesso = time.perf_counter()
        if modo == "schermo":
            self.t_chiusura = self.t_dissolvenza = None
            self.vista = "schermo"
            self._schermo_scelto = self._schermo()
            g = self._schermo_scelto.geometry()          # tutto lo schermo
            self.sfera_schermo = Sfera(punti=2400, larghezza=640,
                                       altezza=max(300, int(640 * g.height() / max(1, g.width()))))
            self._sfera_in_uso = self.sfera_schermo
            if not self.visibile:
                self.t_inizio = adesso
                self.stato, self.frase, self.nota = "IN ASCOLTO", "", ""
                self.progresso = None
                self._anim = None
                self.setGeometry(g)
                self.setWindowOpacity(0.0)
                self.show()
                self._applica_stili()
                self._clic_attraverso(False)
                self.timer.setInterval(25)
                self.visibile = True
            else:
                self._clic_attraverso(False)
                self._anima(g, OPACITA_GRANDE)
        elif modo == "grande":
            self.t_chiusura = self.t_dissolvenza = None
            self.vista = "grande"
            if not self.visibile:
                self.t_inizio = adesso
                self.sfera = Sfera(punti=2000, larghezza=LARGHEZZA // 2, altezza=ALTEZZA // 2)
                self.sfera_leggera = Sfera(punti=2000, larghezza=LARGHEZZA // 4, altezza=ALTEZZA // 4)
                self._sfera_in_uso = self.sfera
                self.stato, self.frase, self.nota = "IN ASCOLTO", "", ""
                self.progresso = None
                self._anim = None
                self._schermo_scelto = self._schermo()
                self.setGeometry(self._rett_grande(self._schermo_scelto))
                self.setWindowOpacity(0.0)
                self.show()
                self._applica_stili()
                self._clic_attraverso(False)
                self.timer.setInterval(25)   # 40 fotogrammi al secondo
                self.visibile = True
            else:
                self._clic_attraverso(False)             # di nuovo "solida": si può trascinare
                self._schermo_scelto = self._schermo()
                self._anima(self._rett_grande(self._schermo_scelto), OPACITA_GRANDE)
        elif modo == "ridotta":
            self.t_chiusura = self.t_dissolvenza = None
            self.vista = "ridotta"
            self._schermo_scelto = self._schermo()
            if not self.visibile:                        # comparsa diretta, già piccola
                self.t_inizio = adesso - DURATA_COMPARSA
                self.sfera = Sfera(punti=2000, larghezza=LARGHEZZA // 2, altezza=ALTEZZA // 2)
                self.sfera_leggera = Sfera(punti=2000, larghezza=LARGHEZZA // 4, altezza=ALTEZZA // 4)
                self._sfera_in_uso = self.sfera_leggera
                self.stato, self.frase, self.nota = "IN ASCOLTO", "", ""
                self.progresso = None
                self._anim = None
                self.setGeometry(self._rett_ridotta(self._schermo_scelto))
                self.setWindowOpacity(0.0)
                self.show()
                self._applica_stili()
                self.timer.setInterval(25)
                self.visibile = True
            self._clic_attraverso(True)                  # i clic passano attraverso (anche durante l'animazione)
            self._anima(self._rett_ridotta(self._schermo_scelto), self.opacita_ridotta)
        elif modo == "nascosta":
            self.vista = "nascosta"
            if self.visibile:
                self._clic_attraverso(True)
                self._anima(self.geometry(), 0.0, durata=0.3, poi=self._nascondi_subito)

    def _nascondi_subito(self):
        self.visibile = False
        self.hide()
        self.timer.setInterval(100)

    def imposta_angolo(self, orizzontale, verticale):
        """"sposta in alto a sinistra": cambia l'angolo (mantiene quello che non è indicato) e riduce."""
        attuale = self.posizione if self.posizione["tipo"] == "angolo" else {
            "v": "alto" if self.posizione["y"] < 0.5 else "basso", "h": "sinistra" if self.posizione["x"] < 0.5 else "destra"}
        self.posizione = {"tipo": "angolo", "v": verticale or attuale["v"], "h": orizzontale or attuale["h"]}
        self._salva_posizione()
        self.imposta_vista("ridotta")

    def _anima(self, rett, opacita, durata=DURATA_ANIMAZIONE, poi=None):
        g = self.geometry()
        self.timer.setInterval(16)               # durante l'animazione: più fotogrammi
        self._anim = {"t0": time.perf_counter(), "dur": max(0.05, durata),
                      "da": (g.x(), g.y(), g.width(), g.height(), self.windowOpacity()),
                      "a": (rett.x(), rett.y(), rett.width(), rett.height(), opacita), "poi": poi}

    def _passo_animazione(self, adesso):
        a = self._anim
        k = (adesso - a["t0"]) / a["dur"]
        e = _liscia(k)
        v = [d + (f - d) * e for d, f in zip(a["da"], a["a"])]
        self.setGeometry(int(round(v[0])), int(round(v[1])), max(40, int(round(v[2]))), max(30, int(round(v[3]))))
        self.setWindowOpacity(_limita(v[4], 0.0, OPACITA_GRANDE))
        if k >= 1.0:
            self._anim = None
            self.timer.setInterval(25)
            if a["poi"]:
                a["poi"]()

    # --- un fotogramma ------------------------------------------------------
    def chiudi_per_utente(self):
        """Clic sulla X: log, file segnale per la guardia e chiusura completa, senza conferma."""
        if self._chiusura_utente:
            return
        self._chiusura_utente = True
        try:
            self.log("Eva chiusa dall'utente (pulsante X sulla sfera).")
        except Exception:
            pass
        try:
            FILE_CHIUSURA_VOLONTARIA.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " chiusa dall'utente\n",
                                                encoding="utf-8")
        except Exception as e:
            self.log(f"Non riesco a creare chiusura_volontaria.flag: {e}")
        try:
            self.vox.fermo.set()
        except Exception:
            pass
        self.bottone_x.hide()
        self.hide()
        QApplication.instance().quit()

    def _posiziona_x(self):
        """La X sta nell'angolo in alto a destra della sfera, visibile solo quando la sfera lo è."""
        bx = self.bottone_x
        if not self.visibile or self.vista == "nascosta" or self.t_dissolvenza is not None:
            if bx.isVisible():
                bx.hide()
            return
        g = self.geometry()
        d = BottoneX.DIM
        marg = 6 if self.vista == "ridotta" else 10
        bx.move(g.right() - d - marg, g.top() + marg)
        if not bx.isVisible():
            bx.show()
            modifica_stile(int(bx.winId()), aggiungi=WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, togli=WS_EX_APPWINDOW)
        bx.raise_()
        bx.setWindowOpacity(0.55 if self.vista == "ridotta" else 0.9)

    def _tick(self):
        self._leggi_eventi()
        self._posiziona_x()
        if not self.visibile:
            return
        adesso = time.perf_counter()
        if self.t_chiusura is not None and adesso >= self.t_chiusura and self.t_dissolvenza is None:
            self.t_dissolvenza = adesso
            self._anim = None
        base = self.opacita_ridotta if self.vista == "ridotta" else OPACITA_GRANDE
        if self.t_dissolvenza is not None:
            sparito = (adesso - self.t_dissolvenza) / DURATA_DISSOLVENZA
            if sparito >= 1.0:
                self.visibile = False
                self.vista = "nascosta"
                self.hide()
                self.timer.setInterval(100)
                return
            self.setWindowOpacity(max(0.0, base * (1.0 - sparito)))
        elif self._anim is not None:
            self._passo_animazione(adesso)
        elif self.vista in ("grande", "schermo"):
            self.setWindowOpacity(min(OPACITA_GRANDE, (adesso - self.t_inizio) / 0.45))
        self.update()

    # --- trascinamento (solo da grande: quando è ridotta i clic passano attraverso) ---------------------------
    def mousePressEvent(self, evento):
        if self.vista == "grande" and evento.button() == Qt.MouseButton.LeftButton:
            self._anim = None
            self._trascina = evento.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, evento):
        if self._trascina is not None:
            self.move(evento.globalPosition().toPoint() - self._trascina)

    def mouseReleaseEvent(self, evento):
        if self._trascina is None:
            return
        self._trascina = None
        # la posizione scelta diventa il nuovo "angolo" della sfera ridotta (salvata in impostazioni_eva.json)
        centro = self.frameGeometry().center()
        schermo = QGuiApplication.screenAt(centro) or self._schermo()
        g = schermo.availableGeometry()
        self.posizione = {"tipo": "punto", "x": _limita((centro.x() - g.x()) / g.width(), 0.0, 1.0),
                          "y": _limita((centro.y() - g.y()) / g.height(), 0.0, 1.0)}
        self._salva_posizione()
        self.log(f"Sfera: nuova posizione salvata ({self.posizione['x']:.2f}, {self.posizione['y']:.2f}) "
                 "in impostazioni_eva.json: sarà il nuovo angolo.")

    # --- disegno ----------------------------------------------------------------
    def paintEvent(self, _evento):
        adesso = time.perf_counter()
        w, h = self.width(), self.height()
        crescita = min(1.0, (adesso - self.t_inizio) / DURATA_COMPARSA)
        if self.vista == "schermo" and self.sfera_schermo is not None:
            sfera = self.sfera_schermo
        else:
            sfera = self.sfera_leggera if w < LARGHEZZA * 0.55 else self.sfera      # piccola: immagine più leggera
        if sfera is not self._sfera_in_uso:
            self._copia_stato(self._sfera_in_uso, sfera)                        # nessuno scatto nel passaggio
            self._sfera_in_uso = sfera
        quadro = sfera.render(crescita=crescita, livello=self.vox.livello)
        self._immagine_dati = quadro                # tengo il riferimento finché si disegna
        qh, qw, _ = quadro.shape
        immagine = QImage(quadro.data, qw, qh, qw * 3, QImage.Format.Format_RGB888)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.drawImage(QRectF(0, 0, w, h), immagine)
        alfa_base = min(1.0, crescita * 1.6)
        r = w / LARGHEZZA
        etichetta = self.stato + (f" {self.progresso}%" if self.progresso is not None else "")   # "ESEGUO 45%"

        if self.vista == "schermo":
            self._disegna_benvenuto(p, w, h, alfa_base, etichetta)
            p.end()
            return
        grande =_limita((r - 0.55) / 0.35, 0.0, 1.0)        # i testi lunghi spariscono mentre si rimpicciolisce
        if grande > 0:
            p.save()
            p.scale(w / LARGHEZZA, h / ALTEZZA)
            self._disegna_testi_grandi(p, alfa_base * grande, etichetta)
            p.restore()
        piccolo = _limita((0.5 - r) / 0.2, 0.0, 1.0)         # da piccola: solo lo stato
        if piccolo > 0:
            font = QFont("Segoe UI", 8)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
            p.setFont(font)
            colore = QColor(AZZURRO)
            colore.setAlphaF(0.95 * piccolo)
            p.setPen(colore)
            p.drawText(QRectF(0, h - 26, w, 22), Qt.AlignmentFlag.AlignCenter, etichetta)
        p.end()

    @staticmethod
    def _copia_stato(da, a):
        a.fase, a.t_fase, a.t, a.vt, a.angolo, a.livello = da.fase, da.t_fase, da.t, da.vt, da.angolo, da.livello
        a.param = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in da.param.items()}
        a._ultimo = da._ultimo

    def _disegna_benvenuto(self, p, w, h, alfa, etichetta):
        """Testi del benvenuto a schermo intero (misure in proporzione all'altezza dello schermo)."""
        def scrivi(testo, y, px, colore, spazio=0, peso=QFont.Weight.Normal, altezza=None, larg=None, ax=0):
            font = QFont("Segoe UI")
            font.setPixelSize(max(8, int(px)))
            font.setWeight(peso)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spazio)
            p.setFont(font)
            p.setPen(colore)
            r = QRectF(ax, y, larg or w, altezza or px * 1.6)
            p.drawText(r, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap),
                       testo)

        def col(r, g, b, a):
            c = QColor(r, g, b)
            c.setAlphaF(max(0.0, min(1.0, a * alfa)))
            return c

        nome = str(getattr(self.vox, "imp", {}).get("NOME_UTENTE", "") or "").strip()
        scrivi("E.V.A.", h * 0.045, h * 0.026, col(150, 200, 255, 0.85), spazio=h * 0.012)
        scrivi("BENVENUTO", h * 0.085, h * 0.016, col(150, 200, 255, 0.6), spazio=h * 0.008)
        scrivi(f"Ciao {nome}" if nome else "Ciao", h * 0.125, h * 0.075, col(215, 235, 255, 1.0),
               spazio=h * 0.002, peso=QFont.Weight.Light)
        scrivi("Di cosa hai bisogno oggi?", h * 0.225, h * 0.032, col(170, 205, 245, 0.9), spazio=h * 0.002)

        # stato in basso, tra due linee sottili
        y = int(h * 0.80)
        font = QFont("Segoe UI")
        font.setPixelSize(max(8, int(h * 0.018)))
        sp = h * 0.006
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, sp)
        larghezza_testo = QFontMetrics(font).horizontalAdvance(etichetta) + sp * len(etichetta)
        sinistra = w / 2 - larghezza_testo / 2 - h * 0.03
        destra = w / 2 + larghezza_testo / 2 + h * 0.03
        p.setPen(QPen(col(150, 200, 255, 0.45), 1))
        p.drawLine(int(sinistra - h * 0.25), y, int(sinistra), y)
        p.drawLine(int(destra), y, int(destra + h * 0.25), y)
        scrivi(etichetta, y - h * 0.014, h * 0.018, col(150, 200, 255, 0.95), spazio=sp, altezza=h * 0.03)
        if self.frase:
            scrivi(self.frase, y + h * 0.035, h * 0.02, col(140, 175, 225, 0.85), altezza=h * 0.035,
                   larg=w - 2 * h * 0.1, ax=h * 0.1)
        if self.nota:
            scrivi(self.nota, y + h * 0.075, h * 0.024, col(205, 228, 255, 0.95), altezza=h * 0.1,
                   larg=w - 2 * h * 0.1, ax=h * 0.1)
        scrivi('Dì «mettiti da parte» per rimpicciolirmi', h * 0.945, h * 0.014, col(140, 175, 225, 0.55),
               spazio=h * 0.002)

    def _disegna_testi_grandi(self, p, alfa, etichetta):
        # Titolo in alto: piccolo e distanziato
        font = QFont("Segoe UI", 13)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 14)
        p.setFont(font)
        colore = QColor(AZZURRO)
        colore.setAlphaF(0.85 * alfa)
        p.setPen(colore)
        p.drawText(QRectF(0, 22, LARGHEZZA, 34), Qt.AlignmentFlag.AlignCenter, "E.V.A.")

        # Stato in basso, tra due linee sottili
        font = QFont("Segoe UI", 9)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 5)
        p.setFont(font)
        y = ALTEZZA - 112
        larghezza_testo = QFontMetrics(font).horizontalAdvance(etichetta) + 5 * len(etichetta)
        sinistra = LARGHEZZA / 2 - larghezza_testo / 2 - 18
        destra = LARGHEZZA / 2 + larghezza_testo / 2 + 18
        linea = QColor(AZZURRO)
        linea.setAlphaF(0.45 * alfa)
        p.setPen(QPen(linea, 1))
        p.drawLine(int(sinistra - 150), y, int(sinistra), y)
        p.drawLine(int(destra), y, int(destra + 150), y)
        colore.setAlphaF(0.95 * alfa)
        p.setPen(colore)
        p.drawText(QRectF(0, y - 12, LARGHEZZA, 24), Qt.AlignmentFlag.AlignCenter, etichetta)

        # Sotto lo stato: l'ultima frase che hai detto (piccola) e la risposta di Eva (più chiara)
        font = QFont("Segoe UI", 10)
        p.setFont(font)
        if self.frase:
            testo = QFontMetrics(font).elidedText(self.frase, Qt.TextElideMode.ElideRight, LARGHEZZA - 120)
            tenue = QColor(140, 175, 225)
            tenue.setAlphaF(0.85 * alfa)
            p.setPen(tenue)
            p.drawText(QRectF(60, y + 20, LARGHEZZA - 120, 22), Qt.AlignmentFlag.AlignCenter, testo)
        if self.nota:
            chiaro = QColor(205, 228, 255)
            chiaro.setAlphaF(0.95 * alfa)
            p.setPen(chiaro)
            p.drawText(QRectF(60, y + 44, LARGHEZZA - 120, 46),
                       int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                       | int(Qt.TextFlag.TextWordWrap), self.nota)


def _icona():
    """Piccola icona blu per l'area di notifica."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QColor(8, 16, 44))
    p.setPen(QPen(QColor(70, 165, 255), 4))
    p.drawEllipse(6, 6, 52, 52)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(120, 200, 255))
    p.drawEllipse(24, 24, 16, 16)
    p.end()
    return QIcon(pix)


def esegui(vox, log, dopo=None):
    """Avvia l'interfaccia. Restituisce il codice di uscita quando si sceglie Esci.
    `dopo`: funzione da chiamare appena la sfera è comparsa (parte l'ascolto, i modelli, Groq...)."""
    import time as _t
    _inizio = _t.perf_counter()
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)         # senza finestra visibile il programma resta vivo
    finestra = Finestra(vox, log)

    vassoio = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        vassoio = QSystemTrayIcon(_icona(), app)
        vassoio.setToolTip("Eva")
        menu = QMenu()
        azione_attiva = QAction("Attiva ora", menu)
        azione_attiva.triggered.connect(vox.attiva_ora)
        azione_esci = QAction("Esci", menu)
        azione_esci.triggered.connect(app.quit)
        menu.addAction(azione_attiva)
        menu.addSeparator()
        menu.addAction(azione_esci)
        vassoio.setContextMenu(menu)
        vassoio.activated.connect(
            lambda motivo: vox.attiva_ora()
            if motivo == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        vassoio.show()
        log("Icona di Eva accanto all'orologio (clic destro: Attiva ora / Esci).")
    else:
        log("Area di notifica non disponibile: niente icona (chiudi Eva dal Task Manager).")

    app._vox_finestra, app._vox_vassoio = finestra, vassoio     # tengo vivi gli oggetti

    # All'avvio (anche automatico) Eva compare a SCHERMO INTERO con il benvenuto grafico e ci resta
    # finché non si dice "mettiti da parte" (allora va in basso a destra, ridotta).
    def _mostra_all_avvio():
        finestra.posizione = {"tipo": "angolo", "v": "basso", "h": "destra"}   # solo per questa volta, non salvata
        if vox.imp.get("BENVENUTO_SCHERMO_INTERO", True):
            finestra.imposta_vista("schermo")
            finestra.nota = "Sto caricando..."
        else:
            finestra.imposta_vista("ridotta")
    def _partenza():
        _mostra_all_avvio()
        t0 = vox.imp.get("_T0")
        log("TEMPO avvio: sfera visibile" + (f" a {_t.perf_counter() - t0:.1f} s dall'accensione" if t0 else ""))
        if dopo is not None:
            QTimer.singleShot(50, dopo)           # il resto parte dopo che la sfera è stata disegnata
    QTimer.singleShot(0, _partenza)
    return app.exec()
