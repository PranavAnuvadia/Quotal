"""
Face recording pill (PySide6) - your Quotal icon, alive.

The icon already looks like a face: two quote marks for eyes and a dark mound
for a mouth. This pill animates it, and your live transcript appears in a
speech bubble beside it.

    pip install PySide6 sounddevice numpy
    python face_pill.py --demo                  # fake speech + fake transcript
    python face_pill.py --demo --autoplay       # loops hands-free

How it behaves
    Idle        just the icon tile, asleep: eyes are slits, the mound breathes,
                a little z floats up
    Listening   it wakes up (and blinks now and then). The mound is its mouth: it
                rises and falls with your voice. A speech bubble opens; your
                words type out letter by letter, the word being typed is
                underlined in orange, and its eyes follow the caret
    Processing  the bubble shrinks to three bouncing dots, the eyes look up and
                around while it thinks

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # while enhancing
    pill.state_request.emit("idle")

Same public API as the other pills (Pill, AudioSource, state_request), plus
text_request and state_changed.

Standalone controls
    click       idle -> recording -> processing -> idle
    drag        move the pill        right click   menu
"""
import re
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
                           QRadialGradient, QPixmap, QPolygonF, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

TILE = 40.0                    # the icon tile
GAP = 13.0                     # space between tile and bubble (the tail lives here)
BUBBLE_W = 252.0
BUBBLE_PADX, BUBBLE_PADY = 17.0, 8.0
LINE_H = 21.0
MAX_LINES = 2
DOTS_SIZE = (66.0, 34.0)
FONT_PX = 14
CPS = 70.0                     # typing speed, characters per second
EYE_H = 8.6                    # height of an eye (a quote mark)
MARGIN = 28
MARGIN_BOTTOM = 22
TOP_ROOM = 30

