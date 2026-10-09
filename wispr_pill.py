"""
Wispr Flow style recording pill (PySide6).

    pip install PySide6 sounddevice numpy
    python wispr_pill.py            # real microphone
    python wispr_pill.py --demo     # fake speech, no mic needed

States
    idle        small black pill, dim dots
    recording   dots turn into white bars that follow your voice loudness
    processing  dots shift left, spinner appears on the right (enhancing)

Standalone controls
    click       idle -> recording -> processing -> (auto) idle
    drag        move the pill
    right click menu

Using it inside your own app (thread-safe, call from anywhere):
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
from PySide6.QtGui import QPainter, QColor, QPen, QImage, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier (0.7 = 30% smaller)

NUM_BARS = 15                  # keep odd so there is a centre bar
CENTER = NUM_BARS // 2

IDLE_SIZE = (76.0, 28.0)       # logical pill sizes (before SCALE)
ACTIVE_SIZE = (94.0, 33.0)
PROC_SIZE = (100.0, 31.0)
MARGIN = 18                    # transparent room for the shadow

DOT_PITCH, BAR_PITCH = 3.9, 5.0
DOT_W, BAR_W = 2.1, 2.7
PROC_DOTS_HALF = 5             # dots kept on each side of centre while processing
SPIN_D, SPIN_GAP = 12.0, 9.0   # spinner diameter / gap from the dots

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
TRAIL_DELAY = 0.042            # seconds of delay per bar going outward

SS = 2                         # supersampling factor for smooth edges

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
        self.vals = [0.0] * NUM_BARS
        self.taper = [1.0 - 0.30 * (abs(i - CENTER) / CENTER) ** 1.3
                      for i in range(NUM_BARS)]

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        mw = max(ACTIVE_SIZE[0], PROC_SIZE[0]) + MARGIN * 2
        mh = max(ACTIVE_SIZE[1], PROC_SIZE[1]) + MARGIN * 2
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
                  g.bottom() - self.height() - 10)

    # ---- animation ----
    def _spring(self, x, v, target, dt):
        k, c = 260.0, 24.0               # slightly under-damped
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

        # loudness: instant attack, soft release -> follows syllables closely
        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.6:
            self.hist.popleft()

        # centre bar = now, outer bars = slightly older loudness
        for i in range(NUM_BARS):
            d = abs(i - CENTER)
            self.vals[i] = self._sample(now - d * TRAIL_DELAY) * self.taper[i]
        self.update()

    # ---- painting ----
    def paintEvent(self, _):
        # Supersample: draw at SSx into an image, then smooth-downscale it.
        # Gives clean edges regardless of the platform's own antialiasing.
        dpr = self.devicePixelRatioF()
        iw = max(1, round(self.width() * dpr)) * SS
        ih = max(1, round(self.height() * dpr)) * SS
        img = QImage(iw, ih, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        w, h = self.width() / SCALE, self.height() / SCALE
        ip = QPainter(img)
        ip.setRenderHint(QPainter.Antialiasing)
        ip.scale(iw / w, ih / h)
        self._draw(ip, w, h)
        ip.end()

        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(self.rect(), img)
        p.end()

    def _draw(self, p, w, h):
        t = time.perf_counter() - self.t0

        sa, pr = clamp01(self.s), clamp01(self.pr)
        pw = lerp(lerp(IDLE_SIZE[0], ACTIVE_SIZE[0], self.s), PROC_SIZE[0], self.pr)
        ph = lerp(lerp(IDLE_SIZE[1], ACTIVE_SIZE[1], self.s), PROC_SIZE[1], self.pr)
        rect = QRectF((w - pw) / 2, (h - ph) / 2, pw, ph)

        # soft shadow
        p.setPen(Qt.NoPen)
        for i in range(7):
            g = i * 1.7
            r = rect.adjusted(-g, -g + 3, g, g + 3)
            p.setBrush(QColor(0, 0, 0, max(0, 20 - i * 3)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # body + hairline border
        body = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(7, 7, 7))
        p.setPen(QPen(QColor(255, 255, 255, 56), 1.0))
        p.drawRoundedRect(body, body.height() / 2, body.height() / 2)

        # layout of dots/bars (+ spinner while processing)
        pitch = lerp(DOT_PITCH, BAR_PITCH, sa)
        bw = lerp(DOT_W, BAR_W, sa)
        dots_w = PROC_DOTS_HALF * 2 * DOT_PITCH + DOT_W
        total = dots_w + SPIN_GAP + SPIN_D
        left = w / 2 - total / 2
        dots_cx = lerp(w / 2, left + dots_w / 2, pr)
        cy = h / 2
        maxh = ph - 13

        p.setPen(Qt.NoPen)
        for i in range(NUM_BARS):
            d = abs(i - CENTER)
            v = (0.06 + 0.94 * clamp01(self.vals[i])) * sa
            hgt = bw + (maxh - bw) * v

            shimmer = 0.5 + 0.5 * math.sin(t * 2.2 - i * 0.55)
            a = lerp(110 + 45 * shimmer, 255, sa)
            if d > PROC_DOTS_HALF:
                a *= 1.0 - pr               # outer dots fade out for spinner
            if a < 1:
                continue
            p.setBrush(QColor(255, 255, 255, int(a)))

            x = dots_cx + (i - CENTER) * pitch
            p.drawRoundedRect(QRectF(x - bw / 2, cy - hgt / 2, bw, hgt),
                              bw / 2, bw / 2)

        # spinner: 8 spokes, bright head sweeping clockwise with a fading tail
        if pr > 0.02:
            sx = left + dots_w + SPIN_GAP + SPIN_D / 2
            head = (t * 9.0) % 8.0
            p.save()
            p.translate(sx, cy)
            r_in, r_out = SPIN_D * 0.27, SPIN_D * 0.5
            for k in range(8):
                delta = (head - k) % 8.0
                a = (0.14 + 0.86 * (1.0 - delta / 8.0) ** 1.4) * pr
                pen = QPen(QColor(255, 255, 255, int(255 * a)), 1.5)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.save()
                p.rotate(k * 45.0)
                p.drawLine(QPointF(0, -r_in), QPointF(0, -r_out))
                p.restore()
            p.restore()

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
