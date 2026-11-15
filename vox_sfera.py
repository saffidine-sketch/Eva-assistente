"""L'aspetto di Eva: un cerchio al centro dello schermo (diametro circa 4/9 dell'altezza) con dentro
fulmini arancio e oro, accesi e luminosi, disegnati con numpy.

  - in ascolto: dal cerchio partono quattro fulmini bianchi, sottili e luminosi, verso i quattro angoli dello schermo;
  - quando Eva pensa (elaboro) i fulmini bianchi si ritirano e si avvolgono attorno al cerchio: quattro anelli bianchi;
  - quando Eva parla i fulmini dentro il cerchio vibrano a tempo con la voce.

Questo file NON usa PySide6: produce solo immagini (array numpy RGB). La finestra (vox_ui.py) le mostra.
Si può provare da solo:
    python vox_sfera.py     -> salva alcune immagini di prova (sfera_*.png)
"""
import math
import time

import numpy as np

# Come si comporta in ogni fase
#   vel   velocità del tremolio lento      rot   rotazione lenta di anelli/fulmini bianchi (radianti/s)
#   conv  0 = quattro fulmini verso gli angoli, 1 = quattro anelli attorno al cerchio
#   lum   luminosità dei fulmini interni   wlum  luminosità dei fulmini/anelli bianchi
FASI = {
    "ascolto": dict(vel=0.9, rot=0.0, conv=0.0, lum=1.0, wlum=1.0),
    "elaboro": dict(vel=1.6, rot=0.55, conv=1.0, lum=1.05, wlum=1.0),
    "esegui": dict(vel=1.3, rot=0.9, conv=1.0, lum=1.10, wlum=1.1),
}
COL_ARANCIO = np.array([255, 120, 20], np.float32)
COL_ORO = np.array([255, 195, 60], np.float32)
COL_CALDO = np.array([255, 238, 190], np.float32)      # il cuore più acceso dei fulmini
COL_BIANCO = np.array([235, 242, 255], np.float32)
KI = 11           # tratti di ogni fulmine interno
RI = 3            # rametti per fulmine interno
KBI = 4           # tratti di ogni rametto interno
KW = 96           # tratti di ogni fulmine bianco (molti: così gli anelli sono tondi)
KWC = 30          # nodi 'grossolani' dello zigzag dei fulmini bianchi
RW = 2            # rametti per fulmine bianco
KBW = 4
NI = 9            # fulmini dentro il cerchio


def _liscia(ora, obiettivo, dt, velocita=3.5):
    """Avvicina 'ora' a 'obiettivo' in modo morbido."""
    return ora + (obiettivo - ora) * (1.0 - math.exp(-dt * velocita))


def _inviluppo_voce(t):
    """Andamento finto del volume della voce (la voce di Windows non ci dà il livello): sillabe a ~4 al secondo."""
    sillabe = abs(math.sin(2 * math.pi * 2.1 * t) * math.sin(2 * math.pi * 0.7 * t + 1.0)) ** 0.5
    return min(1.0, sillabe * (0.65 + 0.35 * math.sin(2 * math.pi * 6.3 * t)) + 0.15)


def _sfuma(a, r):
    """Sfocatura a scatola (due passate per direzione) di raggio r pixel."""
    k = 2 * r + 1
    for asse in (0, 1):
        for _ in range(2):
            pad = [(0, 0), (0, 0)]
            pad[asse] = (r + 1, r)
            c = np.cumsum(np.pad(a, pad, mode="edge"), axis=asse, dtype=np.float32)
            if asse == 0:
                a = (c[k:] - c[:-k]) / k
            else:
                a = (c[:, k:] - c[:, :-k]) / k
    return a


