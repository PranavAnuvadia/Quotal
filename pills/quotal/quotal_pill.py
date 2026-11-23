"""
Quotal recording pill (PySide6).

    pip install PySide6 sounddevice numpy
    python quotal_pill.py            # real microphone
    python quotal_pill.py --demo     # fake speech, no mic needed

Design
    Surface   : the icon's near-black gradient with a glass hairline border
    Brand mark: the two quote marks traced from the Quotal icon
    Recording : marks fade out and a soft "hill" (the icon's mound) fills the
                pill, swelling with your voice, with a lagging echo behind it
    Processing: marks pulse in turn while a light orbits the pill's edge

States
    idle        compact pill, marks breathing
    recording   mic open, hill follows loudness
    processing  "enhancing" - spinner-free loader

Themes
    Pill(audio, theme="dark")  or  "light"  (python quotal_pill.py --theme light)
    pill.set_theme("light")    switches live. Light adds an orange accent: a
                               warm glow behind the marks, orange pooling under
                               the hill, and an orange orbiting loader.

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
                           QLinearGradient, QRadialGradient, QConicalGradient, QPolygonF,
                           QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (64.0, 30.0)       # logical pill sizes (before SCALE)
REC_SIZE = (140.0, 38.0)
PROC_SIZE = (80.0, 32.0)
MARGIN = 22                    # transparent room for the shadow

GLYPH_H = 12.0                 # height of the quote marks
HILL_PAD = 20.0                # recording: space left/right of the hill

HILL_POINTS = 45
HILL_SPAN = 0.30               # seconds of loudness history across the hill
ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)

SAMPLE_RATE = 16000
BLOCK = 512                    # 32 ms

# ----------------------------------------------------------------- themes --
# colours are RGB or RGBA tuples; "glow" is the orange accent (None = off)
THEMES = {
    "dark": dict(
        body=((0.0, (44, 46, 51, 255)), (0.5, (19, 20, 23, 255)),
              (1.0, (8, 9, 11, 255))),
        edge=((0.0, (255, 255, 255, 92)), (0.5, (255, 255, 255, 30)),
              (1.0, (255, 255, 255, 16))),
        inner=((0.0, (255, 255, 255, 30)), (0.35, (255, 255, 255, 0))),
        shadow=(0, 0, 0), shadow_a=22,
        glyph=(247, 247, 249),
        hill=(255, 255, 255), hill_fill=(255, 255, 255), hill_fill_a=92,
        echo=(255, 255, 255), echo_fill_a=34, echo_a=0.38,
        ground=(255, 255, 255, 38),
        arc=(255, 255, 255), arc_passes=((4.0, 0.16), (1.6, 1.0)),
        glow=None,
    ),
    "light": dict(
        body=((0.0, (255, 255, 255, 255)), (0.5, (247, 247, 249, 255)),
              (1.0, (229, 230, 236, 255))),
        edge=((0.0, (24, 26, 34, 74)), (0.5, (24, 26, 34, 50)),
              (1.0, (24, 26, 34, 78))),
        inner=((0.0, (255, 255, 255, 235)), (0.4, (255, 255, 255, 0))),
        shadow=(28, 32, 52), shadow_a=16,
        glyph=(27, 28, 33),
        hill=(30, 31, 36), hill_fill=(255, 118, 36), hill_fill_a=125,
        echo=(255, 118, 36), echo_fill_a=44, echo_a=0.80,
        ground=(30, 31, 36, 34),
        arc=(255, 122, 40), arc_passes=((9.0, 0.10), (4.0, 0.24), (1.7, 1.0)),
        glow=(255, 122, 40),
    ),
}


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


def _poly(pts):
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    path.closeSubpath()
    return path


_all_y = [y for _, y in LEFT_MARK + RIGHT_MARK]
GLYPH_UNITS_H = max(_all_y) - min(_all_y)
LEFT_PATH, RIGHT_PATH = _poly(LEFT_MARK), _poly(RIGHT_MARK)
GLYPH_W = (max(x for x, _ in RIGHT_MARK) - min(x for x, _ in LEFT_MARK)) \
    * GLYPH_H / GLYPH_UNITS_H


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


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe way to change state

    def __init__(self, audio, auto_finish=2.5, theme="dark"):
        super().__init__()
        self.audio = audio
        self.set_theme(theme)
        self.auto_finish = auto_finish   # standalone demo: fake "enhance" time
        self.state = "idle"

        self.s = 0.0 ; self.sv = 0.0     # recording spring (0..1)
        self.pr = 0.0 ; self.prv = 0.0   # processing spring (0..1)

        self.level = 0.0                 # smoothed loudness
        self.hist = deque()              # (time, level) trail

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

    # ---- theme ----
    def set_theme(self, theme):
        """'dark' or 'light'. Safe to call while the pill is on screen."""
        self.theme = theme if theme in THEMES else "dark"
        self.th = THEMES[self.theme]

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
        self.update()

    # ---- painting helpers ----
    def _draw_hill(self, p, xl, xr, base, maxh, now, t, sa):
        n = HILL_POINTS
        width = xr - xl
        if width < 12 or sa < 0.02:
            return
        th = self.th

        def build(delay, power, amp, ripple, mix):
            pts = []
            for j in range(n):
                u = (j - (n - 1) / 2) / ((n - 1) / 2)
                d = abs(u)
                prof = math.cos(d * math.pi / 2) ** power
                lv = mix * self.level + (1.0 - mix) * \
                    self._sample(now - d * HILL_SPAN - delay)
                wob = 1.0 + ripple * lv * math.sin(j * 0.75 - t * 5.5)
                hh = maxh * amp * (0.045 + 0.955 * lv) * prof * wob
                pts.append(QPointF(xl + width * j / (n - 1), base - hh))
            return pts

        back = build(0.13, 0.8, 0.74, 0.0, 0.0)
        front = build(0.0, 1.15, 1.0, 0.12, 0.6)

        def fade(color, shoulder):         # horizontal gradient, 0 at both ends
            g = QLinearGradient(xl, 0, xr, 0)
            for pos, a in ((0.0, 0.0), (0.18, shoulder), (0.5, 1.0),
                           (0.82, shoulder), (1.0, 0.0)):
                g.setColorAt(pos, QColor(color[0], color[1], color[2],
                                         int(255 * a * sa)))
            return g

        # faint ground line
        gr_, gg_, gb_, ga_ = th["ground"]
        ground = QLinearGradient(xl, 0, xr, 0)
        ground.setColorAt(0.0, QColor(gr_, gg_, gb_, 0))
        ground.setColorAt(0.5, QColor(gr_, gg_, gb_, int(ga_ * sa)))
        ground.setColorAt(1.0, QColor(gr_, gg_, gb_, 0))
        p.setPen(QPen(QBrush(ground), 1.0))
        p.drawLine(QPointF(xl, base + 0.5), QPointF(xr, base + 0.5))

        layers = (
            (back, th["echo"], th["echo_fill_a"], th["echo"], th["echo_a"], 1.0, 0.6),
            (front, th["hill_fill"], th["hill_fill_a"], th["hill"], 1.0, 1.5, 0.75),
        )
        for pts, fcol, fill_a, scol, stroke_a, wpen, shoulder in layers:
            top = smooth_path(pts)
            area = QPainterPath(top)
            area.lineTo(QPointF(xr, base))
            area.lineTo(QPointF(xl, base))
            area.closeSubpath()
            fill = QLinearGradient(0, base - maxh, 0, base)
            fill.setColorAt(0.0, QColor(fcol[0], fcol[1], fcol[2], int(fill_a * sa)))
            fill.setColorAt(1.0, QColor(fcol[0], fcol[1], fcol[2], 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(fill))
            p.drawPath(area)
            pen = QPen(QBrush(fade(scol, shoulder)), wpen)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setOpacity(stroke_a)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(top)
            p.setOpacity(1.0)

    # ---- painting ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(SCALE, SCALE)
        th = self.th
        w, h = self.width() / SCALE, self.height() / SCALE
        now = time.perf_counter()
        t = now - self.t0

        sa, pr = clamp01(self.s), clamp01(self.pr)
        pw = lerp(lerp(IDLE_SIZE[0], REC_SIZE[0], self.s), PROC_SIZE[0], self.pr)
        ph = lerp(lerp(IDLE_SIZE[1], REC_SIZE[1], self.s), PROC_SIZE[1], self.pr)
        cx, cy = w / 2, h / 2
        rect = QRectF(cx - pw / 2, cy - ph / 2, pw, ph)
        rr = ph / 2
        breathe = 0.5 + 0.5 * math.sin(t * 2.4)

        # soft layered shadow
        sr, sg, sb = th["shadow"]
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(sr, sg, sb, int(max(0, th["shadow_a"] - i * 2.4))))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        for pos, c in th["body"]:
            grad.setColorAt(pos, QColor(*c))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        # edge hairline + inner highlight
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        for pos, c in th["edge"]:
            edge.setColorAt(pos, QColor(*c))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)
        inner = QLinearGradient(0, rect.top(), 0, rect.bottom())
        for pos, c in th["inner"]:
            inner.setColorAt(pos, QColor(*c))
        p.setPen(QPen(QBrush(inner), 1.0))
        p.drawRoundedRect(rect.adjusted(1.6, 1.6, -1.6, -1.6), rr - 1.5, rr - 1.5)

        # accent glow + hill (clipped to the pill)
        p.save()
        p.setClipPath(body)
        xl = rect.left() + HILL_PAD
        xr = rect.right() - HILL_PAD
        base = cy + ph * 0.24
        maxh = ph * 0.56

        if th["glow"]:
            gr, gg, gb = th["glow"]
            # idle / processing: warm halo behind the marks
            ha = (30 * (1 - sa) * (1 - pr) * (0.75 + 0.25 * breathe)
                  + 66 * pr * (0.55 + 0.45 * breathe))
            if ha > 1:
                halo = QRadialGradient(QPointF(cx, cy), ph * 1.15)
                halo.setColorAt(0.0, QColor(gr, gg, gb, int(ha)))
                halo.setColorAt(1.0, QColor(gr, gg, gb, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(halo))
                p.drawEllipse(QPointF(cx, cy), ph * 1.15, ph * 1.15)
            # recording: orange light pooling under the hill
            rg = sa * (1 - pr) * (26 + 70 * self.level)
            if rg > 1:
                R = pw * 0.46
                p.save()
                p.translate(cx, base + ph * 0.05)
                p.scale(1.0, 0.42)
                pool = QRadialGradient(QPointF(0, 0), R)
                pool.setColorAt(0.0, QColor(gr, gg, gb, int(rg)))
                pool.setColorAt(1.0, QColor(gr, gg, gb, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(pool))
                p.drawEllipse(QPointF(0, 0), R, R)
                p.restore()

        self._draw_hill(p, xl, xr, base, maxh, now, t, sa * (1.0 - pr))
        p.restore()

        # quote marks (hidden while recording)
        gcx = cx
        idle_l = 0.80 + 0.12 * math.sin(t * 1.6)
        idle_r = 0.80 + 0.12 * math.sin(t * 1.6 + 0.9)
        ph_ = t * 5.2
        proc_l = 0.38 + 0.62 * (0.5 + 0.5 * math.sin(ph_))
        proc_r = 0.38 + 0.62 * (0.5 + 0.5 * math.sin(ph_ + math.pi))
        w_idle = (1 - sa) * (1 - pr)
        a_l = idle_l * w_idle + proc_l * pr
        a_r = idle_r * w_idle + proc_r * pr
        bob_l = -1.0 * pr * max(0.0, math.sin(ph_))
        bob_r = -1.0 * pr * max(0.0, math.sin(ph_ + math.pi))

        k = GLYPH_H / GLYPH_UNITS_H
        gcol = th["glyph"]
        p.setPen(Qt.NoPen)
        for path, a, bob in ((LEFT_PATH, a_l, bob_l), (RIGHT_PATH, a_r, bob_r)):
            if a < 0.01:
                continue
            p.save()
            p.translate(gcx, cy + bob)
            p.scale(k, k)
            p.setBrush(QColor(gcol[0], gcol[1], gcol[2], int(255 * clamp01(a))))
            p.drawPath(path)
            p.restore()

        # processing: a light orbiting the edge
        if pr > 0.02:
            ar, ag, ab = th["arc"]
            ang = (-t * 210.0) % 360.0
            for width_, alpha in th["arc_passes"]:
                cg = QConicalGradient(QPointF(cx, cy), ang)
                cg.setColorAt(0.0, QColor(ar, ag, ab, int(235 * alpha * pr)))
                cg.setColorAt(0.09, QColor(ar, ag, ab, int(110 * alpha * pr)))
                cg.setColorAt(0.24, QColor(ar, ag, ab, 0))
                cg.setColorAt(1.0, QColor(ar, ag, ab, 0))
                pen = QPen(QBrush(cg), width_)
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
        m.addAction("Light theme", lambda: self.set_theme("light"))
        m.addAction("Dark theme", lambda: self.set_theme("dark"))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake audio, no mic")
    ap.add_argument("--start", action="store_true", help="start recording")
    ap.add_argument("--theme", choices=sorted(THEMES), default="dark")
    ap.add_argument("--process-seconds", type=float, default=2.5,
                    help="how long the fake 'enhancing' state lasts (0 = stay)")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, auto_finish=args.process_seconds, theme=args.theme)
    pill.place_default()
    pill.show()
    if args.start:
        pill.set_state("recording")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
