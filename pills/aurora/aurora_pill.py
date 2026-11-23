"""
Aurora recording pill (PySide6).

    pip install PySide6 sounddevice numpy
    python aurora_pill.py            # real microphone
    python aurora_pill.py --demo     # fake speech, no mic needed

Design
    Surface   : dark smoked glass with a soft top sheen
    Idle      : compact pill, a faint aurora drifting, one breathing dot
    Recording : aurora light swells and swirls faster with your voice, and
                three thin ribbon waves ripple outward from the centre
    Processing: the lights gather into an orbit while a rainbow ring
                circles the pill's edge

Same public API as the other pills (Pill, AudioSource, state_request), so you
can swap designs by changing one import.

Standalone controls
    click       idle -> recording -> processing -> (auto) idle
    drag        move the pill        right click   menu

Inside your own app (thread-safe, call from anywhere):
    pill.state_request.emit("recording")
    pill.state_request.emit("processing")   # while your AI polishing runs
    pill.state_request.emit("idle")         # when text has been pasted
"""
import sys
import math
import time
import argparse
from collections import deque

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPainterPath,
                           QLinearGradient, QRadialGradient, QConicalGradient,
                           QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (58.0, 28.0)       # logical pill sizes (before SCALE)
REC_SIZE = (138.0, 38.0)
PROC_SIZE = (88.0, 32.0)
MARGIN = 26                    # transparent room for shadow + processing glow

# cyan -> blue -> violet -> magenta -> peach. Change these to re-colour it.
PALETTE = [(70, 205, 255), (88, 130, 255), (150, 100, 255),
           (255, 88, 196), (255, 156, 108)]

# (colour index, x position -1..1, x drift, x speed, x phase,
#  y drift, y speed, y phase, radius as a fraction of pill height)
BLOBS = [
    (0, -0.78, 0.20, 0.61, 0.0, 0.22, 0.83, 1.2, 0.70),
    (1, -0.40, 0.22, 0.47, 1.7, 0.20, 0.71, 0.4, 0.74),
    (2, 0.00, 0.26, 0.53, 3.1, 0.24, 0.97, 2.6, 0.82),
    (3, 0.42, 0.22, 0.67, 4.4, 0.20, 0.59, 5.0, 0.74),
    (4, 0.80, 0.20, 0.43, 5.6, 0.22, 0.89, 3.3, 0.68),
]

WAVE_PAD = 18.0                # recording: space left/right of the waves
ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)

SAMPLE_RATE = 16000
BLOCK = 512                    # 32 ms


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


def smooth_path(pts):
    """Quadratic-smoothed curve through a list of QPointF."""
    path = QPainterPath(pts[0])
    for i in range(1, len(pts) - 1):
        mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2,
                      (pts[i].y() + pts[i + 1].y()) / 2)
        path.quadTo(pts[i], mid)
    path.lineTo(pts[-1])
    return path


