"""
Neon recording pill (PySide6) - a live transcript that glows like a neon sign.

    pip install PySide6 sounddevice numpy
    python neon_pill.py --demo                  # fake speech + fake transcript
    python neon_pill.py --demo --autoplay       # loops hands-free (great for recordings)
    python neon_pill.py --theme cyber           # sunset | cyber | toxic | rose | ice

Design
    A near-black glass card. Everything that is lit is neon.

    Idle       : compact pill, a dim neon tube, your Quotal quote marks on standby
    Listening  : the tube border flickers on, the quote marks hang at the top
                 left, and your words appear as neon: each word ignites with a
                 real tube flicker and an over-bright flash, then settles into a
                 steady glow. A caret blinks after the newest word. Two lines
                 show at once; older lines run at lower power and scroll away.
                 Under the text a neon voice line swells with your loudness
    Processing : compact loader: the marks pulse in turn while a bright arc
                 runs around the tube

Words are baked to glow sprites once, so each frame only blits a few pixmaps.

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # while enhancing
    pill.state_request.emit("idle")
    pill.set_theme("cyber")

Same public API as the other pills (Pill, AudioSource, state_request), plus
text_request and state_changed.
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
                           QPolygonF, QTransform, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (66.0, 30.0)       # compact pill
PROC_SIZE = (96.0, 34.0)       # compact loader
LISTEN_W = 204.0               # width while listening, before words arrive
PAD_L, PAD_R = 24.0, 26.0
PAD_T, PAD_B = 14.0, 10.0
LINE_H = 22.0
RAIL_H, RAIL_GAP = 12.0, 7.0   # voice line height, gap between text and line
MAX_LINES = 2
TEXT_W_MAX = 240.0             # wrap width
MIN_TEXT_W = 70.0
FONT_PX = 15
GLYPH_H = 11.0
GLYPH_ZONE, GLYPH_GAP = 22.0, 10.0
SPR_PAD = 11.0                 # glow bleed around baked sprites
MARGIN = 30                    # transparent room for shadow / glow
MARGIN_BOTTOM = 22

HILL_POINTS = 41
HILL_SPAN = 0.30               # seconds of loudness history across the voice line
ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
IGNITE = 0.55                  # seconds for the over-bright flash to cool

# text colour, hot core colour, tube (border) colour
THEMES = {
    "sunset": dict(text=(255, 146, 64),  hot=(255, 243, 226), edge=(255, 60, 160)),
    "cyber":  dict(text=(64, 222, 255),  hot=(232, 252, 255), edge=(164, 92, 255)),
    "toxic":  dict(text=(166, 255, 84),  hot=(246, 255, 228), edge=(36, 232, 200)),
    "rose":   dict(text=(255, 104, 160), hot=(255, 238, 244), edge=(255, 168, 64)),
    "ice":    dict(text=(150, 200, 255), hot=(246, 250, 255), edge=(120, 120, 255)),
}
FONT_CHOICES = ["Inter", "SF Pro Display", "Segoe UI Variable Display", "Segoe UI",
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
GLYPH_UNITS_W = max(x for x, _ in RIGHT_MARK) - min(x for x, _ in LEFT_MARK)
LEFT_PATH, RIGHT_PATH = _poly(LEFT_MARK), _poly(RIGHT_MARK)

# a tube catching fire: (time, on/off level)
FLICK = [(0.00, 0.0), (0.06, 1.0), (0.12, 0.10), (0.19, 1.0), (0.25, 0.30), (0.31, 1.0)]


def flick(age):
    if age < 0:
        return 0.0
    if age >= 0.34:
        return 1.0
    a = 0.0
    for tt, v in FLICK:
        if age >= tt:
            a = v
    return a


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

    def __init__(self, audio, auto_finish=0, theme="sunset"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"
        self.t_listen = -10.0            # when the tube last lit up

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
        self._sprites = {}

        fams = set(QFontDatabase.families())
        family = next((f for f in FONT_CHOICES if f in fams), None)
        self.font = QFont(family) if family else QFont()
        self.font.setPixelSize(FONT_PX)
        self.font.setWeight(QFont.DemiBold)
        self.fm = QFontMetricsF(self.font)
        self.space = self.fm.horizontalAdvance(" ")
        self.text_off = (LINE_H - self.fm.height()) / 2 + self.fm.ascent()

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

        self.set_theme(theme)
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- theme / sprites ----
    def set_theme(self, name):
        """'sunset' | 'cyber' | 'toxic' | 'rose' | 'ice'. Safe to call any time."""
        self.theme = name if name in THEMES else "sunset"
        th = THEMES[self.theme]
        self.c_text, self.c_hot, self.c_edge = th["text"], th["hot"], th["edge"]
        self._sprites.clear()
        self._bake_glyphs()
        size = 40                                    # soft glow sprite
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        r, g, b = self.c_text
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

    def _neon_pixmap(self, w, h, path):
        """Bake a path into a glowing neon sprite (glow layers + hot core)."""
        pm = QPixmap(int(math.ceil(w * 2)), int(math.ceil(h * 2)))
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        tr, tg, tb = self.c_text
        hr, hg, hb = self.c_hot
        for width, alpha in ((10.0, 0.05), (6.5, 0.09), (3.8, 0.19), (2.0, 0.42)):
            pen = QPen(QColor(tr, tg, tb, int(255 * alpha)), width)
            pen.setJoinStyle(Qt.RoundJoin)
            q.strokePath(path, pen)
        q.fillPath(path, QColor(hr, hg, hb))
        pen = QPen(QColor(tr, tg, tb, 210), 0.8)
        pen.setJoinStyle(Qt.RoundJoin)
        q.strokePath(path, pen)
        q.end()
        return pm

    def _bake_glyphs(self):
        k = GLYPH_H / GLYPH_UNITS_H
        w = GLYPH_UNITS_W * k + 2 * SPR_PAD
        h = GLYPH_UNITS_H * k + 2 * SPR_PAD
        tf = QTransform().translate(w / 2, h / 2).scale(k, k)
        self.glyph_l = self._neon_pixmap(w, h, tf.map(LEFT_PATH))
        self.glyph_r = self._neon_pixmap(w, h, tf.map(RIGHT_PATH))
        self.glyph_wh = (w, h)

    def _word_sprite(self, word, ww):
        key = word
        pm = self._sprites.get(key)
        if pm is None:
            if len(self._sprites) > 500:
                self._sprites.clear()
                self._bake_glyphs()
            path = QPainterPath()
            path.addText(SPR_PAD, SPR_PAD + self.text_off, self.font, word)
            pm = self._neon_pixmap(ww + 2 * SPR_PAD, LINE_H + 2 * SPR_PAD, path)
            self._sprites[key] = pm
        return pm

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
            self.t_listen = time.perf_counter()
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

    def _target_size(self):
        if not self.recording and self.tfade < 0.3:
            return PROC_SIZE if self.state == "processing" else IDLE_SIZE
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

        if self.recording:
            self.tfade = min(1.0, self.tfade + dt * 8.0)
        else:
            self.tfade = max(0.0, self.tfade - dt * 6.0)
            if self.tfade <= 0.0 and self.words:
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

    def _tube(self, p, rect, rr, brightness):
        """Neon tube around the card."""
        if brightness < 0.02:
            return
        er, eg, eb = self.c_edge
        hr, hg, hb = self.c_hot
        r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(Qt.NoBrush)
        for width, alpha in ((9.0, 0.05), (5.0, 0.12), (2.6, 0.26), (1.5, 0.95)):
            p.setPen(QPen(QColor(er, eg, eb, int(255 * alpha * brightness)), width))
            p.drawRoundedRect(r, rr, rr)
        p.setPen(QPen(QColor(hr, hg, hb, int(120 * brightness)), 0.6))
        p.drawRoundedRect(r, rr, rr)

    def _voice_line(self, p, xl, xr, base, maxh, now, t, alpha):
        """Neon voice line: swells with loudness, with a two-tone echo."""
        width = xr - xl
        if width < 12 or alpha < 0.02:
            return
        n = HILL_POINTS
        tr, tg, tb = self.c_text
        er, eg, eb = self.c_edge
        hr, hg, hb = self.c_hot

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

        def gpen(col, hot, a, wd):
            g = QLinearGradient(xl, 0, xr, 0)
            g.setColorAt(0.0, QColor(col[0], col[1], col[2], 0))
            g.setColorAt(0.2, QColor(col[0], col[1], col[2], int(255 * a * 0.9)))
            g.setColorAt(0.5, QColor(hot[0], hot[1], hot[2], int(255 * a)))
            g.setColorAt(0.8, QColor(col[0], col[1], col[2], int(255 * a * 0.9)))
            g.setColorAt(1.0, QColor(col[0], col[1], col[2], 0))
            pen = QPen(QBrush(g), wd)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            return pen

        back = smooth_path(build(0.13, 0.8, 0.74, 0.0, 0.0))
        front = smooth_path(build(0.0, 1.15, 1.0, 0.12, 0.6))
        p.setBrush(Qt.NoBrush)
        for path, col, hot, passes in (
                (back, (er, eg, eb), (er, eg, eb), ((3.2, 0.10), (1.1, 0.55))),
                (front, (tr, tg, tb), (hr, hg, hb),
                 ((6.0, 0.10), (3.2, 0.24), (1.5, 1.0)))):
            for wd, a in passes:
                p.setPen(gpen(col, hot, a * alpha, wd))
                p.drawPath(path)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        cx = w / 2
        now = time.perf_counter()
        t = now - self.t0
        pr = clamp01(self.pr)
        pw, ph = self.pw, self.ph
        rect = QRectF(cx - pw / 2, h - MARGIN_BOTTOM - ph, pw, ph)
        rr = min(ph / 2, 25.0)
        tr, tg, tb = self.c_text
        er, eg, eb = self.c_edge
        big = clamp01((ph - 38.0) / (self.rec_min_h - 38.0))     # listening layout amount
        lv = self.level * big

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            rad = min(r.height() / 2, rr + g)
            p.setBrush(QColor(0, 0, 0, int(max(0, 30 - i * 3.2))))
            p.drawRoundedRect(r, rad, rad)

        # near-black glass body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(22, 23, 32))
        grad.setColorAt(0.5, QColor(10, 11, 17))
        grad.setColorAt(1.0, QColor(5, 5, 9))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        p.save()
        p.setClipPath(body)

        # neon spill on the glass: warm bloom behind the text, tube-coloured below
        if big > 0.02:
            for (ox, oy, sy, R, col, a) in (
                    (rect.center().x(), rect.top() + PAD_T + LINE_H * 0.8, 0.55,
                     pw * 0.55, (tr, tg, tb), 14 + 44 * lv),
                    (rect.center().x(), rect.bottom() + 2, 0.6,
                     pw * 0.6, (er, eg, eb), 26 + 66 * lv)):
                p.save()
                p.translate(ox, oy)
                p.scale(1.0, sy)
                bloom = QRadialGradient(QPointF(0, 0), R)
                bloom.setColorAt(0.0, QColor(col[0], col[1], col[2], int(a * big)))
                bloom.setColorAt(1.0, QColor(col[0], col[1], col[2], 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(bloom))
                p.drawEllipse(QPointF(0, 0), R, R)
                p.restore()

        text_left = rect.left() + PAD_L + GLYPH_ZONE + GLYPH_GAP
        text_top = rect.top() + PAD_T
        text_w = max(10.0, rect.right() - PAD_R - text_left + 8)
        tvis = self.tfade * clamp01((big - 0.4) / 0.5)

        # --- neon quote marks: centred when compact, hanging when listening ----
        gw, gh = self.glyph_wh
        k = lerp(1.0, 0.95, big) * (1.0 + 0.05 * lv)
        gx_hang = rect.left() + PAD_L + (gw - 2 * SPR_PAD) / 2
        gy_hang = text_top + LINE_H * 0.42
        gx = lerp(cx, gx_hang, big)
        gy = lerp(rect.center().y(), gy_hang, big)
        ph_ = t * 5.2
        standby = 0.62 + 0.18 * math.sin(t * 1.6)
        w_idle = (1 - big) * (1 - pr)
        a_l = standby * w_idle + (0.38 + 0.62 * (0.5 + 0.5 * math.sin(ph_))) * (1 - big) * pr + big
        a_r = standby * w_idle + (0.38 + 0.62 * (0.5 + 0.5 * math.sin(ph_ + math.pi))) * (1 - big) * pr + big
        glyph_flick = flick(now - self.t_listen) if self.recording else 1.0
        for pm, a in ((self.glyph_l, a_l), (self.glyph_r, a_r)):
            p.setOpacity(clamp01(a * (0.35 + 0.65 * glyph_flick if big > 0.5 else 1.0)))
            p.drawPixmap(QRectF(gx - gw * k / 2, gy - gh * k / 2, gw * k, gh * k), pm,
                         QRectF(0, 0, pm.width(), pm.height()))
        p.setOpacity(1.0)

        # --- live text: neon sprites -------------------------------------------
        if tvis > 0.01:
            p.save()
            p.setClipRect(QRectF(text_left - 14, rect.top() + 2, text_w + 28,
                                 PAD_T + LINE_H * MAX_LINES + RAIL_GAP * 0.5))
            if not self.lines:
                f = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 3.2))     # tube humming
                pm = self._word_sprite("Listening", self._wwidth("Listening"))
                p.setOpacity(clamp01(f * tvis * flick(now - self.t_listen)))
                p.drawPixmap(QPointF(text_left - SPR_PAD, text_top - SPR_PAD), pm)
                p.setOpacity(1.0)
            else:
                first = max(0.0, self.cur - (MAX_LINES - 1))
                last_idx = len(self.words) - 1
                for li, line in enumerate(self.lines):
                    row = li - first
                    if row < -1.0 or row > MAX_LINES:
                        continue
                    age = self.cur - li
                    la = (1.0 - 0.55 * clamp01(age)) * clamp01(1.0 + age) \
                        * clamp01(row + 1.0) * clamp01(MAX_LINES - row)
                    if la < 0.01:
                        continue
                    y0 = text_top + row * LINE_H - SPR_PAD
                    for wi, wx, ww in line:
                        wd = self.words[wi]
                        age_w = max(0.0, now - wd["born"])
                        f = flick(age_w)
                        pm = self._word_sprite(wd["t"], ww)
                        buzz = 0.93 + 0.07 * math.sin(t * 47.0 + wi) * math.sin(t * 13.0) \
                            if wi == last_idx else 1.0
                        x0 = text_left + wx - SPR_PAD
                        p.setOpacity(clamp01(la * max(0.2, f) * tvis * buzz))
                        p.drawPixmap(QPointF(x0, y0), pm)
                        ig = math.exp(-age_w / IGNITE)
                        if ig > 0.05:                           # over-bright flash
                            p.setCompositionMode(QPainter.CompositionMode_Plus)
                            p.setOpacity(clamp01(ig * 0.85 * f * la * tvis))
                            p.drawPixmap(QPointF(x0, y0), pm)
                            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                p.setOpacity(1.0)

                # caret after the newest word that has lit up
                vis = None
                for li2 in range(len(self.lines) - 1, -1, -1):
                    for item in reversed(self.lines[li2]):
                        if self.words[item[0]]["born"] <= now:
                            vis = (li2, item)
                            break
                    if vis:
                        break
                if vis:
                    li, (wi, wx, ww) = vis
                    row = li - first
                    blink = 0.30 + 0.70 * (0.5 + 0.5 * math.sin(t * 5.6))
                    cxp = text_left + wx + ww + 5.0
                    cyp = text_top + row * LINE_H + LINE_H / 2
                    p.setCompositionMode(QPainter.CompositionMode_Plus)
                    self._glow_at(p, cxp + 1, cyp, 11.0, 0.7 * blink * tvis)
                    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                    hr, hg, hb = self.c_hot
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(hr, hg, hb, int(255 * blink * tvis)))
                    p.drawRoundedRect(QRectF(cxp, cyp - 8.5, 1.8, 17.0), 0.9, 0.9)
            p.restore()

        # --- neon voice line ---------------------------------------------------
        if big > 0.05:
            base = rect.bottom() - PAD_B - 1.0
            self._voice_line(p, rect.left() + 24, rect.right() - 24, base,
                             RAIL_H + 4.0, now, t, big * (1 - pr) * tvis_line(self))

        # glass sheen
        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 18))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # --- tube border: dim standby -> flickers on -> lives with your voice ---
        if self.recording:
            age = now - self.t_listen
            b = lerp(0.30, 0.80 + 0.20 * self.level, flick(age))
        elif self.state == "processing":
            b = 0.42
        else:
            b = 0.30 + 0.06 * math.sin(t * 1.6)
        self._tube(p, rect, rr, b)

        # processing: a bright arc runs around the tube
        if pr > 0.02:
            ang = (-t * 210.0) % 360.0
            ctr = rect.center()
            for width_, alpha in ((8.0, 0.10), (4.0, 0.24), (1.8, 1.0)):
                cgr = QConicalGradient(QPointF(ctr.x(), ctr.y()), ang)
                cgr.setColorAt(0.0, QColor(tr, tg, tb, int(235 * alpha * pr)))
                cgr.setColorAt(0.09, QColor(tr, tg, tb, int(110 * alpha * pr)))
                cgr.setColorAt(0.24, QColor(tr, tg, tb, 0))
                cgr.setColorAt(1.0, QColor(tr, tg, tb, 0))
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
        for name in THEMES:
            m.addAction(f"Neon: {name}", lambda n=name: self.set_theme(n))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


def tvis_line(pill):
    """The voice line lights with the tube (same flicker-on as the border)."""
    if pill.recording:
        return flick(time.perf_counter() - pill.t_listen)
    return 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="fake audio AND a fake streaming transcript")
    ap.add_argument("--theme", choices=sorted(THEMES), default="sunset")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, theme=args.theme)
    pill.place_default()
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