ACCENT = (255, 122, 36)
INK = (28, 28, 33)
ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
FONT_UI = ["Inter", "SF Pro Text", "Segoe UI Variable Text", "Segoe UI",
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
    state_request = Signal(str)          # thread-safe: change state
    text_request = Signal(str)           # thread-safe: set the live text
    state_changed = Signal(str)          # emitted after every state change

    def __init__(self, audio, auto_finish=0):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"
        self.rnd = random.Random(5)

        self.awake, self.awakev = 0.0, 0.0       # eyes open amount (spring)
        self.bw, self.bwv = 0.0, 0.0             # bubble size (springs)
        self.bh, self.bhv = 0.0, 0.0
        self.cur, self.curv = 0.0, 0.0           # animated "current line"
        self.level = 0.0
        self.gaze = [0.0, 0.0]
        self.next_blink, self.blink_t = 2.0, -1.0
        self.tfade = 0.0                         # text visibility inside the bubble

        self.text, self.shown = "", 0.0          # typewriter state
        self.lines = []                          # [(start, end)] char ranges
        self.dots_pos = (0.0, 0.0)

        fams = set(QFontDatabase.families())
        ui = next((f for f in FONT_UI if f in fams), None)
        self.font = QFont(ui) if ui else QFont()
        self.font.setPixelSize(FONT_PX)
        self.font.setWeight(QFont.Medium)
        self.fm = QFontMetricsF(self.font)
        self.zfont = QFont(self.font)
        self.zfont.setItalic(True)
        self.zfont.setWeight(QFont.Bold)

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        max_w = TILE + GAP + BUBBLE_W
        max_h = BUBBLE_PADY * 2 + LINE_H * MAX_LINES
        self.resize(int((max_w + MARGIN * 2) * SCALE),
                    int((max_h + TOP_ROOM + MARGIN_BOTTOM) * SCALE))

        self._make_glow()
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    def _make_glow(self):
        size = 44
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        r, g, b = ACCENT
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, QColor(r, g, b, 190))
        grad.setColorAt(0.35, QColor(r, g, b, 55))
        grad.setColorAt(1.0, QColor(r, g, b, 0))
        q.setPen(Qt.NoPen)
        q.setBrush(QBrush(grad))
        q.drawEllipse(QPointF(size / 2, size / 2), size / 2, size / 2)
        q.end()
        self.glow, self.glow_size = pm, size

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
            if prev != "recording":
                self.text, self.shown, self.lines, self.cur = "", 0.0, [], 0.0
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
        """Replace the live text. Letters already typed stay typed."""
        if not self.recording:
            return
        k = 0
        while k < len(text) and k < len(self.text) and text[k] == self.text[k]:
            k += 1
        self.shown = min(self.shown, float(k))
        self.text = text
        lines, start, last_end = [], None, 0
        width_max = BUBBLE_W - 2 * BUBBLE_PADX
        for m in re.finditer(r"\S+", text):
            if start is None:
                start, last_end = m.start(), m.end()
            elif self.fm.horizontalAdvance(text[start:m.end()]) <= width_max:
                last_end = m.end()
            else:
                lines.append((start, last_end))
                start, last_end = m.start(), m.end()
        if start is not None:
            lines.append((start, last_end))
        self.lines = lines

    def _typed_lines(self):
        n = int(self.shown)
        return [(s, e) for (s, e) in self.lines if s < n]

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt, k=300.0, c=27.0):
        v += (k * (target - x) - c * v) * dt
        return x + v * dt, v

    def tick(self):
        now = time.perf_counter()
        t = now - self.t0
        dt = min(now - self.last, 1 / 30)
        self.last = now

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))

        self.awake, self.awakev = self._spring(
            self.awake, self.awakev, 0.0 if self.state == "idle" else 1.0, dt, 200.0, 22.0)

        # typewriter
        if self.shown < len(self.text):
            self.shown = min(float(len(self.text)),
                             self.shown + (CPS + 3.0 * (len(self.text) - self.shown)) * dt)
        typed = self._typed_lines()

        # what is the bubble showing?
        text_mode = self.recording and bool(typed)
        dots_mode = (self.recording and not typed) or self.state == "processing"
        if text_mode:
            tw = BUBBLE_W
            th = BUBBLE_PADY * 2 + LINE_H * min(len(typed), MAX_LINES)
        elif dots_mode:
            tw, th = DOTS_SIZE
        else:
            tw, th = 0.0, 0.0
        self.bw, self.bwv = self._spring(self.bw, self.bwv, tw, dt)
        self.bh, self.bhv = self._spring(self.bh, self.bhv, th, dt)
        self.bw, self.bh = max(0.0, self.bw), max(0.0, self.bh)
        self.tfade += ((1.0 if text_mode else 0.0) - self.tfade) * (1.0 - math.exp(-12.0 * dt))
        self.cur, self.curv = self._spring(self.cur, self.curv,
                                           float(max(0, len(typed) - 1)), dt, 230.0, 25.0)

        # blinking
        if self.blink_t >= 0.0:
            self.blink_t += dt
            if self.blink_t > 0.15:
                self.blink_t = -1.0
        elif self.awake > 0.8:
            self.next_blink -= dt
            if self.next_blink <= 0.0:
                self.blink_t = 0.0
                self.next_blink = self.rnd.uniform(2.4, 5.4)

        # where should it look?
        if self.state == "processing":
            gx, gy = 0.7 * math.sin(t * 1.6), -0.85
        elif text_mode and getattr(self, "caret_vec", None):
            gx, gy = self.caret_vec
        elif self.recording:
            gx, gy = 0.8, 0.0
        else:
            gx, gy = 0.0, 0.0
        f = 1.0 - math.exp(-7.0 * dt)
        self.gaze[0] += (gx - self.gaze[0]) * f
        self.gaze[1] += (gy - self.gaze[1]) * f
        self.update()

    # ---- painting helpers ----
    def _glow_at(self, p, x, y, radius, opacity):
        if opacity <= 0.01:
            return
        p.setOpacity(min(1.0, opacity))
        p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                     self.glow, QRectF(0, 0, self.glow_size * 2, self.glow_size * 2))
        p.setOpacity(1.0)

    def _bubble(self, p, x, ybot, t, now):
        bw, bh = self.bw, self.bh
        if bw < 8.0 or bh < 8.0:
            return
        rect = QRectF(x, ybot - bh, bw, bh)
        rr = min(bh / 2, 20.0)
        alpha = clamp01(bw / 40.0)
        p.setOpacity(alpha)

        # tail pointing at the face, then the bubble body
        ty = ybot - 12.0
        tail = QPainterPath()
        tail.moveTo(x + 3.0, ty - 6.5)
        tail.quadTo(x - 3.5, ty - 1.5, x - 9.5, ty + 3.5)
        tail.quadTo(x - 1.0, ty + 6.0, x + 4.0, ty + 7.0)
        tail.closeSubpath()
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        body = body.united(tail)

        p.setPen(Qt.NoPen)
        for i in range(6):                                   # soft shadow
            g = i * 1.6
            sh = QPainterPath(body)
            sh.translate(0, 3.0 + i * 0.4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 20 - i * 3.2))))
            p.drawPath(sh.translated(0, 0))
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(255, 255, 255))
        grad.setColorAt(1.0, QColor(243, 241, 236))
        p.setBrush(QBrush(grad))
        p.drawPath(body)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 22), 1.0))
        p.drawPath(body)

        cx, cy = rect.center().x(), rect.center().y()
        if self.tfade < 0.98:                                # typing-indicator dots
            da = (1.0 - self.tfade)
            think = self.state == "processing"
            col = ACCENT if think else (150, 147, 140)
            p.setPen(Qt.NoPen)
            for i in range(3):
                ph = t * (7.5 if think else 3.0) - i * 0.85
                bob = -4.0 * max(0.0, math.sin(ph)) if think else -1.6 * math.sin(ph)
                p.setBrush(QColor(col[0], col[1], col[2], int(255 * da)))
                p.drawEllipse(QPointF(cx + (i - 1) * 11.0, cy + bob), 3.1, 3.1)
            self.dots_pos = (cx, cy)

        self.caret_vec = None
        if self.tfade > 0.02:
            typed = self._typed_lines()
            n = int(self.shown)
            p.save()
            p.setClipRect(rect.adjusted(2, 2, -2, -2))
            p.setFont(self.font)
            fm = self.fm
            asc = fm.ascent()
            off = (LINE_H - fm.height()) / 2 + asc
            left = rect.left() + BUBBLE_PADX
            first = max(0.0, self.cur - (MAX_LINES - 1))
            last_xy = None
            for li, (s, e) in enumerate(typed):
                row = li - first
                la = clamp01(row + 1.0) * (1.0 - 0.5 * clamp01(self.cur - li))
                if la <= 0.01 or row > MAX_LINES:
                    continue
                vis = self.text[s:min(e, n)]
                ytop = rect.top() + BUBBLE_PADY + row * LINE_H
                p.setPen(QColor(INK[0], INK[1], INK[2], int(255 * la * self.tfade)))
                p.drawText(QPointF(left, ytop + off), vis)
                last_xy = (left + fm.horizontalAdvance(vis), ytop, vis, la)
            if self.recording and last_xy:
                ex, ytop, vis, la = last_xy
                w0 = vis.rfind(" ") + 1                      # the word being typed
                sx = left + fm.horizontalAdvance(vis[:w0])
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*ACCENT, int(230 * la * self.tfade)))
                p.drawRoundedRect(QRectF(sx, ytop + off + 2.6, max(2.0, ex - sx), 2.0), 1.0, 1.0)
                blink = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 6.0))
                p.setBrush(QColor(*ACCENT, int(255 * blink * self.tfade)))
                p.drawRoundedRect(QRectF(ex + 2.5, ytop + 2.5, 1.8, LINE_H - 5), 0.9, 0.9)
                self.caret_xy = (ex + 3.4, ytop + LINE_H / 2)
            p.restore()
        p.setOpacity(1.0)

    def _tile(self, p, tx, ty, t, now):
        """The icon tile with its living face."""
        lv = self.level * clamp01(self.awake)
        tcx, tcy = tx + TILE / 2, ty + TILE / 2
        rect = QRectF(tx, ty, TILE, TILE)
        rr = 11.5
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)

        # shadow + glow
        p.setPen(Qt.NoPen)
        for i in range(8):
            g = i * 1.7
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 30 - i * 3.6))))
            p.drawRoundedRect(r, rr + g, rr + g)
        listen = clamp01(self.awake) if self.recording else 0.0
        if listen > 0.02:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            self._glow_at(p, tcx, tcy, 40.0, (0.16 + 0.55 * lv) * listen)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        grad = QLinearGradient(0, ty, 0, ty + TILE)          # the icon's charcoal
        grad.setColorAt(0.0, QColor(42, 44, 50))
        grad.setColorAt(0.55, QColor(20, 21, 25))
        grad.setColorAt(1.0, QColor(10, 11, 13))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        p.save()
        p.setClipPath(body)

        # the mound: its mouth
        n = 29
        base = ty + TILE + 0.5
        if self.state == "idle":
            hh = 3.6 + 0.9 * math.sin(t * 1.4)
        elif self.state == "processing":
            hh = 5.0 + 1.6 * math.sin(t * 5.0)
        else:
            hh = 4.2 + 12.5 * lv
        pts = []
        for j in range(n):
            u = (j - (n - 1) / 2) / ((n - 1) / 2)
            wob = 1.0 + 0.10 * lv * math.sin(j * 0.9 - t * 9.0)
            pts.append(QPointF(tx - 2 + (TILE + 4) * j / (n - 1),
                               base - hh * math.cos(u * math.pi / 2) ** 1.5 * wob))
        mound = QPainterPath(pts[0])
        for pt in pts[1:]:
            mound.lineTo(pt)
        area = QPainterPath(mound)
        area.lineTo(QPointF(tx + TILE + 2, base))
        area.lineTo(QPointF(tx - 2, base))
        area.closeSubpath()
        mg = QLinearGradient(0, base - 14, 0, base)
        mg.setColorAt(0.0, QColor(78, 80, 90))
        mg.setColorAt(1.0, QColor(30, 31, 37))
        p.setBrush(QBrush(mg))
        p.setPen(Qt.NoPen)
        p.drawPath(area)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 105), 1.1))
        p.drawPath(mound)

        # eyes: the quote marks. squint when asleep, widen with your voice
        k = EYE_H / GLYPH_UNITS_H
        open_ = clamp01(self.awake) * 0.92 + 0.08
        blink = 1.0
        if self.blink_t >= 0.0:
            blink = 1.0 - 0.92 * math.sin(math.pi * self.blink_t / 0.15)
        sx = 1.0 + 0.10 * lv
        sy = open_ * blink * (1.0 + 0.10 * lv)
        p.save()
        p.translate(tcx + self.gaze[0] * 2.6, tcy - 5.0 + self.gaze[1] * 2.0)
        p.scale(k * sx, k * sy)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(247, 247, 250))
        p.drawPath(LEFT_PATH)
        p.drawPath(RIGHT_PATH)
        p.restore()

        # sheen
        sheen = QLinearGradient(0, ty, 0, ty + TILE * 0.5)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(tx, ty, TILE, TILE / 2))
        p.restore()

        # rim: hairline, orange while listening
        edge = QLinearGradient(0, ty, 0, ty + TILE)
        edge.setColorAt(0.0, QColor(255, 255, 255, 100))
        edge.setColorAt(1.0, QColor(255, 255, 255, 18))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)
        if listen > 0.02:
            p.setPen(QPen(QColor(*ACCENT, int(255 * (0.25 + 0.7 * lv) * listen)), 1.3))
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)

        # asleep: a little z drifts up
        if self.awake < 0.6:
            a = (1.0 - self.awake / 0.6)
            for i, size in enumerate((9, 12)):
                ph = ((t * 0.45 + i * 0.5) % 1.0)
                f = QFont(self.zfont)
                f.setPixelSize(size)
                p.setFont(f)
                p.setPen(QColor(235, 235, 240, int(200 * a * math.sin(ph * math.pi))))
                p.drawText(QPointF(tx + TILE - 4 + ph * 7 + i * 3, ty + 4 - ph * 13), "z")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        now = time.perf_counter()
        t = now - self.t0

        bub_vis = clamp01(self.bw / 40.0)
        gap = GAP * bub_vis
        total = TILE + gap + self.bw
        x0 = w / 2 - total / 2
        ybot = h - MARGIN_BOTTOM
        tx, ty = x0, ybot - TILE

        self.caret_xy = None
        self._bubble(p, x0 + TILE + gap, ybot, t, now)
        if self.caret_xy and self.state == "recording":      # eyes follow the caret
            dx = self.caret_xy[0] - (tx + TILE / 2)
            dy = self.caret_xy[1] - (ty + TILE / 2)
            self.caret_vec = (max(-1.0, min(1.0, dx / 90.0)), max(-1.0, min(1.0, dy / 30.0)))
        self._tile(p, tx, ty, t, now)

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
            QTimer.singleShot(1200, lambda: pill.state_request.emit("recording"))

    def _build(self):
        rnd = random.Random()
        frames, shown = [], []
        for w in RAW.split():
            if w in MISHEAR:                     # the "ASR" hears it wrong first
                frames.append((rnd.uniform(0.24, 0.36), " ".join(shown + [MISHEAR[w]])))
                frames.append((0.30, " ".join(shown + [w])))
            else:
                frames.append((rnd.uniform(0.20, 0.38), " ".join(shown + [w])))
            shown.append(w)
        return frames

    def _on_state(self, st):
        self.timer.stop()
        if st == "recording":
            self.frames, self.idx = self._build(), 0
            self.timer.start(1300)
        elif st == "processing":
            QTimer.singleShot(2600, lambda: self.pill.state_request.emit("idle")
                              if self.pill.state == "processing" else None)
        elif st == "idle" and self.autoplay:
            QTimer.singleShot(2400, lambda: self.pill.state_request.emit("recording"))

    def _step(self):
        if not self.pill.recording:
            return
        if self.idx < len(self.frames):
            delay, text = self.frames[self.idx]
            self.pill.text_request.emit(text)
            self.idx += 1
            self.timer.start(int(delay * 1000))
        elif self.autoplay:                      # sentence finished: stop dictating
            QTimer.singleShot(1500, lambda: self.pill.state_request.emit("processing"))


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="fake audio AND a fake streaming transcript")
    ap.add_argument("--autoplay", action="store_true",
                    help="with --demo: loop record -> process -> idle forever")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio)
    if args.demo:
        DemoTranscriber(pill, autoplay=args.autoplay)
    pill.place_default()
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
