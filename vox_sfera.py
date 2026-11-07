"""La sfera di Eva: tanti puntini luminosi disegnati con numpy.

Questo file NON usa PySide6: produce solo immagini (array numpy RGB).
La finestra (vox_ui.py) le mostra. Si può provare da solo:
    python vox_sfera.py     -> salva alcune immagini di prova (sfera_*.png)
"""
import math
import time

import numpy as np

# Come si comporta la sfera in ogni fase
#   vel   velocità delle onde      rot  rotazione (giri/s in radianti)
#   amp   quanto si deforma        col  colore dei puntini (R, G, B)
#   halo  forza dell'alone         halo_col  colore dell'alone
#   lum   luminosità dei puntini
FASI = {
    "ascolto": dict(vel=1.0, rot=0.22, amp=0.030, col=(70, 165, 255),
                    halo_col=(20, 105, 235), halo=0.60, lum=1.00),
    "elaboro": dict(vel=2.7, rot=0.80, amp=0.210, col=(125, 100, 235),
                    halo_col=(85, 55, 195), halo=0.40, lum=0.85),
    "esegui": dict(vel=1.5, rot=0.45, amp=0.070, col=(170, 232, 255),
                   halo_col=(110, 205, 255), halo=1.05, lum=1.30),
}


def _liscia(ora, obiettivo, dt, velocita=3.5):
    """Avvicina 'ora' a 'obiettivo' in modo morbido."""
    return ora + (obiettivo - ora) * (1.0 - math.exp(-dt * velocita))


