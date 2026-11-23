"""
Dot-matrix recording pill (PySide6).

    pip install PySide6 sounddevice numpy
    python dotmatrix_pill.py                    # real microphone
    python dotmatrix_pill.py --demo             # fake speech, no mic needed
    python dotmatrix_pill.py --accent white     # amber | white | red | mint | ice

Design
    A matte-black capsule with a recessed LED lattice. The pill is a window
    onto the lattice: as it grows, more dots are revealed along its curved
    edge. Every dot has phosphor persistence (fast on, slow afterglow) and
    hot LEDs push toward white.

    Idle       : one light scanning back and forth, leaving a fading trail
    Recording  : mirrored LED columns that follow your voice, with a hold-and-
                 fall peak dot on every column (classic VU meter)
    Processing : a comet chases around the rim of the pill while the inside
                 breathes

Same public API as the other pills (Pill, AudioSource, state_request).

Standalone controls
    click       idle -> recording -> processing -> (auto) idle
    drag        move the pill        right click   menu

Inside your own app (thread-safe, call from anywhere):
    pill.state_request.emit("recording")
    pill.state_request.emit("processing")   # while your AI polishing runs
    pill.state_request.emit("idle")         # when text has been pasted
    pill.set_accent("white")                # switch LED colour live
"""
import sys
import math
import time
import argparse
from collections import deque

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPainterPath,
                           QLinearGradient, QRadialGradient, QPixmap,
                           QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (66.0, 30.0)       # logical pill sizes (before SCALE)
REC_SIZE = (150.0, 40.0)
PROC_SIZE = (92.0, 34.0)
MARGIN = 24                    # transparent room for the shadow

PITCH = 5.0                    # distance between LEDs
COLS, ROWS = 27, 7             # the lattice (odd, so there is a centre)
EDGE_MARGIN = 3.4              # keep LEDs this far inside the capsule edge

ATTACK_RATE, DECAY_RATE = 36.0, 8.0   # LED persistence (1/s): on / afterglow
ATTACK, RELEASE = 34.0, 10.0          # loudness smoothing speeds (1/s)
TRAIL = 0.30                          # seconds of history across the columns

# accent -> (LED colour, hot colour)
ACCENTS = {
    "amber": ((255, 142, 38), (255, 230, 184)),
    "white": ((226, 230, 240), (255, 255, 255)),
    "red":   ((255, 54, 54), (255, 206, 196)),
    "mint":  ((54, 255, 168), (214, 255, 236)),
    "ice":   ((88, 196, 255), (222, 246, 255)),
}

SAMPLE_RATE = 16000
BLOCK = 512                    # 32 ms