def _alone(acc, f, r):
    """Alone morbido: riduce l'immagine di f volte, la sfoca di r pixel, la riporta alla misura originale."""
    h, w = acc.shape
    h2, w2 = h // f, w // f
    m = acc[:h2 * f, :w2 * f].reshape(h2, f, w2, f).mean(axis=(1, 3))
    m = _sfuma(m, r)
    g = np.repeat(np.repeat(m, f, axis=0), f, axis=1)
    return np.pad(g, ((0, h - g.shape[0]), (0, w - g.shape[1])), mode="edge")


def _zigzag(rng, forma, fase_alt=True):
    """Zigzag irregolare: un po' alternato (a dente) e un po' casuale."""
    alt = np.where(np.arange(forma[-1]) % 2 == 0, 1.0, -1.0) * rng.uniform(0.3, 1.0, forma)
    z = 0.6 * alt + 0.7 * rng.uniform(-1.0, 1.0, forma)
    z[..., 0] = 0.0
    return z


class Sfera:
    def __init__(self, punti=70, larghezza=380, altezza=300, seme=11):
        self.w, self.h = larghezza, altezza
        rng = np.random.default_rng(seme)
        self.cx, self.cy = larghezza / 2, altezza / 2
        self.R = altezza * 2.0 / 9.0                           # diametro = 4/9 dell'altezza (circa 0,44), in proporzione: vale su ogni monitor

        # --- fulmini arancio e oro dentro il cerchio ---
        n = NI
        self.th0 = ((np.arange(n) + rng.uniform(-0.3, 0.3, n)) * (2 * math.pi / n)).astype(np.float64)
        self.lung = rng.uniform(0.84, 0.98, n)
        self.dim = rng.uniform(0.80, 1.0, n).astype(np.float32)
        self.gold = (np.arange(n) % 2 == 1)
        self.f_ang = rng.uniform(0.15, 0.4, n)
        self.p_ang = rng.uniform(0, 2 * math.pi, n)
        s = np.linspace(0, 1, KI + 1)[None, :] + rng.uniform(-0.3 / KI, 0.3 / KI, (n, KI + 1))
        s[:, 0], s[:, -1] = 0.0, 1.0
        self.s = np.sort(s, axis=1)
        self.zig = _zigzag(rng, (n, KI + 1)) * np.where(rng.random((n, 1)) < 0.5, 1.0, -1.0)
        self.f_z = rng.uniform(0.6, 1.5, (n, KI + 1))
        self.p_z = rng.uniform(0, 2 * math.pi, (n, KI + 1))
        self.f_v = rng.uniform(7.0, 13.0, (n, KI + 1))
        self.p_v = rng.uniform(0, 2 * math.pi, (n, KI + 1))
        self.kb = rng.integers(2, KI - 3, (n, RI))
        self.lato = rng.choice([-1.0, 1.0], (n, RI))
        self.ang_b = rng.uniform(0.40, 0.85, (n, RI))
        self.len_b = rng.uniform(0.18, 0.34, (n, RI))
        sb = np.linspace(0, 1, KBI + 1)[None, None, :] + rng.uniform(-0.08, 0.08, (n, RI, KBI + 1))
        sb[..., 0], sb[..., -1] = 0.0, 1.0
        self.sb = np.sort(sb, axis=2)
        self.zb = _zigzag(rng, (n, RI, KBI + 1))

        # --- quattro fulmini bianchi verso gli angoli ---
        angoli = [math.atan2(sy * altezza / 2, sx * larghezza / 2) for sy in (-1, 1) for sx in (1, -1)]
        # ordine: alto-destra, alto-sinistra, basso-destra, basso-sinistra
        self.thw = np.array(angoli, np.float64)
        self.Dw = np.hypot(larghezza / 2, altezza / 2) * np.ones(4) * 0.985
        swc = np.linspace(0, 1, KWC + 1)[None, :] + rng.uniform(-0.3 / KWC, 0.3 / KWC, (4, KWC + 1))
        swc[:, 0], swc[:, -1] = 0.0, 1.0
        self.swc = np.sort(swc, axis=1)                       # posizioni dei nodi dello zigzag
        self.sw = np.linspace(0, 1, KW + 1)[None, :] * np.ones((4, 1))
        self.zwc = _zigzag(rng, (4, KWC + 1))
        self.f_zw = rng.uniform(0.5, 1.2, (4, KWC + 1))
        self.p_zw = rng.uniform(0, 2 * math.pi, (4, KWC + 1))
        self.kbw = rng.integers(12, KW - 18, (4, RW))
        self.latow = rng.choice([-1.0, 1.0], (4, RW))
        self.angw = rng.uniform(0.45, 0.8, (4, RW))
        self.lenw = rng.uniform(0.05, 0.10, (4, RW))
        sbw = np.linspace(0, 1, KBW + 1)[None, None, :] + rng.uniform(-0.08, 0.08, (4, RW, KBW + 1))
        sbw[..., 0], sbw[..., -1] = 0.0, 1.0
        self.sbw = np.sort(sbw, axis=2)
        self.zbw = _zigzag(rng, (4, RW, KBW + 1))
        self.f_zbw = rng.uniform(0.5, 1.2, (4, RW, KBW + 1))
        self.p_zbw = rng.uniform(0, 2 * math.pi, (4, RW, KBW + 1))
        self.raggio_anello = self.R * (1.10 + 0.09 * np.arange(4))      # quattro anelli, uno dentro l'altro

        # --- sfondo, cerchio ---
        yy, xx = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        dx, dy = xx - self.cx, yy - self.cy
        self.r = np.sqrt(dx * dx + dy * dy) / self.R                    # distanza dal centro, in raggi del cerchio
        vignetta = np.clip(1.0 - 0.05 * self.r ** 2 / 4, 0.55, 1.0)
        self.sfondo = np.array([9, 5, 3], np.float32) * vignetta[..., None]
        self.disco = np.clip((1.0 - self.r) * self.R / 1.2 + 0.5, 0.0, 1.0)     # 1 dentro il cerchio, bordo morbido
        self.rim = np.exp(-((self.r - 1.0) * self.R / 1.5) ** 2)         # bordo sottile
        self.rim_alone_f = np.exp(-np.maximum(self.r - 1.0, 0) * 2.2) * (self.r > 1.0)
        self.rim_alone_d = np.exp(-np.maximum(1.0 - self.r, 0) * 3.0) * (self.r <= 1.0)
        self.cuore = np.exp(-(self.r / 0.30) ** 2)
        fondo = self.disco * (0.22 + 0.30 * np.exp(-(self.r / 0.75) ** 2))
        self.statici = np.stack([fondo, self.rim, self.rim_alone_f, self.rim_alone_d, self.cuore * self.disco]).astype(np.float32)

        # Stato che cambia nel tempo (così le transizioni sono morbide)
        self.fase = "ascolto"
        self.t_fase = 0.0
        self.t = 0.0
        self.vt = 0.0          # "tempo" del tremolio (scorre più veloce quando Eva pensa)
        self.angolo = 0.0      # rotazione lenta di anelli e fulmini bianchi
        self.param = dict(FASI["ascolto"])
        self.livello = 0.0
        self.voce = 0.0        # quanto vibrano i fulmini con la voce (0..1)
        self._ultimo = None

    def imposta_fase(self, fase):
        if fase != self.fase and fase in FASI:
            self.fase, self.t_fase = fase, self.t

    # --- geometria ---------------------------------------------------------------
    def _interni(self, ease):
        """Fulmini arancio/oro dentro il cerchio: (P0, P1, peso, oro)."""
        n, R, cx, cy, vt = NI, self.R, self.cx, self.cy, self.vt
        th = self.th0 + 0.10 * np.sin(self.f_ang * vt * 2.0 + self.p_ang)
        co, si = np.cos(th), np.sin(th)
        v = self.voce
        L = R * self.lung * ease * (1.0 + 0.10 * v * np.sin(self.f_v[:, 0] * self.t + self.p_v[:, 0]))
        amp = 0.075 + 0.07 * v
        z = self.zig + 0.30 * np.sin(self.f_z * vt * 2.4 + self.p_z)
        if v > 0.01:
            z = z + 1.1 * v * np.sin(self.f_v * self.t + self.p_v)     # vibrano a tempo con la voce
        inv = np.minimum(1.0, self.s * 4.0)
        d = self.s * L[:, None]
        off = z * inv * amp * L[:, None]
        u = np.stack([co, si], axis=-1)[:, None, :]
        nv = np.stack([-si, co], axis=-1)[:, None, :]
        P = np.array([cx, cy])[None, None, :] + d[..., None] * u + off[..., None] * nv

        idx = np.arange(n)[:, None]
        Pb0 = P[idx, self.kb]
        thb = th[:, None] + self.lato * self.ang_b
        ub = np.stack([np.cos(thb), np.sin(thb)], axis=-1)[:, :, None, :]
        nb = np.stack([-np.sin(thb), np.cos(thb)], axis=-1)[:, :, None, :]
        lb = (L[:, None] * self.len_b)[..., None]
        zb = self.zb + 0.30 * np.sin(self.f_z[:, None, :KBI + 1] * vt * 2.4 + self.p_z[:, None, :KBI + 1])
        if v > 0.01:
            zb = zb + 1.0 * v * np.sin(self.f_v[:, None, :KBI + 1] * self.t + self.p_v[:, None, :KBI + 1])
        Pb = (Pb0[:, :, None, :] + (self.sb * lb)[..., None] * ub
              + (zb * np.minimum(1.0, self.sb * 4.0) * 0.20 * lb)[..., None] * nb)

        p0 = [P[:, :-1].reshape(-1, 2), Pb[:, :, :-1].reshape(-1, 2)]
        p1 = [P[:, 1:].reshape(-1, 2), Pb[:, :, 1:].reshape(-1, 2)]
        sm = 0.5 * (self.s[:, :-1] + self.s[:, 1:])
        pesi = [((1.0 - 0.40 * sm) * self.dim[:, None]).reshape(-1)]
        sbm = 0.5 * (self.sb[..., :-1] + self.sb[..., 1:])
        att = (1.0 - 0.40 * self.s[idx, self.kb])[..., None]
        pesi.append((0.80 * att * (1.0 - 0.70 * sbm) * self.dim[:, None, None]).reshape(-1))
        oro = [np.repeat(self.gold, KI), np.repeat(self.gold, RI * KBI)]
        return np.concatenate(p0), np.concatenate(p1), np.concatenate(pesi), np.concatenate(oro)

    def _bianchi(self, ease, conv):
        """Quattro fulmini bianchi che si avvolgono in quattro anelli: (P0, P1, peso)."""
        R, cx, cy, vt = self.R, self.cx, self.cy, self.vt
        c0 = np.array([cx, cy])
        th = self.thw + 0.012 * np.sin(vt * 0.9 + np.arange(4))
        co, si = np.cos(th), np.sin(th)
        u = np.stack([co, si], axis=-1)[:, None, :]
        nv = np.stack([-si, co], axis=-1)[:, None, :]
        L = (self.Dw - R) * ease                                         # lunghezza fuori dal cerchio
        # fulmine teso: dal bordo del cerchio verso l'angolo, con zigzag
        zc = self.zwc + 0.30 * np.sin(self.f_zw * vt * 3.0 + self.p_zw)
        z = np.stack([np.interp(self.sw[k], self.swc[k], zc[k]) for k in range(4)])
        zam = 0.022
        d = R + self.sw * L[:, None]
        off = z * np.minimum(1.0, self.sw * 6.0) * zam * (R + L[:, None] * 0.6)
        P = c0[None, None, :] + d[..., None] * u + off[..., None] * nv       # (4, KW+1, 2)

        # morbidamente verso l'anello: coordinate polari (raggio, angolo) interpolate, la punta parte per prima
        rel = P - c0[None, None, :]
        rho_b = np.hypot(rel[..., 0], rel[..., 1])
        al_b = th[:, None] + np.arctan2(rel[..., 0] * (-si)[:, None] + rel[..., 1] * co[:, None],
                                        rel[..., 0] * co[:, None] + rel[..., 1] * si[:, None])
        al_r = th[:, None] + self.angolo + 2 * math.pi * self.sw
        rho_r = (self.raggio_anello[:, None] * (1.0 + 0.012 * np.sin(vt * 2.0 + 6.0 * self.sw + np.arange(4)[:, None]))
                 + 0.022 * R * z * np.minimum(1.0, np.minimum(self.sw, 1 - self.sw) * 8.0))
        cs = np.clip(conv * 1.6 - 0.6 * (1.0 - self.sw), 0.0, 1.0)
        cs = cs * cs * (3 - 2 * cs)
        rho = rho_b * (1 - cs) + rho_r * cs
        al = al_b * (1 - cs) + al_r * cs
        P = c0[None, None, :] + np.stack([rho * np.cos(al), rho * np.sin(al)], axis=-1)
        # lungo l'anello l'ampiezza non cambia: il peso dipende dalla posizione
        idx = np.arange(4)[:, None]
        Pb0 = P[idx, self.kbw]
        thb = th[:, None] + self.latow * self.angw
        ub = np.stack([np.cos(thb), np.sin(thb)], axis=-1)[:, :, None, :]
        nb = np.stack([-np.sin(thb), np.cos(thb)], axis=-1)[:, :, None, :]
        lb = (L[:, None] * self.lenw)[..., None] * (1.0 - conv)
        zb = self.zbw + 0.3 * np.sin(self.f_zbw * vt * 3.0 + self.p_zbw)
        Pb = (Pb0[:, :, None, :] + (self.sbw * lb)[..., None] * ub
              + (zb * np.minimum(1.0, self.sbw * 4.0) * 0.22 * lb)[..., None] * nb)

        sm = 0.5 * (self.sw[:, :-1] + self.sw[:, 1:])
        peso_f = (1.0 - 0.45 * sm * (1.0 - conv))                            # la punta sfuma un poco, l'anello no
        peso_r = 0.75 * (1.0 - conv) ** 1.5 * (1.0 - 0.35 * (self.sbw[..., :-1] + self.sbw[..., 1:]))
        p0 = np.concatenate([P[:, :-1].reshape(-1, 2), Pb[:, :, :-1].reshape(-1, 2)])
        p1 = np.concatenate([P[:, 1:].reshape(-1, 2), Pb[:, :, 1:].reshape(-1, 2)])
        pesi = np.concatenate([peso_f.reshape(-1), peso_r.reshape(-1)])
        return p0, p1, pesi

    # --- disegno ------------------------------------------------------------------
    def _spalma(self, sx, sy, pesi):
        """Somma i pesi (un'immagine per ogni livello) nei 4 pixel attorno a ogni punto."""
        w, h = self.w, self.h
        ix = np.floor(sx).astype(np.int32)
        iy = np.floor(sy).astype(np.int32)
        fx, fy = sx - ix, sy - iy
        dentro = (ix >= 0) & (ix < w - 1) & (iy >= 0) & (iy < h - 1)
        ix, iy, fx, fy = ix[dentro], iy[dentro], fx[dentro], fy[dentro]
        base = iy * w + ix
        indici = np.concatenate([base, base + 1, base + w, base + w + 1])
        k = np.concatenate([(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy])
        uscita = []
        for peso in pesi:
            pw = peso[dentro]
            uscita.append(np.bincount(indici, weights=k * np.tile(pw, 4), minlength=w * h).reshape(h, w).astype(np.float32))
        return uscita

    def _punti(self, p0, p1, peso_seg, luce):
        """Punti lungo ogni segmento (circa uno per pixel) con peso proporzionale alla lunghezza."""
        lungh = np.linalg.norm(p1 - p0, axis=1)
        ns = int(min(64, max(3, math.ceil(float(lungh.max())) + 1)))
        t = np.linspace(0.0, 1.0, ns, dtype=np.float64)[None, :]
        sx = (p0[:, 0:1] * (1 - t) + p1[:, 0:1] * t).ravel()
        sy = (p0[:, 1:2] * (1 - t) + p1[:, 1:2] * t).ravel()
        peso = ((peso_seg * luce * lungh / ns)[:, None] * np.ones((1, ns))).ravel()
        return sx, sy, peso

    def render(self, crescita=1.0, livello=0.0, ora=None, parla=False):
        """Restituisce un'immagine (h, w, 3) uint8.

        crescita: 0..1 (apparizione: il cerchio si accende e i fulmini si allungano)
        livello : 0..1 volume del microfono
        parla   : True mentre Eva sta parlando (i fulmini interni vibrano a tempo con la voce)
        """
        ora = time.perf_counter() if ora is None else ora
        dt = 0.033 if self._ultimo is None else min(0.1, max(0.001, ora - self._ultimo))
        self._ultimo = ora
        self.t += dt

        for chiave, valore in FASI[self.fase].items():
            vel = 2.6 if chiave == "conv" else 3.5          # fulmini e anelli si trasformano in circa un secondo
            self.param[chiave] = _liscia(self.param[chiave], valore, dt, vel)
        P = self.param
        self.livello = _liscia(self.livello, float(livello), dt, 9.0)
        self.voce = _liscia(self.voce, _inviluppo_voce(self.t) if parla else 0.0, dt, 14.0)
        self.vt += dt * float(P["vel"])
        self.angolo += dt * float(P["rot"])
        conv = min(1.0, max(0.0, float(P["conv"])))
        w, h, R = self.w, self.h, self.R

        c0 = min(1.0, max(0.0, crescita))
        ease = c0 * c0 * (3 - 2 * c0)
        v = self.voce

        # fulmini interni (arancio, oro)
        p0, p1, ps, oro = self._interni(ease)
        luce = float(P["lum"]) * (1.0 + 0.55 * v) * (0.30 + 0.70 * min(1.0, ease * 1.4))
        sx, sy, peso = self._punti(p0, p1, ps, luce)
        og = np.repeat(oro, len(peso) // len(oro))
        # niente luce fuori dal cerchio
        rr = np.hypot(sx - self.cx, sy - self.cy) / R
        peso = peso * np.clip((0.99 - rr) * 12.0, 0.0, 1.0)
        a_or, a_gd = self._spalma(sx, sy, [np.where(og, 0.0, peso), np.where(og, peso, 0.0)])

        # fulmini bianchi / anelli
        q0, q1, qs = self._bianchi(ease, conv)
        lw = float(P["wlum"]) * (0.25 + 0.75 * min(1.0, ease * 1.4)) * (1.0 + 0.12 * math.sin(self.t * 2.4))
        wx, wy, wp = self._punti(q0, q1, qs, lw)
        a_w, = self._spalma(wx, wy, [wp])

        def nitido(acc, k):
            m = acc.copy()
            m[:, 1:-1] = 0.25 * acc[:, :-2] + 0.5 * acc[:, 1:-1] + 0.25 * acc[:, 2:]
            m2 = m.copy()
            m2[1:-1] = 0.25 * m[:-2] + 0.5 * m[1:-1] + 0.25 * m[2:]
            return 1.0 - np.exp(-k * (0.55 * acc + 0.45 * m2 * 1.6))

        n_or, n_gd = nitido(a_or, 2.6), nitido(a_gd, 2.6)
        n_w = nitido(a_w, 2.4)
        # bagliore (luce che sbava attorno ai fulmini)
        interni = a_or + a_gd
        g_in = np.minimum(_alone(interni, 2, 2) * 2.4 + _alone(interni, 4, 3) * 2.6, 3.0)
        g_w = np.minimum(_alone(a_w, 2, 2) * 1.8 + _alone(a_w, 4, 3) * 1.4, 3.0)

        respiro = 1.0 + 0.04 * math.sin(2 * math.pi * self.t / 3.2) + 0.15 * v
        vivo = 0.3 + 0.7 * ease
        campi = np.empty((12, h, w), np.float32)
        campi[:5] = self.statici
        campi[5], campi[6], campi[7] = g_in, n_or, n_gd
        campi[8] = (n_or + n_gd) ** 2
        campi[9], campi[10], campi[11] = g_w, n_w, n_w * n_w
        colori = np.array([
            np.array([70, 28, 5], np.float32) * respiro * vivo,                              # fondo del cerchio
            np.array([255, 150, 40], np.float32) * 0.85 * ease * respiro,                    # bordo
            np.array([210, 85, 12], np.float32) * 0.22 * ease * respiro,                     # alone fuori
            np.array([210, 85, 12], np.float32) * 0.30 * ease * respiro,                     # alone dentro
            np.array([150, 70, 10], np.float32) * (0.5 + 0.9 * v) * ease * 0.5,              # cuore
            np.array([150, 62, 10], np.float32),                                             # bagliore interno
            COL_ARANCIO * 1.15, COL_ORO * 1.1, COL_CALDO * 0.55,
            np.array([60, 80, 120], np.float32) * 0.55,                                      # bagliore bianco
            COL_BIANCO * 1.05, np.array([60, 60, 60], np.float32),
        ], np.float32)
        mix = (colori.T @ campi.reshape(12, -1)).reshape(3, h, w).transpose(1, 2, 0)
        return np.clip(self.sfondo + mix, 0, 255).astype(np.uint8)


def _salva_png(array, percorso):
    from PIL import Image
    Image.fromarray(array).resize((array.shape[1] * 2, array.shape[0] * 2),
                                  Image.BICUBIC).save(percorso)


if __name__ == "__main__":
    import sys
    cartella = sys.argv[1] if len(sys.argv) > 1 else "."
    s = Sfera(larghezza=380, altezza=300)
    t0 = time.perf_counter()
    quadri = 120
    for n in range(quadri):
        s.render(crescita=1.0, livello=0.3, ora=n / 30.0)
    print(f"tempo medio per fotogramma (finestra 380x300): {(time.perf_counter() - t0) / quadri * 1000:.1f} ms")
    schermo = Sfera(larghezza=640, altezza=360)
    t0 = time.perf_counter()
    for n in range(quadri):
        schermo.render(crescita=1.0, livello=0.0, ora=n / 30.0)
    print(f"tempo medio per fotogramma (schermo intero 640x360): {(time.perf_counter() - t0) / quadri * 1000:.1f} ms")
    for nome, fase, livello, parla, quadri_n in (("ascolto", "ascolto", 0.0, False, 120), ("elaboro_meta", "elaboro", 0.0, False, 22),
                                                 ("elaboro", "elaboro", 0.0, False, 120), ("esegui", "esegui", 0.0, False, 120),
                                                 ("parla", "ascolto", 0.0, True, 120)):
        for sigla, (larg, alt) in (("finestra", (380, 300)), ("schermo", (640, 360))):
            s = Sfera(larghezza=larg, altezza=alt)
            s.imposta_fase(fase)
            for n in range(quadri_n):
                img = s.render(crescita=1.0, livello=livello, ora=n / 30.0, parla=parla)
            _salva_png(img, f"{cartella}/sfera_{sigla}_{nome}.png")
    s = Sfera(larghezza=640, altezza=360)
    for n, crescita in enumerate((0.15, 0.5, 0.85)):
        img = s.render(crescita=crescita, livello=0.0, ora=n / 30.0)
        _salva_png(img, f"{cartella}/sfera_schermo_apparizione_{n}.png")
    print("immagini salvate in", cartella)
