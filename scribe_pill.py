"""
Scribe recording pill (PySide6) - a pill with a LIVE TRANSCRIPT preview.

    pip install PySide6 sounddevice numpy
    python scribe_pill.py --demo                 # fake speech + fake transcript
    python scribe_pill.py --demo --autoplay      # loops hands-free (great for recordings)
    python scribe_pill.py --accent violet        # orange | ice | violet | mint | rose

Design
    A dark-glass caption capsule docked at the bottom of the screen. It grows
    upward as the text grows, like a dynamic island.

    Idle        : compact pill with a breathing orb
    Recording   : the orb pulses with your voice; "Listening" shimmers until
                  words arrive. Each new word eases in; the newest word is
                  highlighted and a caret blinks after it. Two lines show at
                  once - when a third starts, the older line scrolls up and dims
    Processing  : the orb becomes a spinner and a light sweeps across the text
                  while your AI polishes it. When the final text arrives the
                  changed words fade in with a soft flash

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.text_request.emit("hey team quick update")     # replaces the text
    pill.state_request.emit("recording")
    pill.state_request.emit("processing")                # while enhancing
    pill.text_request.emit("Hey team, quick update.")    # enhanced result
    pill.state_request.emit("idle")                      # text fades, pill collapses
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
                           QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (62.0, 30.0)       # compact pill
LISTEN_W = 168.0               # width while recording with no text yet
PAD_L, PAD_R, PAD_V = 14.0, 20.0, 10.0
ORB_R, GAP = 7.5, 11.0         # orb radius, gap between orb and text
LINE_H = 19.0                  # text line height
MAX_LINES = 2                  # lines visible at once
TEXT_W_MAX = 240.0             # wrap width
MIN_TEXT_W = 74.0
FONT_PX = 13                   # text size
MARGIN = 26                    # transparent room for shadow / glow
MARGIN_BOTTOM = 18             # distance from window bottom to the pill

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
WORD_FADE = 0.26               # seconds for a new word to ease in

ACCENTS = {
    "orange": dict(core=(255, 238, 214), glow=(255, 132, 40)),
    "ice":    dict(core=(228, 246, 255), glow=(70, 190, 255)),
    "violet": dict(core=(246, 232, 255), glow=(168, 110, 255)),
    "mint":   dict(core=(226, 255, 242), glow=(60, 235, 165)),
    "rose":   dict(core=(255, 232, 240), glow=(255, 84, 140)),
}
FONT_CHOICES = ["Inter", "SF Pro Text", "Segoe UI Variable Text", "Segoe UI",
                "Helvetica Neue", "Roboto", "Noto Sans", "Arial"]

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


def ease_out(x):
    x = clamp01(x)
    return 1.0 - (1.0 - x) ** 3


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
        self.s = 0.0 ; self.sv = 0.0                 # recording spring
        self.pr = 0.0 ; self.prv = 0.0               # processing spring
        self.cur = 0.0 ; self.curv = 0.0             # animated "latest line" index

        self.level = 0.0
        self.hist = deque()
        self.tfade = 0.0                             # text visibility
        self.flash = 0.0                             # soft flash on final text

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
        max_w = PAD_L + ORB_R * 2 + GAP + TEXT_W_MAX + 8 + PAD_R
        max_h = PAD_V * 2 + LINE_H * MAX_LINES
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
            if prev == "idle":               # fresh dictation: start from blank
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
        if self.state == "processing":
            self.flash = 1.0
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

    def _target_size(self):
        if self.state == "idle" and self.tfade < 0.35:
            return IDLE_SIZE
        w = PAD_L + ORB_R * 2 + GAP + TEXT_W_MAX + 8 + PAD_R
        n = len(self.lines)
        h = PAD_V * 2 + max(1, min(n, MAX_LINES)) * LINE_H
        return (w, h)

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

        # text visibility + cleanup after the pill collapses
        if self.state == "idle":
            self.tfade = max(0.0, self.tfade - dt * 5.0)
            if self.tfade <= 0.0 and self.words:
                self.words, self.lines, self.cur = [], [], 0.0
        else:
            self.tfade = min(1.0, self.tfade + dt * 8.0)
        self.flash = max(0.0, self.flash - dt * 2.6)

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

    def _orb(self, p, x, y, t, expand, sa, pr):
        gr, gg, gb = self.c_glow
        cr, cg, cb = self.c_core
        lv = self.level * sa
        breathe = 0.5 + 0.5 * math.sin(t * 2.0)
        r = lerp(5.2, ORB_R, expand) * (1.0 + 0.24 * lv) + 0.35 * breathe * (1 - sa)

        p.setCompositionMode(QPainter.CompositionMode_Plus)
        self._glow_at(p, x, y, 13.0 + 12.0 * lv + 3.0 * breathe, 0.40 + 0.55 * max(lv, pr))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        disc = QRadialGradient(QPointF(x - r * 0.28, y - r * 0.34), r * 1.4)
        disc.setColorAt(0.0, QColor(cr, cg, cb))
        disc.setColorAt(0.55, QColor(gr, gg, gb))
        disc.setColorAt(1.0, QColor(int(gr * 0.55), int(gg * 0.55), int(gb * 0.55)))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(disc))
        p.drawEllipse(QPointF(x, y), r, r)

        # voice halo ring while recording
        ring_a = (0.16 + 0.60 * lv) * (1 - pr) * sa
        if ring_a > 0.02:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(gr, gg, gb, int(255 * ring_a)), 1.2))
            rr = r + 3.2 + 4.0 * lv
            p.drawEllipse(QPointF(x, y), rr, rr)

        # spinner while processing
        if pr > 0.02:
            cgrad = QConicalGradient(QPointF(x, y), (-t * 320.0) % 360.0)
            cgrad.setColorAt(0.0, QColor(gr, gg, gb, int(255 * pr)))
            cgrad.setColorAt(0.30, QColor(gr, gg, gb, 0))
            cgrad.setColorAt(1.0, QColor(gr, gg, gb, 0))
            pen = QPen(QBrush(cgrad), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            p.setBrush(Qt.NoBrush)
            p.setPen(pen)
            rr = r + 3.6
            p.drawEllipse(QPointF(x, y), rr, rr)

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
        sa, pr = clamp01(self.s), clamp01(self.pr)
        pw, ph = self.pw, self.ph
        rect = QRectF(cx - pw / 2, h - MARGIN_BOTTOM - ph, pw, ph)
        rr = min(ph / 2, 22.0)
        gr, gg, gb = self.c_glow
        act = max(sa * (0.25 + 0.75 * self.level), pr * 0.5, self.flash)

        # accent light cast below the pill
        if act > 0.02:
            p.save()
            p.translate(cx, rect.bottom() + 5)
            p.scale(1.0, 0.16)
            R = pw * 0.55
            pool = QRadialGradient(QPointF(0, 0), R)
            pool.setColorAt(0.0, QColor(gr, gg, gb, int(70 * act)))
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
            p.setBrush(QColor(0, 0, 0, int(max(0, 26 - i * 2.8))))
            p.drawRoundedRect(r, min(r.height() / 2, rr + g), min(r.height() / 2, rr + g))

        # dark glass body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(34, 36, 44))
        grad.setColorAt(0.5, QColor(15, 16, 21))
        grad.setColorAt(1.0, QColor(7, 8, 11))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        p.save()
        p.setClipPath(body)

        # --- orb ---------------------------------------------------------
        expand = clamp01((pw - IDLE_SIZE[0]) / (LISTEN_W - IDLE_SIZE[0]))
        ox_left = rect.left() + PAD_L + ORB_R
        ox = lerp(cx, ox_left, expand)
        oy = rect.bottom() - min(ph / 2, PAD_V + LINE_H / 2)
        self._orb(p, ox, oy, t, expand, sa, pr)

        # --- text --------------------------------------------------------
        text_left = rect.left() + PAD_L + ORB_R * 2 + GAP
        text_w = max(10.0, rect.right() - PAD_R - text_left + 6)
        text_rect = QRectF(text_left - 2, rect.top() + 2, text_w + 4, ph - 4)
        tvis = self.tfade * clamp01((expand - 0.35) / 0.5)
        p.setFont(self.font)
        fm = self.fm
        asc = fm.ascent()
        off = (LINE_H - fm.height()) / 2 + asc

        if tvis > 0.01:
            p.save()
            p.setClipRect(text_rect)

            if not self.lines:
                # placeholder with a travelling shine
                label = "Enhancing" if self.state == "processing" else "Listening"
                y = rect.top() + PAD_V + off
                p.setPen(QColor(170, 176, 190, int(150 * tvis)))
                p.drawText(QPointF(text_left, y), label)
                p.setPen(self._shimmer_pen(text_left, text_left + 90, t, int(255 * tvis)))
                p.drawText(QPointF(text_left, y), label)
            else:
                n = len(self.lines)
                first = max(0.0, self.cur - (MAX_LINES - 1))
                last_idx = len(self.words) - 1
                top = rect.top() + PAD_V
                for li, line in enumerate(self.lines):
                    row = li - first
                    if row < -1.0 or row > MAX_LINES:
                        continue
                    age = self.cur - li
                    la = (1.0 - 0.52 * clamp01(age)) * clamp01(1.0 + age) \
                        * clamp01(row + 1.0) * clamp01(MAX_LINES - row + 0.0)
                    if la < 0.01:
                        continue
                    y = top + row * LINE_H + off
                    for wi, wx, ww in line:
                        wd = self.words[wi]
                        age_w = max(0.0, now - wd["born"])
                        e = ease_out(age_w / WORD_FADE)
                        newest = (wi == last_idx) and self.state == "recording"
                        a = la * max(0.25, e) * tvis
                        yy = y + (1.0 - e) * 1.5
                        x = text_left + wx
                        if newest:
                            hl = 0.30 + 0.70 * math.exp(-age_w / 0.40)
                            hr = QRectF(x - 3.5, yy - asc - 1.0, ww + 7.0, fm.height() + 2.0)
                            p.setPen(Qt.NoPen)
                            p.setBrush(QColor(gr, gg, gb, int(58 * hl * a)))
                            p.drawRoundedRect(hr, 5.0, 5.0)
                            p.setPen(QColor(255, 238, 220, int(255 * a)))
                        else:
                            shade = 244 if age < 0.5 else 226
                            p.setPen(QColor(shade, shade + 2, 250, int(255 * a * 0.96)))
                        p.drawText(QPointF(x, yy), wd["t"])

                # caret after the newest word
                if self.state == "recording" and self.lines:
                    li = len(self.lines) - 1
                    wi, wx, ww = self.lines[li][-1]
                    row = li - first
                    blink = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 5.6))
                    cxp = text_left + wx + ww + 3.5
                    cyp = top + row * LINE_H + LINE_H / 2
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(gr, gg, gb, int(255 * blink * tvis)))
                    p.drawRoundedRect(QRectF(cxp, cyp - 7.5, 1.7, 15.0), 0.85, 0.85)

                # processing: a light sweeps across the text
                if pr > 0.02:
                    p.setPen(self._shimmer_pen(text_left, text_left + text_w, t,
                                               int(255 * pr * tvis)))
                    for li, line in enumerate(self.lines):
                        row = li - first
                        if row < -0.5 or row > MAX_LINES - 0.5:
                            continue
                        y = top + row * LINE_H + off
                        for wi, wx, ww in line:
                            p.drawText(QPointF(text_left + wx, y), self.words[wi]["t"])
            p.restore()

        # glass sheen
        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # hairline edge, tinted by activity / the final-text flash
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 88))
        edge.setColorAt(0.5, QColor(255, 255, 255, 28))
        edge.setColorAt(1.0, QColor(255, 255, 255, 16))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)
        if act > 0.02:
            p.setPen(QPen(QColor(gr, gg, gb, int(120 * min(1.0, act))), 1.0))
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
ENHANCED = ("Hey team, quick update on the Quotal launch. The new recording pill "
            "ships tomorrow, and I want everyone to try dictation mode before we "
            "go live.")
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
        words = RAW.split()
        frames, shown = [], []
        for w in words:
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
            self.timer.start(700)                # short "Listening" moment first
        elif st == "processing":
            QTimer.singleShot(2000, self._enhance)
        elif st == "idle" and self.autoplay:
            QTimer.singleShot(1800, lambda: self.pill.state_request.emit("recording"))

    def _step(self):
        if self.pill.state != "recording":
            return
        if self.idx < len(self.frames):
            delay, text = self.frames[self.idx]
            self.pill.text_request.emit(text)
            self.idx += 1
            self.timer.start(int(delay * 1000))
        elif self.autoplay:                      # sentence finished: stop dictating
            QTimer.singleShot(900, lambda: self.pill.state_request.emit("processing"))

    def _enhance(self):
        if self.pill.state == "processing":
            self.pill.text_request.emit(ENHANCED)
            QTimer.singleShot(2200, lambda: self.pill.state_request.emit("idle")
                              if self.pill.state == "processing" else None)


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