# ----------------------------------------------------------- lattice maths --
_cs = np.arange(COLS) - COLS // 2
_rs = np.arange(ROWS) - ROWS // 2
GX, GY = np.meshgrid(_cs * PITCH, _rs * PITCH)        # (ROWS, COLS)
KROW = np.abs(GY) / PITCH                             # row distance from middle
MIDROW = (KROW == 0)
COLPOS = np.abs(_cs) / max(1, COLS // 2)              # 0 centre .. 1 edge
TAPER = 1.0 - 0.62 * COLPOS ** 1.3
HALF_ROWS = ROWS // 2


# ------------------------------------------------------------------ audio --
class AudioSource:
    """Mic -> loudness 0..1. Self-calibrates to your noise floor and voice."""

    def __init__(self, demo=False):
        self.demo = demo or sd is None
        self.stream = None
        self.db = -90.0
        self.hist = deque()              # (time, dBFS) for adaptive range
        self.t0 = time.time()

    def start(self):
        self.t0 = time.time()
        self.hist.clear()
        self.db = -90.0
        if self.demo or self.stream is not None:
            return
        try:
            self.stream = sd.InputStream(
                channels=1, samplerate=SAMPLE_RATE, blocksize=BLOCK,
                dtype="float32", callback=self._callback)
            self.stream.start()
        except Exception as exc:
            print(f"[pill] mic unavailable ({exc}); using demo signal")
            self.stream = None
            self.demo = True

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _callback(self, indata, frames, t, status):
        x = indata[:, 0]
        rms = float(np.sqrt(np.mean(x * x)))
        self.db = 20.0 * math.log10(rms + 1e-7)

    def level(self):
        if self.demo:
            return self._fake()

        now = time.perf_counter()
        db = self.db
        self.hist.append((now, db))
        while self.hist and now - self.hist[0][0] > 4.0:
            self.hist.popleft()

        # Adaptive range from the last 4 s: quiet floor = noise, top = voice.
        if len(self.hist) < 30:
            noise, top = -58.0, -35.0
        else:
            arr = np.fromiter((d for _, d in self.hist), float)
            noise = float(np.percentile(arr, 10))
            top = float(np.percentile(arr, 98))
        noise = min(max(noise, -85.0), -38.0)
        gate = noise + 7.0                     # below this = silence
        top = max(top, db, gate + 14.0)        # never less than 14 dB range

        x = (db - gate) / (top - gate)
        return min(1.0, max(0.0, x)) ** 0.85

    def _fake(self):
        t = time.time() - self.t0
        talking = 1.0 if math.sin(t * 0.6) > -0.2 else 0.0
        env = abs(math.sin(t * 5.2)) ** 0.7 * (0.6 + 0.4 * math.sin(t * 1.7 + 1))
        return min(1.0, env * talking)


# ----------------------------------------------------------------- widget --
def lerp(a, b, t):
    return a + (b - a) * t


def clamp01(x):
    return min(1.0, max(0.0, x))


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe way to change state

    def __init__(self, audio, auto_finish=2.5, accent="amber"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # standalone demo: fake "enhance" time
        self.state = "idle"

        self.s = 0.0 ; self.sv = 0.0     # recording spring (0..1)
        self.pr = 0.0 ; self.prv = 0.0   # processing spring (0..1)

        self.level = 0.0                 # smoothed loudness
        self.hist = deque()              # (time, level) trail
        self.flow = 0.0

        self.br = np.zeros((ROWS, COLS))                 # LED brightness
        self.mask = np.zeros((ROWS, COLS))               # revealed by the pill
        self.pk = np.zeros(COLS)                         # peak-hold per column
        self.hold = np.zeros(COLS)

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        mw = max(REC_SIZE[0], PROC_SIZE[0]) + MARGIN * 2
        mh = max(REC_SIZE[1], PROC_SIZE[1]) + MARGIN * 2
        self.resize(int(mw * SCALE), int(mh * SCALE))

        self.set_accent(accent)
        self.state_request.connect(self.set_state)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- accent ----
    def set_accent(self, name):
        """'amber' | 'white' | 'red' | 'mint' | 'ice'. Safe to call any time."""
        self.accent = name if name in ACCENTS else "amber"
        self.c_base, self.c_hot = ACCENTS[self.accent]
        r, g, b = self.c_base
        size = 26
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, QColor(r, g, b, 105))
        grad.setColorAt(0.35, QColor(r, g, b, 28))
        grad.setColorAt(1.0, QColor(r, g, b, 0))
        q.setPen(Qt.NoPen)
        q.setBrush(QBrush(grad))
        q.drawEllipse(QPointF(size / 2, size / 2), size / 2, size / 2)
        q.end()
        self.glow = pm
        self.glow_size = size

    # ---- state ----
    @property
    def recording(self):
        return self.state == "recording"

    def set_state(self, state):
        if state == self.state or state not in ("idle", "recording", "processing"):
            return
        self.state = state
        if state == "recording":
            self.audio.start()
        else:
            self.audio.stop()
        if state == "processing" and self.auto_finish:
            QTimer.singleShot(int(self.auto_finish * 1000), self._auto_finish)

    def _auto_finish(self):
        if self.state == "processing":
            self.set_state("idle")

    def place_default(self):
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.x() + (g.width() - self.width()) // 2,
                  g.bottom() - self.height() - 6)

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt):
        k, c = 240.0, 23.0               # slightly under-damped
        v += (k * (target - x) - c * v) * dt
        return x + v * dt, v

    def _sample(self, tt):
        """Smoothed loudness as it was at time tt (linear interpolation)."""
        h = self.hist
        if not h:
            return 0.0
        if tt >= h[-1][0]:
            return h[-1][1]
        for k in range(len(h) - 1, 0, -1):
            t1, v1 = h[k]
            t0, v0 = h[k - 1]
            if t0 <= tt <= t1:
                f = (tt - t0) / (t1 - t0) if t1 > t0 else 1.0
                return v0 + (v1 - v0) * f
        return h[0][1]

    def _size(self):
        pw = lerp(lerp(IDLE_SIZE[0], REC_SIZE[0], self.s), PROC_SIZE[0], self.pr)
        ph = lerp(lerp(IDLE_SIZE[1], REC_SIZE[1], self.s), PROC_SIZE[1], self.pr)
        return pw, ph

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 1 / 30)
        self.last = now
        t = now - self.t0

        self.s, self.sv = self._spring(
            self.s, self.sv, 1.0 if self.recording else 0.0, dt)
        self.pr, self.prv = self._spring(
            self.pr, self.prv, 1.0 if self.state == "processing" else 0.0, dt)
        sa, pr = clamp01(self.s), clamp01(self.pr)

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.9:
            self.hist.popleft()
        self.flow += dt * (0.5 + 2.4 * self.level * sa)

        # --- which LEDs does the capsule reveal? -------------------------
        pw, ph = self._size()
        hw, hh = pw / 2, ph / 2
        rr = hh
        cxl = np.clip(GX, -(hw - rr), hw - rr)
        edge_d = rr - np.hypot(GX - cxl, GY)             # >0 inside the capsule
        s_in = edge_d - EDGE_MARGIN
        self.mask = np.clip(s_in / 1.6 + 0.35, 0.0, 1.0)

        # --- target brightness for each state ----------------------------
        # idle: a single light scanning along the middle row
        amp = max(hw - rr + 2.0, 8.0)
        sx = amp * math.sin(t * 1.7)
        t_idle = MIDROW * (0.10 + 0.90 * np.exp(-((GX - sx) / (PITCH * 1.15)) ** 2))

        # recording: mirrored columns following the voice trail + peak dots
        hcol = np.zeros(COLS)
        for c in range(COLS):
            d = COLPOS[c]
            lv = 0.55 * self.level + 0.45 * self._sample(now - d * TRAIL)
            tex = 0.5 + 0.5 * math.sin(c * 2.3 + self.flow * 6.0) \
                * math.sin(c * 0.7 - self.flow * 3.3)
            hcol[c] = lv * TAPER[c] * (HALF_ROWS + 0.7) * (0.55 + 0.60 * tex)
        up = hcol >= self.pk
        self.pk = np.where(up, hcol, self.pk)
        self.hold = np.where(up, 0.28, self.hold - dt)
        self.pk = np.where(self.hold < 0, np.maximum(self.pk - 7.0 * dt, 0.0), self.pk)

        t_rec = np.clip(hcol[None, :] - KROW + 0.35, 0.0, 1.0)
        peak_on = (KROW == np.round(self.pk)[None, :]) & (self.pk[None, :] >= 1.0) \
            & (self.pk[None, :] > hcol[None, :] + 0.9)
        t_rec = np.maximum(t_rec, peak_on * 1.15)

        # processing: comet around the rim + a quiet breathing core
        ang = np.arctan2(GY / max(hh, 1.0), GX / max(hw, 1.0))
        ring = np.clip((7.0 - s_in) / 2.8, 0.0, 1.0)
        head = t * 4.2
        d1 = (head - ang) % (2 * math.pi)
        d2 = (head + math.pi - ang) % (2 * math.pi)
        comet = np.clip(np.exp(-d1 / 1.25) + 0.40 * np.exp(-d2 / 1.25), 0.0, 1.0)
        breathe = 0.02 + 0.10 * (0.5 + 0.5 * np.sin(t * 3.0 - np.hypot(GX, GY) * 0.22))
        t_proc = ring * (0.07 + 0.93 * comet) + (1.0 - ring) * breathe

        w_idle, w_rec = (1 - sa) * (1 - pr), sa * (1 - pr)
        target = w_idle * t_idle + w_rec * t_rec + pr * t_proc

        rate = np.where(target > self.br, ATTACK_RATE, DECAY_RATE)
        self.br += (target - self.br) * (1.0 - np.exp(-rate * dt))
        self.update()

    # ---- painting ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        cx, cy = w / 2, h / 2
        pw, ph = self._size()
        rect = QRectF(cx - pw / 2, cy - ph / 2, pw, ph)
        rr = ph / 2

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 26 - i * 2.8))))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # matte black body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(24, 24, 27))
        grad.setColorAt(0.5, QColor(11, 11, 13))
        grad.setColorAt(1.0, QColor(4, 4, 5))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        p.save()
        p.setClipPath(body)
        mask = self.mask.tolist()
        br = self.br.tolist()
        gx, gy = GX.tolist(), GY.tolist()
        base, hot = self.c_base, self.c_hot

        # 1) unlit LEDs: the lattice is always faintly visible
        p.setPen(Qt.NoPen)
        for r in range(ROWS):
            for c in range(COLS):
                m = mask[r][c]
                if m < 0.03:
                    continue
                p.setBrush(QColor(70, 72, 82, int(120 * m)))
                p.drawEllipse(QPointF(cx + gx[r][c], cy + gy[r][c]), 1.0, 1.0)

        # 2) glow (additive)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        gs = self.glow_size
        for r in range(ROWS):
            for c in range(COLS):
                b = br[r][c]
                m = mask[r][c]
                if b < 0.14 or m < 0.03:
                    continue
                p.setOpacity(min(1.0, b * 0.7) * m)
                p.drawPixmap(QPointF(cx + gx[r][c] - gs / 2, cy + gy[r][c] - gs / 2),
                             self.glow)
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # 3) LED cores: hot LEDs push toward white
        for r in range(ROWS):
            for c in range(COLS):
                b = br[r][c]
                m = mask[r][c]
                if b < 0.03 or m < 0.03:
                    continue
                bb = min(1.0, b)
                heat = min(1.0, (b ** 2.8) * 1.1)
                col = QColor(int(lerp(base[0], hot[0], heat)),
                             int(lerp(base[1], hot[1], heat)),
                             int(lerp(base[2], hot[2], heat)),
                             int(255 * min(1.0, bb * 1.15) * m))
                rad = 1.05 + 0.65 * bb
                p.setBrush(col)
                p.drawEllipse(QPointF(cx + gx[r][c], cy + gy[r][c]), rad, rad)

        # glass sheen across the top half
        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # recessed bezel + hairline
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 150), 2.2))
        p.drawRoundedRect(rect.adjusted(1.6, 1.6, -1.6, -1.6), rr - 1.5, rr - 1.5)
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 80))
        edge.setColorAt(0.5, QColor(255, 255, 255, 26))
        edge.setColorAt(1.0, QColor(255, 255, 255, 14))
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)

    # ---- interaction ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._origin = self.pos()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._press is not None and e.buttons() & Qt.LeftButton:
            d = e.globalPosition().toPoint() - self._press
            if self._moved or d.manhattanLength() > 4:
                self._moved = True
                self.move(self._origin + d)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            if not self._moved:
                nxt = {"idle": "recording", "recording": "processing",
                       "processing": "idle"}[self.state]
                self.set_state(nxt)
            self._press = None

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Idle", lambda: self.set_state("idle"))
        m.addAction("Recording", lambda: self.set_state("recording"))
        m.addAction("Processing", lambda: self.set_state("processing"))
        m.addSeparator()
        for name in ACCENTS:
            m.addAction(f"LED: {name}", lambda n=name: self.set_accent(n))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake audio, no mic")
    ap.add_argument("--start", action="store_true", help="start recording")
    ap.add_argument("--accent", choices=sorted(ACCENTS), default="amber")
    ap.add_argument("--process-seconds", type=float, default=2.5,
                    help="how long the fake 'enhancing' state lasts (0 = stay)")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, auto_finish=args.process_seconds, accent=args.accent)
    pill.place_default()
    pill.show()
    if args.start:
        pill.set_state("recording")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
