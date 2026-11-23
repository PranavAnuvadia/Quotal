"""
Scribe recording pill (PySide6) - live transcript, shown while LISTENING.

    pip install PySide6 sounddevice numpy
    python scribe_pill.py --demo                 # fake speech + fake transcript
    python scribe_pill.py --demo --autoplay      # loops hands-free (great for recordings)
    python scribe_pill.py --accent violet        # orange | ice | violet | mint | rose

Design
    A dark-glass caption card, docked at the bottom of the screen, that grows
    upward as you speak.

    Idle       : compact pill, the Quotal quote marks breathing
    Listening  : the quote marks slide to the top-left and hang like a
                 pull-quote. Your words appear to their right: each new word
                 ignites in the accent colour and cools to white, and a caret
                 blinks after it. Two lines are visible; older lines dim and
                 scroll away. Under the text a light rail swells with your
                 voice and the whole card glows from below
    Processing : collapses to a compact loader: the marks pulse in turn while
                 a light orbits the edge (set SHOW_TEXT_WHILE_PROCESSING = True
                 to keep the text on screen with a shimmer instead)

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # while enhancing
    pill.state_request.emit("idle")                      # collapses
    pill.set_accent("violet")

Same public API as the other pills (Pill, AudioSource, state_request), plus
text_request and state_changed.

Standalone controls
    click       idle -> recording -> processing -> idle
    drag        move the pill        right click   menu
"""
import sys
import math
import time
import random
import argparse
from collections import deque

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QObject, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPainterPath, QFont,
                           QFontMetricsF, QFontDatabase, QLinearGradient,
                           QRadialGradient, QConicalGradient, QPixmap,
                           QPolygonF, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier
SHOW_TEXT_WHILE_PROCESSING = False

IDLE_SIZE = (66.0, 30.0)       # compact pill
PROC_SIZE = (96.0, 34.0)       # compact loader
LISTEN_W = 196.0               # width while listening, before words arrive
PAD_L, PAD_R = 22.0, 24.0
PAD_T, PAD_B = 13.0, 9.0
LINE_H = 20.0
RAIL_H, RAIL_GAP = 12.0, 6.0   # voice rail height, gap between text and rail
MAX_LINES = 2
TEXT_W_MAX = 236.0             # wrap width
MIN_TEXT_W = 70.0
FONT_PX = 14
GLYPH_H_BIG, GLYPH_H_SMALL = 9.5, 11.0
GLYPH_ZONE = 21.0              # width reserved for the hanging quote marks
GLYPH_GAP = 9.0
MARGIN = 28                    # transparent room for shadow / glow
MARGIN_BOTTOM = 20             # distance from window bottom to the pill

HILL_POINTS = 41
HILL_SPAN = 0.30               # seconds of loudness history across the rail
ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
WORD_FADE = 0.28               # seconds for a new word to ease in
IGNITE = 0.60                  # seconds for a new word to cool from accent to white

ACCENTS = {
    "orange": dict(core=(255, 238, 214), glow=(255, 126, 36)),
    "ice":    dict(core=(228, 246, 255), glow=(70, 190, 255)),
    "violet": dict(core=(246, 232, 255), glow=(168, 110, 255)),
    "mint":   dict(core=(226, 255, 242), glow=(60, 235, 165)),
    "rose":   dict(core=(255, 232, 240), glow=(255, 84, 140)),
}
FONT_CHOICES = ["Inter", "SF Pro Text", "Segoe UI Variable Text", "Segoe UI",
                "Helvetica Neue", "Roboto", "Noto Sans", "Arial"]