def palette_gradient(xl, xr, alpha):
    """Horizontal palette gradient that fades to nothing at both ends."""
    g = QLinearGradient(xl, 0, xr, 0)
    r, gg, b = PALETTE[0]
    g.setColorAt(0.0, QColor(r, gg, b, 0))
    n = len(PALETTE)
    for i, (r, gg, b) in enumerate(PALETTE):
        g.setColorAt(0.14 + 0.72 * i / (n - 1), QColor(r, gg, b, int(255 * alpha)))
    r, gg, b = PALETTE[-1]
    g.setColorAt(1.0, QColor(r, gg, b, 0))
    return g


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe way to change state

    def __init__(self, audio, auto_finish=2.5):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # standalone demo: fake "enhance" time
        self.state = "idle"

        self.s = 0.0 ; self.sv = 0.0     # recording spring (0..1)
        self.pr = 0.0 ; self.prv = 0.0   # processing spring (0..1)

        self.level = 0.0                 # smoothed loudness
        self.hist = deque()              # (time, level) trail
        self.flow = 0.0                  # animation clock, speeds up with voice

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

        self.state_request.connect(self.set_state)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

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

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 1 / 30)
        self.last = now

        self.s, self.sv = self._spring(
            self.s, self.sv, 1.0 if self.recording else 0.0, dt)
        self.pr, self.prv = self._spring(
            self.pr, self.prv, 1.0 if self.state == "processing" else 0.0, dt)

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.9:
            self.hist.popleft()

        # the lights swirl faster when you talk, and faster again when thinking
        sa, pr = clamp01(self.s), clamp01(self.pr)
        self.flow += dt * (0.45 + 2.6 * self.level * sa + 1.5 * pr)
        self.update()

    # ---- painting helpers ----
    def _draw_aurora(self, p, rect, now, t, sa, pr):
        cx, cy = rect.center().x(), rect.center().y()
        hw, ph = rect.width() / 2, rect.height()
        breathe = 0.5 + 0.5 * math.sin(t * 2.4)
        w_idle, w_rec = (1 - sa) * (1 - pr), sa * (1 - pr)
        n = len(BLOBS)

        p.setCompositionMode(QPainter.CompositionMode_Screen)
        p.setPen(Qt.NoPen)
        for k, (ci, xp, ax, fx, px, ay, fy, py, r0) in enumerate(BLOBS):
            # centre reacts first, edges a moment later -> colour ripples outward
            lv = 0.5 * self.level + 0.5 * self._sample(now - abs(xp) * 0.24)

            fxp = cx + xp * hw * 0.82 + ax * hw * math.sin(self.flow * fx + px)
            fyp = cy + ph * 0.12 + ay * ph * math.sin(self.flow * fy + py)
            ang = self.flow * 1.9 + k * 2 * math.pi / n
            oxp = cx + hw * 0.30 * math.cos(ang)
            oyp = cy + ph * 0.16 * math.sin(ang)
            x, y = lerp(fxp, oxp, pr), lerp(fyp, oyp, pr)

            a = (w_idle * (0.15 + 0.04 * math.sin(t * 1.3 + k))
                 + w_rec * (0.30 + 0.62 * lv)
                 + pr * (0.46 + 0.22 * breathe))
            rad = ph * r0 * 1.22 * (0.85 + 0.55 * lv * w_rec) * (1 + 0.14 * pr * breathe)

            r, g, b = PALETTE[ci]
            grad = QRadialGradient(QPointF(x, y), rad)
            grad.setColorAt(0.0, QColor(r, g, b, int(255 * clamp01(a))))
            grad.setColorAt(0.45, QColor(r, g, b, int(255 * clamp01(a) * 0.42)))
            grad.setColorAt(1.0, QColor(r, g, b, 0))
            p.setBrush(QBrush(grad))
            p.drawEllipse(QPointF(x, y), rad, rad)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    def _draw_waves(self, p, xl, xr, cy, ph, now, a):
        if a < 0.02 or xr - xl < 12:
            return
        n, width = 58, xr - xl
        amp_max = ph * 0.46
        specs = (  # cycles, speed, phase, alpha, pen width, amplitude
            (1.25, 2.1, 0.0, 1.00, 1.7, 1.00),
            (1.90, -1.6, 1.0, 0.55, 1.2, 0.72),
            (2.70, 2.9, 2.3, 0.34, 1.0, 0.50),
        )
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setBrush(Qt.NoBrush)
        for idx, (freq, spd, ph0, al, wd, am) in enumerate(specs):
            pts = []
            for j in range(n):
                u = j / (n - 1)
                d = abs(2 * u - 1)
                env = math.sin(math.pi * u) ** 1.4
                lv = 0.55 * self.level + 0.45 * self._sample(now - d * 0.28)
                amp = amp_max * am * (0.05 + 0.95 * lv) * env
                y = cy + amp * math.sin(2 * math.pi * freq * u + self.flow * spd + ph0)
                pts.append(QPointF(xl + width * u, y))
            path = smooth_path(pts)
            if idx == 0:                                   # soft glow underlay
                glow = QPen(QBrush(palette_gradient(xl, xr, 0.16 * a)), 5.5)
                glow.setCapStyle(Qt.RoundCap)
                p.setPen(glow)
                p.drawPath(path)
            pen = QPen(QBrush(palette_gradient(xl, xr, al * a)), wd)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPath(path)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    # ---- painting ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        now = time.perf_counter()
        t = now - self.t0

        sa, pr = clamp01(self.s), clamp01(self.pr)
        pw = lerp(lerp(IDLE_SIZE[0], REC_SIZE[0], self.s), PROC_SIZE[0], self.pr)
        ph = lerp(lerp(IDLE_SIZE[1], REC_SIZE[1], self.s), PROC_SIZE[1], self.pr)
        cx, cy = w / 2, h / 2
        rect = QRectF(cx - pw / 2, cy - ph / 2, pw, ph)
        rr = ph / 2

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, max(0, 24 - i * 2.6)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # smoked-glass body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(32, 34, 47))
        grad.setColorAt(0.5, QColor(14, 15, 22))
        grad.setColorAt(1.0, QColor(6, 7, 11))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        # everything luminous lives inside the glass
        p.save()
        p.setClipPath(body)
        self._draw_aurora(p, rect, now, t, sa, pr)
        self._draw_waves(p, rect.left() + WAVE_PAD, rect.right() - WAVE_PAD,
                         cy, ph, now, sa * (1.0 - pr))

        # idle: one breathing dot
        idle_w = (1 - sa) * (1 - pr)
        if idle_w > 0.02:
            pulse = 0.5 + 0.5 * math.sin(t * 2.0)
            halo = QRadialGradient(QPointF(cx, cy), 9.0)
            halo.setColorAt(0.0, QColor(255, 255, 255, int(70 * idle_w * (0.6 + 0.4 * pulse))))
            halo.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(QPointF(cx, cy), 9.0, 9.0)
            p.setBrush(QColor(255, 255, 255, int(235 * idle_w)))
            p.drawEllipse(QPointF(cx, cy), 1.7 + 0.25 * pulse, 1.7 + 0.25 * pulse)

        # glass sheen over the light
        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 30))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # glass edge: bright on top, fading toward the bottom
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 96))
        edge.setColorAt(0.5, QColor(255, 255, 255, 30))
        edge.setColorAt(1.0, QColor(255, 255, 255, 18))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)

        # recording: a faint prismatic rim
        if sa * (1 - pr) > 0.02:
            rim = palette_gradient(rect.left(), rect.right(), 0.30 * sa * (1 - pr))
            p.setPen(QPen(QBrush(rim), 1.0))
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)

        # processing: rainbow ring orbiting the edge, with glow
        if pr > 0.02:
            ang = (-t * 150.0) % 360.0
            cols = PALETTE + [PALETTE[0]]
            for width_, alpha in ((8.0, 0.07), (4.0, 0.16), (1.6, 1.0)):
                cg = QConicalGradient(QPointF(cx, cy), ang)
                for i, (r, g, b) in enumerate(cols):
                    cg.setColorAt(i / (len(cols) - 1),
                                  QColor(r, g, b, int(255 * alpha * pr)))
                pen = QPen(QBrush(cg), width_)
                p.setPen(pen)
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
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake audio, no mic")
    ap.add_argument("--start", action="store_true", help="start recording")
    ap.add_argument("--process-seconds", type=float, default=2.5,
                    help="how long the fake 'enhancing' state lasts (0 = stay)")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, auto_finish=args.process_seconds)
    pill.place_default()
    pill.show()
    if args.start:
        pill.set_state("recording")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