class Sfera:
    def __init__(self, punti=2000, larghezza=380, altezza=300, seme=7):
        self.w, self.h = larghezza, altezza
        n = punti
        i = np.arange(n, dtype=np.float64) + 0.5
        phi = np.arccos(1 - 2 * i / n)
        theta = math.pi * (1 + 5 ** 0.5) * i                  # spirale di Fibonacci
        self.p = np.stack([np.cos(theta) * np.sin(phi),
                           np.sin(theta) * np.sin(phi),
                           np.cos(phi)], axis=1).astype(np.float32)
        rng = np.random.default_rng(seme)
        # Piccola irregolarità: spezza gli anelli regolari della spirale
        self.p += rng.normal(0, 0.014, self.p.shape).astype(np.float32)
        self.p /= np.linalg.norm(self.p, axis=1, keepdims=True)
        self.dim = rng.uniform(0.55, 1.0, n).astype(np.float32)   # luce di ogni puntino

        # Sfondo blu scuro e distanza di ogni pixel dal centro (per l'alone)
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        self.cx, self.cy = self.w / 2, self.h * 0.47
        self.raggio0 = 0.34 * min(self.w, self.h)
        dx, dy = (xx - self.cx) / self.raggio0, (yy - self.cy) / self.raggio0
        self.r2 = dx * dx + dy * dy
        vignetta = np.clip(1.0 - 0.28 * self.r2, 0.25, 1.0)
        self.sfondo = (np.array([7, 14, 38], np.float32) * vignetta[..., None]
                       + np.array([1, 2, 6], np.float32))

        # Stato che cambia nel tempo (così le transizioni sono morbide)
        self.fase = "ascolto"
        self.t_fase = 0.0
        self.t = 0.0
        self.vt = 0.0          # "tempo" delle onde (scorre più veloce in 'elaboro')
        self.angolo = 0.0
        self.param = {k: (np.array(v, np.float32) if isinstance(v, tuple) else v)
                      for k, v in FASI["ascolto"].items()}
        self.livello = 0.0
        self._ultimo = None

    def imposta_fase(self, fase):
        if fase != self.fase and fase in FASI:
            self.fase, self.t_fase = fase, self.t

    def render(self, crescita=1.0, livello=0.0, ora=None):
        """Restituisce un'immagine (h, w, 3) uint8.

        crescita: 0..1 (apparizione: la sfera cresce da piccola con dissolvenza)
        livello : 0..1 volume del microfono
        """
        ora = time.perf_counter() if ora is None else ora
        dt = 0.033 if self._ultimo is None else min(0.1, max(0.001, ora - self._ultimo))
        self._ultimo = ora
        self.t += dt

        obiettivo = FASI[self.fase]
        for chiave, valore in obiettivo.items():
            if isinstance(valore, tuple):
                valore = np.array(valore, np.float32)
            self.param[chiave] = _liscia(self.param[chiave], valore, dt)
        P = self.param
        self.livello = _liscia(self.livello, float(livello), dt, 9.0)

        # Quanto si deforma: in ascolto segue il volume del microfono
        amp = P["amp"] + (0.20 * self.livello if self.fase == "ascolto" else 0.0)
        self.vt += dt * float(P["vel"])
        self.angolo += dt * float(P["rot"])
        vt = self.vt

        x, y, z = self.p[:, 0], self.p[:, 1], self.p[:, 2]
        # Onde che attraversano la sfera (somma di tre onde lente)
        d = (0.50 * np.sin(2.1 * x + 1.7 * vt) * np.cos(1.6 * y - 1.1 * vt)
             + 0.35 * np.sin(3.0 * z + 2.3 * vt + 1.9 * x)
             + 0.25 * np.sin(4.2 * y - 2.9 * vt + 2.2 * z)) / 1.1
        r = 1.0 + amp * d
        X, Y, Z = x * r, y * r, z * r

        # Rotazione lenta + leggera inclinazione
        c, s = math.cos(self.angolo), math.sin(self.angolo)
        xr, zr = c * X + s * Z, -s * X + c * Z
        inc = 0.32 + 0.05 * math.sin(self.t * 0.3)
        ci, si = math.cos(inc), math.sin(inc)
        yr, zr = ci * Y - si * zr, si * Y + ci * zr

        # Dimensione: crescita iniziale, respiro lento (ascolto), pulsazione (esegui)
        c0 = min(1.0, max(0.0, crescita))
        ease = c0 * c0 * (3 - 2 * c0)            # crescita morbida: piccola -> grande
        scala = 0.08 + 0.92 * ease
        if self.fase == "ascolto":
            scala *= 1 + 0.022 * math.sin(2 * math.pi * self.t / 4.5)
        pulsa = 0.0
        if self.fase == "esegui":
            pulsa = 0.5 + 0.5 * math.sin(2 * math.pi * 2.2 * (self.t - self.t_fase) - 1.2)
            scala *= 1 + 0.055 * pulsa

        # Prospettiva
        k = 3.4 / (3.4 - 0.9 * zr)
        R = self.raggio0 * scala
        sx = self.cx + R * xr * k
        sy = self.cy + R * yr * k

        # Luce: i puntini davanti sono più luminosi, le creste delle onde brillano
        profondita = np.clip((zr + 1.25) / 2.5, 0.0, 1.0)
        luce = (0.22 + 0.78 * profondita ** 1.3) * self.dim * (1 + 0.45 * np.clip(d, 0, 1))
        luce = luce * float(P["lum"]) * ease

        # Ogni puntino è spalmato su 4 pixel (movimento fluido, senza scatti)
        w, h = self.w, self.h
        ix = np.floor(sx).astype(np.int32)
        iy = np.floor(sy).astype(np.int32)
        fx, fy = sx - ix, sy - iy
        dentro = (ix >= 0) & (ix < w - 1) & (iy >= 0) & (iy < h - 1)
        ix, iy, fx, fy, luce = ix[dentro], iy[dentro], fx[dentro], fy[dentro], luce[dentro]
        base = iy * w + ix
        indici = np.concatenate([base, base + 1, base + w, base + w + 1])
        pesi = np.concatenate([(1 - fx) * (1 - fy) * luce, fx * (1 - fy) * luce,
                               (1 - fx) * fy * luce, fx * fy * luce])
        acc = np.bincount(indici, weights=pesi, minlength=w * h).reshape(h, w).astype(np.float32)

        # Piccolo bagliore attorno ai puntini
        sfocato = acc.copy()
        sfocato[:, 1:-1] = 0.25 * acc[:, :-2] + 0.5 * acc[:, 1:-1] + 0.25 * acc[:, 2:]
        acc2 = sfocato.copy()
        acc2[1:-1] = 0.25 * sfocato[:-2] + 0.5 * sfocato[1:-1] + 0.25 * sfocato[2:]
        punti = 1.0 - np.exp(-1.9 * (0.55 * acc + 0.45 * acc2 * 2.0))

        # Alone azzurro attorno alla sfera
        sc2 = max(0.02, scala * scala)
        alone = np.exp(-self.r2 / (0.85 * sc2 + 0.05)) * float(P["halo"]) * (0.25 + 0.75 * ease)
        if self.fase == "esegui":
            alone = alone * (0.80 + 0.35 * pulsa)

        col = P["col"]
        immagine = (self.sfondo
                    + alone[..., None] * P["halo_col"] * 0.95
                    + punti[..., None] * col
                    + (punti ** 3)[..., None] * (255 - col) * 0.55)
        return np.clip(immagine, 0, 255).astype(np.uint8)


def _salva_png(array, percorso):
    from PIL import Image
    Image.fromarray(array).resize((array.shape[1] * 2, array.shape[0] * 2),
                                  Image.BICUBIC).save(percorso)


if __name__ == "__main__":
    import sys
    cartella = sys.argv[1] if len(sys.argv) > 1 else "."
    s = Sfera()
    t0 = time.perf_counter()
    quadri = 120
    for n in range(quadri):
        s.render(crescita=1.0, livello=0.3, ora=n / 30.0)
    print(f"tempo medio per fotogramma: {(time.perf_counter() - t0) / quadri * 1000:.1f} ms")
    for nome, fase, livello in (("ascolto", "ascolto", 0.25), ("elaboro", "elaboro", 0.0),
                                ("esegui", "esegui", 0.0)):
        s = Sfera()
        s.imposta_fase(fase)
        for n in range(90):
            img = s.render(crescita=1.0, livello=livello, ora=n / 30.0)
        _salva_png(img, f"{cartella}/sfera_{nome}.png")
    s = Sfera()
    for n, crescita in enumerate((0.15, 0.5, 0.85)):
        img = s.render(crescita=crescita, livello=0.0, ora=n / 30.0)
        _salva_png(img, f"{cartella}/sfera_apparizione_{n}.png")
    print("immagini salvate in", cartella)