# Quotal quote marks, traced from the icon (units: 1.0 = 100 icon px).
LEFT_MARK = [
    (-2.07, -0.922), (-2.05, -0.777), (-2.04, -0.75), (-2.033, -0.685), (-2.02, -0.65), (-1.975, -0.367),
    (-1.965, -0.35), (-1.58, -0.355), (-1.567, -0.365), (-1.575, -0.427), (-1.587, -0.472), (-1.587, -0.495),
    (-1.577, -0.507), (-1.502, -0.515), (-1.393, -0.512), (-1.295, -0.492), (-1.23, -0.465), (-1.153, -0.417),
    (-1.103, -0.378), (-1.05, -0.323), (-0.983, -0.225), (-0.917, -0.068), (-0.925, -0.048), (-0.96, -0.035),
    (-1.327, 0.025), (-1.335, 0.035), (-1.335, 0.055), (-1.32, 0.12), (-1.31, 0.228), (-1.333, 0.253),
    (-1.438, 0.26), (-1.448, 0.25), (-1.465, 0.188), (-1.472, 0.115), (-1.485, 0.07), (-1.498, 0.058),
    (-1.627, 0.083), (-1.673, 0.085), (-1.732, 0.1), (-1.817, 0.11), (-1.837, 0.125), (-1.827, 0.145),
    (-1.83, 0.215), (-1.808, 0.325), (-1.8, 0.395), (-1.785, 0.45), (-1.778, 0.517), (-1.762, 0.568),
    (-1.732, 0.75), (-1.702, 0.873), (-1.695, 0.963), (-1.683, 0.99), (-1.647, 0.998), (-1.59, 0.995),
    (-1.45, 0.96), (-1.065, 0.897), (-1.032, 0.882), (-0.955, 0.863), (-0.52, 0.818), (-0.297, 0.78),
    (-0.245, 0.76), (-0.223, 0.728), (-0.207, 0.68), (-0.203, 0.635), (-0.207, 0.54), (-0.223, 0.47),
    (-0.225, 0.427), (-0.24, 0.388), (-0.245, 0.335), (-0.26, 0.295), (-0.268, 0.233), (-0.282, 0.188),
    (-0.282, 0.163), (-0.305, 0.068), (-0.31, 0.02), (-0.325, -0.022), (-0.333, -0.07), (-0.345, -0.092),
    (-0.35, -0.128), (-0.412, -0.292), (-0.5, -0.453), (-0.605, -0.595), (-0.733, -0.723), (-0.853, -0.812),
    (-0.983, -0.885), (-1.14, -0.948), (-1.29, -0.985), (-1.43, -1.0), (-1.62, -0.995), (-1.853, -0.965),
    (-1.907, -0.95), (-1.962, -0.945),
]
RIGHT_MARK = [
    (0.203, -0.922), (0.2, -0.902), (0.21, -0.828), (0.22, -0.8), (0.23, -0.718), (0.242, -0.685),
    (0.25, -0.618), (0.26, -0.6), (0.273, -0.495), (0.285, -0.45), (0.29, -0.36), (0.3, -0.35),
    (0.7, -0.355), (0.71, -0.365), (0.71, -0.38), (0.688, -0.47), (0.688, -0.497), (0.705, -0.51),
    (0.877, -0.512), (0.922, -0.507), (0.988, -0.49), (1.035, -0.47), (1.125, -0.415), (1.23, -0.315),
    (1.298, -0.212), (1.34, -0.113), (1.345, -0.083), (1.357, -0.062), (1.345, -0.048), (1.312, -0.035),
    (0.943, 0.028), (0.94, 0.065), (0.96, 0.17), (0.963, 0.233), (0.94, 0.253), (0.875, 0.255),
    (0.843, 0.263), (0.828, 0.25), (0.815, 0.223), (0.807, 0.145), (0.797, 0.117), (0.792, 0.072),
    (0.78, 0.058), (0.487, 0.105), (0.44, 0.117), (0.44, 0.205), (0.45, 0.24), (0.46, 0.325),
    (0.472, 0.365), (0.48, 0.435), (0.495, 0.487), (0.502, 0.55), (0.52, 0.608), (0.57, 0.882),
    (0.58, 0.91), (0.583, 0.963), (0.598, 0.99), (0.618, 0.998), (0.69, 0.993), (0.733, 0.978),
    (0.792, 0.97), (0.805, 0.963), (0.89, 0.953), (1.045, 0.922), (1.14, 0.912), (1.205, 0.895),
    (1.23, 0.88), (1.302, 0.863), (1.74, 0.818), (1.905, 0.787), (1.97, 0.782), (2.025, 0.762),
    (2.055, 0.723), (2.067, 0.662), (2.067, 0.575), (2.055, 0.46), (1.998, 0.228), (1.97, 0.052),
    (1.927, -0.115), (1.917, -0.13), (1.907, -0.175), (1.865, -0.282), (1.8, -0.41), (1.698, -0.56),
    (1.633, -0.635), (1.548, -0.718), (1.397, -0.828), (1.28, -0.89), (1.145, -0.945), (0.98, -0.985),
    (0.85, -1.0), (0.647, -0.995), (0.417, -0.965), (0.273, -0.935), (0.217, -0.93),
]

SAMPLE_RATE = 16000
BLOCK = 512                    # 32 ms


def _poly(pts):
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    path.closeSubpath()
    return path


_all_y = [y for _, y in LEFT_MARK + RIGHT_MARK]
GLYPH_UNITS_H = max(_all_y) - min(_all_y)
LEFT_PATH, RIGHT_PATH = _poly(LEFT_MARK), _poly(RIGHT_MARK)
GLYPH_UNITS_W = max(x for x, _ in RIGHT_MARK) - min(x for x, _ in LEFT_MARK)


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


def ease_out(x):
    x = clamp01(x)
    return 1.0 - (1.0 - x) ** 3


def mix(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)),
            int(lerp(c1[2], c2[2], t)))


def smooth_path(pts):
    """Quadratic-smoothed curve through a list of QPointF."""
    path = QPainterPath(pts[0])
    for i in range(1, len(pts) - 1):
        mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2,
                      (pts[i].y() + pts[i + 1].y()) / 2)
        path.quadTo(pts[i], mid)
    path.lineTo(pts[-1])
    return path


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe: change state
    text_request = Signal(str)           # thread-safe: set the live text
    state_changed = Signal(str)          # emitted after every state change

    def __init__(self, audio, auto_finish=0, accent="orange"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"

        self.pw = IDLE_SIZE[0] ; self.pwv = 0.0      # animated pill size
        self.ph = IDLE_SIZE[1] ; self.phv = 0.0
        self.pr = 0.0 ; self.prv = 0.0               # processing spring
        self.cur = 0.0 ; self.curv = 0.0             # animated "latest line" index

        self.level = 0.0
        self.hist = deque()
        self.tfade = 0.0                             # text visibility

        self.words = []                              # [{'t': str, 'born': float}]
        self.lines = []                              # [[(word_idx, x, w), ...], ...]
        self._wcache = {}

        # typography
        fams = set(QFontDatabase.families())
        family = next((f for f in FONT_CHOICES if f in fams), None)
        self.font = QFont(family) if family else QFont()
        self.font.setPixelSize(FONT_PX)
        self.font.setWeight(QFont.Medium)
        self.fm = QFontMetricsF(self.font)
        self.space = self.fm.horizontalAdvance(" ")

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        self.rec_min_h = PAD_T + LINE_H + RAIL_GAP + RAIL_H + PAD_B
        max_w = PAD_L + GLYPH_ZONE + GLYPH_GAP + TEXT_W_MAX + 6 + PAD_R
        max_h = PAD_T + LINE_H * MAX_LINES + RAIL_GAP + RAIL_H + PAD_B
        self.resize(int((max_w + MARGIN * 2) * SCALE),
                    int((max_h + MARGIN + MARGIN_BOTTOM) * SCALE))

        self.set_accent(accent)
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- accent ----
    def set_accent(self, name):
        """'orange' | 'ice' | 'violet' | 'mint' | 'rose'. Safe to call any time."""
        self.accent = name if name in ACCENTS else "orange"
        a = ACCENTS[self.accent]
        self.c_core, self.c_glow = a["core"], a["glow"]
        size = 40                                    # one soft glow sprite
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        r, g, b = self.c_glow
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, QColor(r, g, b, 190))
        grad.setColorAt(0.30, QColor(r, g, b, 60))
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
        prev = self.state
        self.state = state
        if state == "recording":
            self.audio.start()
            if prev != "recording":          # fresh dictation: start from blank
                self.words, self.lines, self.cur = [], [], 0.0
        else:
            self.audio.stop()
        if state == "processing" and self.auto_finish:
            QTimer.singleShot(int(self.auto_finish * 1000), self._auto_finish)
        self.state_changed.emit(state)

    def _auto_finish(self):
        if self.state == "processing":
            self.set_state("idle")

    def place_default(self):
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.x() + (g.width() - self.width()) // 2,
                  g.bottom() - self.height() - 4)

    # ---- text ----
    def set_text(self, text):
        """Replace the live text cleanly without glitching or re-animating existing words."""
        new = text.split()
        if not new:
            self.words, self.lines, self.cur = [], [], 0.0
            self._layout()
            return
        old = self.words
        now = time.perf_counter()

        def _norm(w):
            return "".join(c for c in w.lower() if c.isalnum())

        words = []
        for j, w in enumerate(new):
            if j < len(old) and (_norm(old[j]["t"]) == _norm(w) or old[j]["t"] == w):
                words.append({"t": w, "born": old[j]["born"]})
            else:
                words.append({"t": w, "born": now})
        self.words = words
        self._layout()

    def _wwidth(self, word):
        w = self._wcache.get(word)
        if w is None:
            w = self.fm.horizontalAdvance(word)
            if len(self._wcache) > 2000:
                self._wcache.clear()
            self._wcache[word] = w
        return w

    def _layout(self):
        lines, cur, x = [], [], 0.0
        for i, wd in enumerate(self.words):
            ww = self._wwidth(wd["t"])
            if cur and x + ww > TEXT_W_MAX:
                lines.append(cur)
                cur, x = [], 0.0
            cur.append((i, x, ww))
            x += ww + self.space
        if cur:
            lines.append(cur)
        self.lines = lines

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt, k=240.0, c=23.0):
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

    def _text_mode(self):
        return self.recording or (SHOW_TEXT_WHILE_PROCESSING
                                  and self.state == "processing")

    def _target_size(self):
        if not self._text_mode() and self.tfade < 0.3:
            return PROC_SIZE if self.state == "processing" else IDLE_SIZE
        # Keep width stable while listening so existing words don't shift horizontally
        w = PAD_L + GLYPH_ZONE + GLYPH_GAP + TEXT_W_MAX + 6 + PAD_R
        n = len(self.lines)
        h = PAD_T + max(1, min(n, MAX_LINES)) * LINE_H + RAIL_GAP + RAIL_H + PAD_B
        return (w, h)

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 1 / 30)
        self.last = now

        self.pr, self.prv = self._spring(
            self.pr, self.prv, 1.0 if self.state == "processing" else 0.0, dt)

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.9:
            self.hist.popleft()

        # text visibility; clear it once the card has let go of it
        if self._text_mode():
            self.tfade = min(1.0, self.tfade + dt * 8.0)
        else:
            self.tfade = max(0.0, self.tfade - dt * 6.0)
            if self.tfade <= 0.0 and self.words and self.state != "recording":
                self.words, self.lines, self.cur = [], [], 0.0

        tw, th = self._target_size()
        self.pw, self.pwv = self._spring(self.pw, self.pwv, tw, dt, 300.0, 27.0)
        self.ph, self.phv = self._spring(self.ph, self.phv, th, dt, 300.0, 27.0)
        target_line = float(max(0, len(self.lines) - 1))
        self.cur, self.curv = self._spring(self.cur, self.curv, target_line, dt,
                                           220.0, 24.0)
        self.update()

    # ---- painting helpers ----
    def _glow_at(self, p, x, y, radius, opacity):
        if opacity <= 0.01:
            return
        p.setOpacity(min(1.0, opacity))
        p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                     self.glow, QRectF(0, 0, self.glow_size * 2, self.glow_size * 2))
        p.setOpacity(1.0)

    def _rail(self, p, xl, xr, base, maxh, now, t, alpha):
        """Voice rail: a soft hill that swells with loudness, plus an echo."""
        width = xr - xl
        if width < 12 or alpha < 0.02:
            return
        gr, gg, gb = self.c_glow
        cr, cg, cb = self.c_core
        n = HILL_POINTS

        def build(delay, power, amp, ripple, mixv):
            pts = []
            for j in range(n):
                u = (j - (n - 1) / 2) / ((n - 1) / 2)
                d = abs(u)
                prof = math.cos(d * math.pi / 2) ** power
                lv = mixv * self.level + (1.0 - mixv) * \
                    self._sample(now - d * HILL_SPAN - delay)
                wob = 1.0 + ripple * lv * math.sin(j * 0.75 - t * 5.5)
                hh = maxh * amp * (0.05 + 0.95 * lv) * prof * wob
                pts.append(QPointF(xl + width * j / (n - 1), base - hh))
            return pts

        back = build(0.13, 0.8, 0.74, 0.0, 0.0)
        front = build(0.0, 1.15, 1.0, 0.12, 0.6)

        ground = QLinearGradient(xl, 0, xr, 0)
        ground.setColorAt(0.0, QColor(255, 255, 255, 0))
        ground.setColorAt(0.5, QColor(255, 255, 255, int(34 * alpha)))
        ground.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(ground), 1.0))
        p.drawLine(QPointF(xl, base + 0.5), QPointF(xr, base + 0.5))

        for pts, fill_a, stroke_a, wpen, hot in (
                (back, 40, 0.45, 1.0, False), (front, 96, 1.0, 1.6, True)):
            top = smooth_path(pts)
            area = QPainterPath(top)
            area.lineTo(QPointF(xr, base))
            area.lineTo(QPointF(xl, base))
            area.closeSubpath()
            fill = QLinearGradient(0, base - maxh, 0, base)
            fill.setColorAt(0.0, QColor(gr, gg, gb, int(fill_a * alpha)))
            fill.setColorAt(1.0, QColor(gr, gg, gb, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(fill))
            p.drawPath(area)

            sg = QLinearGradient(xl, 0, xr, 0)
            sg.setColorAt(0.0, QColor(gr, gg, gb, 0))
            sg.setColorAt(0.2, QColor(gr, gg, gb, int(255 * alpha * 0.9)))
            mid = (cr, cg, cb) if hot else (gr, gg, gb)
            sg.setColorAt(0.5, QColor(mid[0], mid[1], mid[2], int(255 * alpha)))
            sg.setColorAt(0.8, QColor(gr, gg, gb, int(255 * alpha * 0.9)))
            sg.setColorAt(1.0, QColor(gr, gg, gb, 0))
            pen = QPen(QBrush(sg), wpen)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setOpacity(stroke_a)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(top)
            p.setOpacity(1.0)

    def _shimmer_pen(self, x0, x1, t, alpha=255):
        """Pen that is invisible except for a moving band of light."""
        c = (t * 0.85) % 1.5 - 0.25
        g = QLinearGradient(x0, 0, x1, 0)
        w = 0.17
        g.setColorAt(0.0, QColor(255, 255, 255, 0))
        g.setColorAt(max(0.0, min(1.0, c - w)), QColor(255, 255, 255, 0))
        g.setColorAt(max(0.0, min(1.0, c)), QColor(255, 244, 228, alpha))
        g.setColorAt(max(0.0, min(1.0, c + w)), QColor(255, 255, 255, 0))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        return QPen(QBrush(g), 1.0)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        cx = w / 2
        now = time.perf_counter()
        t = now - self.t0
        pr = clamp01(self.pr)
        pw, ph = self.pw, self.ph
        rect = QRectF(cx - pw / 2, h - MARGIN_BOTTOM - ph, pw, ph)
        rr = min(ph / 2, 25.0)
        gr, gg, gb = self.c_glow
        cr, cg, cb = self.c_core
        big = clamp01((ph - 38.0) / (self.rec_min_h - 38.0))     # text layout amount
        lv = self.level * big
        act = max(lv * 0.9 + 0.12 * big, pr * 0.5)

        # accent light cast below the card
        if act > 0.02:
            p.save()
            p.translate(cx, rect.bottom() + 5)
            p.scale(1.0, 0.17)
            R = pw * 0.58
            pool = QRadialGradient(QPointF(0, 0), R)
            pool.setColorAt(0.0, QColor(gr, gg, gb, int(86 * act)))
            pool.setColorAt(1.0, QColor(gr, gg, gb, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(pool))
            p.drawEllipse(QPointF(0, 0), R, R)
            p.restore()

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            rad = min(r.height() / 2, rr + g)
            p.setBrush(QColor(0, 0, 0, int(max(0, 26 - i * 2.8))))
            p.drawRoundedRect(r, rad, rad)

        # dark glass body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(38, 40, 48))
        grad.setColorAt(0.5, QColor(16, 17, 22))
        grad.setColorAt(1.0, QColor(8, 9, 12))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        p.save()
        p.setClipPath(body)

        # voice-driven glow rising from the bottom of the card
        if big > 0.02:
            p.save()
            p.translate(cx, rect.bottom() + 2)
            p.scale(1.0, 0.62)
            R = pw * 0.62
            up = QRadialGradient(QPointF(0, 0), R)
            up.setColorAt(0.0, QColor(gr, gg, gb, int((34 + 120 * lv) * big)))
            up.setColorAt(1.0, QColor(gr, gg, gb, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(up))
            p.drawEllipse(QPointF(0, 0), R, R)
            p.restore()

        # --- text block geometry -------------------------------------------
        text_left = rect.left() + PAD_L + GLYPH_ZONE + GLYPH_GAP
        text_top = rect.top() + PAD_T
        text_w = max(10.0, rect.right() - PAD_R - text_left + 8)
        tvis = self.tfade * clamp01((big - 0.4) / 0.5)

        # --- quote marks: centred when compact, hanging when listening ---------
        gh = lerp(GLYPH_H_SMALL, GLYPH_H_BIG, big)
        k = gh / GLYPH_UNITS_H
        gw = GLYPH_UNITS_W * k
        gx_hang = rect.left() + PAD_L + gw / 2
        gy_hang = text_top + LINE_H * 0.40
        gx = lerp(cx, gx_hang, big)
        gy = lerp(rect.center().y(), gy_hang, big)
        w_idle = (1 - big) * (1 - pr)
        idle_l = 0.80 + 0.12 * math.sin(t * 1.6)
        idle_r = 0.80 + 0.12 * math.sin(t * 1.6 + 0.9)
        ph_ = t * 5.2
        proc_l = 0.38 + 0.62 * (0.5 + 0.5 * math.sin(ph_))
        proc_r = 0.38 + 0.62 * (0.5 + 0.5 * math.sin(ph_ + math.pi))
        a_l = idle_l * w_idle + proc_l * (1 - big) * pr + big
        a_r = idle_r * w_idle + proc_r * (1 - big) * pr + big
        gcol = mix((246, 246, 249), (cr, cg, cb), big)
        if big > 0.05:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            self._glow_at(p, gx, gy, 15 + 8 * lv, 0.45 * big * (0.6 + 0.4 * lv))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(Qt.NoPen)
        kk = k * (1.0 + 0.06 * lv)
        for path, a, bob in ((LEFT_PATH, a_l, -1.0 * pr * max(0.0, math.sin(ph_)) * (1 - big)),
                             (RIGHT_PATH, a_r, -1.0 * pr * max(0.0, math.sin(ph_ + math.pi)) * (1 - big))):
            p.save()
            p.translate(gx, gy + bob)
            p.scale(kk, kk)
            p.setBrush(QColor(gcol[0], gcol[1], gcol[2], int(255 * clamp01(a))))
            p.drawPath(path)
            p.restore()

        # --- live text -----------------------------------------------------
        p.setFont(self.font)
        fm = self.fm
        asc = fm.ascent()
        off = (LINE_H - fm.height()) / 2 + asc
        if tvis > 0.01:
            p.save()
            p.setClipRect(QRectF(text_left - 3, rect.top() + 3, text_w + 6,
                                 PAD_T + LINE_H * MAX_LINES + 2))
            if not self.lines:
                label = "Listening" if self.recording else "Enhancing"
                y = text_top + off
                p.setPen(QColor(150, 156, 170, int(170 * tvis)))
                p.drawText(QPointF(text_left, y), label)
                p.setPen(self._shimmer_pen(text_left, text_left + 90, t, int(255 * tvis)))
                p.drawText(QPointF(text_left, y), label)
            else:
                first = max(0.0, self.cur - (MAX_LINES - 1))
                last_idx = len(self.words) - 1
                for li, line in enumerate(self.lines):
                    row = li - first
                    if row < -1.0 or row > MAX_LINES:
                        continue
                    age = self.cur - li
                    la = (1.0 - 0.50 * clamp01(age)) * clamp01(1.0 + age) \
                        * clamp01(row + 1.0) * clamp01(MAX_LINES - row)
                    if la < 0.01:
                        continue
                    y = text_top + row * LINE_H + off
                    for wi, wx, ww in line:
                        wd = self.words[wi]
                        age_w = max(0.0, now - wd["born"])
                        e = ease_out(age_w / WORD_FADE)
                        ig = math.exp(-age_w / IGNITE)
                        a = la * max(0.25, e) * tvis
                        yy = y + (1.0 - e) * 1.5
                        x = text_left + wx
                        if ig > 0.04:                      # ignition glow
                            hr = QRectF(x - 4.0, yy - asc - 1.0, ww + 8.0, fm.height() + 2.0)
                            p.setPen(Qt.NoPen)
                            p.setBrush(QColor(gr, gg, gb, int(34 * ig * a)))
                            p.drawRoundedRect(hr.adjusted(-3, -2, 3, 2), 8.0, 8.0)
                            p.setBrush(QColor(gr, gg, gb, int(70 * ig * a)))
                            p.drawRoundedRect(hr, 6.0, 6.0)
                        base = 246 if age < 0.5 else 228
                        col = mix((base, base + 1, 252), (cr, cg, cb), 0.85 * ig)
                        p.setPen(QColor(0, 0, 0, int(120 * a)))          # soft text shadow
                        p.drawText(QPointF(x, yy + 1.1), wd["t"])
                        p.setPen(QColor(col[0], col[1], col[2], int(255 * a)))
                        p.drawText(QPointF(x, yy), wd["t"])

                # caret after the newest word
                if self.recording:
                    li = len(self.lines) - 1
                    wi, wx, ww = self.lines[li][-1]
                    row = li - first
                    blink = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * 5.6))
                    cxp = text_left + wx + ww + 4.0
                    cyp = text_top + row * LINE_H + LINE_H / 2
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(gr, gg, gb, int(255 * blink * tvis)))
                    p.drawRoundedRect(QRectF(cxp, cyp - 8.0, 1.8, 16.0), 0.9, 0.9)

                if self.state == "processing":              # shimmer (optional mode)
                    p.setPen(self._shimmer_pen(text_left, text_left + text_w, t, int(255 * tvis)))
                    for li, line in enumerate(self.lines):
                        row = li - first
                        if row < -0.5 or row > MAX_LINES - 0.5:
                            continue
                        y = text_top + row * LINE_H + off
                        for wi, wx, ww in line:
                            p.drawText(QPointF(text_left + wx, y), self.words[wi]["t"])
            p.restore()

        # --- voice rail ----------------------------------------------------
        if big > 0.05:
            base = rect.bottom() - PAD_B - 1.0
            self._rail(p, rect.left() + 22, rect.right() - 22, base,
                       RAIL_H + 4.0, now, t, big * (1 - pr))

        # glass sheen
        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 24))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # hairline edge, tinted by activity
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 96))
        edge.setColorAt(0.5, QColor(255, 255, 255, 30))
        edge.setColorAt(1.0, QColor(255, 255, 255, 18))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)
        inner = QLinearGradient(0, rect.top(), 0, rect.bottom())
        inner.setColorAt(0.0, QColor(255, 255, 255, 34))
        inner.setColorAt(0.35, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(inner), 1.0))
        p.drawRoundedRect(rect.adjusted(1.6, 1.6, -1.6, -1.6), max(1.0, rr - 1.5), max(1.0, rr - 1.5))
        if act > 0.02:
            rim = QLinearGradient(0, rect.top(), 0, rect.bottom())
            rim.setColorAt(0.0, QColor(gr, gg, gb, 0))
            rim.setColorAt(1.0, QColor(gr, gg, gb, int(150 * min(1.0, act))))
            p.setPen(QPen(QBrush(rim), 1.0))
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)

        # processing: an accent light orbiting the compact loader
        if pr > 0.02:
            ang = (-t * 210.0) % 360.0
            ctr = rect.center()
            for width_, alpha in ((7.0, 0.09), (3.6, 0.22), (1.7, 1.0)):
                cgr = QConicalGradient(QPointF(ctr.x(), ctr.y()), ang)
                cgr.setColorAt(0.0, QColor(gr, gg, gb, int(235 * alpha * pr)))
                cgr.setColorAt(0.09, QColor(gr, gg, gb, int(110 * alpha * pr)))
                cgr.setColorAt(0.24, QColor(gr, gg, gb, 0))
                cgr.setColorAt(1.0, QColor(gr, gg, gb, 0))
                pen = QPen(QBrush(cgr), width_)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
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
            m.addAction(f"Accent: {name}", lambda n=name: self.set_accent(n))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


# ------------------------------------------------------------ demo content --
RAW = ("hey team quick update on the quotal launch the new recording pill ships "
       "tomorrow and i want everyone to try dictation mode before we go live")
MISHEAR = {"quotal": "quota", "pill": "bill", "ships": "chips", "dictation": "dictionary"}


class DemoTranscriber(QObject):
    """Pretends to be a streaming speech-to-text engine so you can see the pill
    work without any ASR installed. Replace with your real ASR callback:
        asr.on_partial = lambda text: pill.text_request.emit(text)
    """

    def __init__(self, pill, autoplay=False):
        super().__init__(pill)
        self.pill = pill
        self.autoplay = autoplay
        self.frames = []
        self.idx = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._step)
        pill.state_changed.connect(self._on_state)
        if autoplay:
            QTimer.singleShot(900, lambda: pill.state_request.emit("recording"))

    def _build(self):
        rnd = random.Random()
        frames, shown = [], []
        for w in RAW.split():
            if w in MISHEAR:                     # the "ASR" hears it wrong first
                frames.append((rnd.uniform(0.20, 0.32), " ".join(shown + [MISHEAR[w]])))
                frames.append((0.26, " ".join(shown + [w])))
            else:
                frames.append((rnd.uniform(0.17, 0.34), " ".join(shown + [w])))
            shown.append(w)
        return frames

    def _on_state(self, st):
        self.timer.stop()
        if st == "recording":
            self.frames, self.idx = self._build(), 0
            self.timer.start(900)                # a "Listening" moment first
        elif st == "processing":
            QTimer.singleShot(2200, lambda: self.pill.state_request.emit("idle")
                              if self.pill.state == "processing" else None)
        elif st == "idle" and self.autoplay:
            QTimer.singleShot(1800, lambda: self.pill.state_request.emit("recording"))

    def _step(self):
        if not self.pill.recording:
            return
        if self.idx < len(self.frames):
            delay, text = self.frames[self.idx]
            self.pill.text_request.emit(text)
            self.idx += 1
            self.timer.start(int(delay * 1000))
        elif self.autoplay:                      # sentence finished: stop dictating
            QTimer.singleShot(1100, lambda: self.pill.state_request.emit("processing"))


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="fake audio AND a fake streaming transcript")
    ap.add_argument("--autoplay", action="store_true",
                    help="with --demo: loop record -> process -> idle forever")
    ap.add_argument("--accent", choices=sorted(ACCENTS), default="orange")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, accent=args.accent)
    if args.demo:
        DemoTranscriber(pill, autoplay=args.autoplay)
    pill.place_default()
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
