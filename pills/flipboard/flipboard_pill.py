"""
Flipboard recording pill (PySide6) - a split-flap board (airport departures
sign) in a metal capsule. Your live transcript is spelled out by real
mechanical flaps that flip through random characters before they land.

    pip install PySide6 sounddevice numpy
    python flipboard_pill.py --demo                 # fake speech + fake transcript
    python flipboard_pill.py --demo --autoplay      # loops hands-free
    python flipboard_pill.py --style amber          # classic | amber | mint | ice

How it behaves
    Idle        compact capsule, the board reads QUOTAL (a random flap flips now
                and then, like a board that is alive)
    Listening   the board flips to LISTENING; the lamp column on the left is a
                VU meter that follows your voice. As words arrive the capsule
                widens and the words are written onto a 2-row board. Only the
                new letters flip. When a line fills up, the board "turns the
                page": every flap flips and the lines move up
    Processing  the board reads ENHANCING while a wave of flips runs along it
                and the lamps chase up and down

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # while enhancing
    pill.state_request.emit("idle")
    pill.set_style("amber")

Same public API as the other pills (Pill, AudioSource, state_request), plus
text_request and state_changed. The board shows UPPERCASE like a real one.

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
                           QFontDatabase, QLinearGradient, QRadialGradient,
                           QPixmap, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

ROWS, COLS = 2, 17             # the board
CELL_W, CELL_H = 11.2, 17.0    # one flap tile
PITCH_X, PITCH_Y = 12.6, 19.0  # distance between tiles
PAD_X, PAD_V = 13.0, 8.0       # capsule padding
LAMP_COL = 17.0                # width of the lamp column
FLIP_T = 0.085                 # seconds per flap flip
MARGIN = 26                    # transparent room for the shadow
MARGIN_BOTTOM = 20

IDLE_WORD, LISTEN_WORD, PROC_WORD = "QUOTAL", "LISTENING", "ENHANCING"
ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)

# letter colour, lamp colour, hot lamp colour
STYLES = {
    "classic": dict(ink=(248, 244, 234), lamp=(255, 168, 58), hot=(255, 232, 190)),
    "amber":   dict(ink=(255, 190, 70),  lamp=(255, 150, 40), hot=(255, 232, 180)),
    "mint":    dict(ink=(160, 255, 208), lamp=(70, 240, 170), hot=(222, 255, 238)),
    "ice":     dict(ink=(206, 230, 255), lamp=(110, 190, 255), hot=(236, 248, 255)),
}
FONT_CHOICES = ["JetBrains Mono", "SF Mono", "Menlo", "Consolas", "Roboto Mono",
                "DejaVu Sans Mono", "Liberation Mono", "Courier New"]
CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

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


# ------------------------------------------------------------- flap logic --
class Cell:
    """One split-flap tile: flips through a few random characters, then lands."""
    __slots__ = ("cur", "nxt", "seq", "t", "flipping", "tgt", "last_tgt", "delay")

    def __init__(self):
        self.cur = self.nxt = self.tgt = self.last_tgt = " "
        self.seq, self.t, self.flipping, self.delay = [], 0.0, False, 0.0

    def start(self, seq):
        self.seq = list(seq)
        self.nxt = self.seq.pop(0)
        self.t, self.flipping = 0.0, True

    def kick(self, rnd):
        """A flourish: flip through two random characters and come back."""
        if not self.flipping:
            self.start([rnd.choice(CHARS), rnd.choice(CHARS), self.cur])

    def step(self, dt, tgt, rnd):
        if tgt != self.last_tgt:
            self.last_tgt = tgt
            self.delay = rnd.uniform(0.0, 0.10)       # flaps never move in sync
        self.tgt = tgt
        if self.flipping:
            self.t += dt / FLIP_T
            if self.t >= 1.0:
                self.cur = self.nxt
                if self.seq:
                    self.nxt = self.seq.pop(0)
                    self.t -= 1.0
                else:
                    self.flipping, self.t = False, 0.0
            else:
                end = self.seq[-1] if self.seq else self.nxt
                if end != tgt:                         # text changed mid-flip
                    self.seq = [tgt]
        if not self.flipping and self.cur != tgt:
            if self.delay > 0.0:
                self.delay -= dt
                return
            n = rnd.choice((1, 2, 2, 3))
            self.start([rnd.choice(CHARS) for _ in range(n)] + [tgt])


# ----------------------------------------------------------------- widget --
def lerp(a, b, t):
    return a + (b - a) * t


def clamp01(x):
    return min(1.0, max(0.0, x))


def mix(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)),
            int(lerp(c1[2], c2[2], t)))


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe: change state
    text_request = Signal(str)           # thread-safe: set the live text
    state_changed = Signal(str)          # emitted after every state change

    def __init__(self, audio, auto_finish=0, style="classic"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"
        self.rnd = random.Random(11)

        self.pw, self.pwv = 104.0, 0.0                 # animated capsule size
        self.ph, self.phv = 33.0, 0.0
        self.lamp = 0.0                                # lamp column visibility
        self.shift = PITCH_X * 0.5                     # half-cell centring offset
        self.level = 0.0
        self.text = ""
        self.lines = []
        self.cells = [[Cell() for _ in range(COLS)] for _ in range(ROWS)]
        for c, ch in enumerate(self._idle_row()):      # board starts on QUOTAL
            self.cells[0][c].cur = self.cells[0][c].last_tgt = ch
        self.kick_t = 0.0
        self.idle_t = 2.0
        self.kick_i = 0

        fams = set(QFontDatabase.families())
        family = next((f for f in FONT_CHOICES if f in fams), None)
        self.font = QFont(family) if family else QFont()
        self.font.setStyleHint(QFont.Monospace)
        self.font.setPixelSize(14)
        self.font.setWeight(QFont.Bold)

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        max_w, max_h = self._size_for(COLS, ROWS, 1.0)
        self.resize(int((max_w + MARGIN * 2) * SCALE),
                    int((max_h + MARGIN + MARGIN_BOTTOM) * SCALE))

        self.set_style(style)
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- style ----
    def set_style(self, name):
        """'classic' | 'amber' | 'mint' | 'ice'. Safe to call any time."""
        self.style = name if name in STYLES else "classic"
        s = STYLES[self.style]
        self.c_ink, self.c_lamp, self.c_hot = s["ink"], s["lamp"], s["hot"]
        size = 30
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        r, g, b = self.c_lamp
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, QColor(r, g, b, 190))
        grad.setColorAt(0.35, QColor(r, g, b, 55))
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
            if prev != "recording":
                self.text, self.lines = "", []
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

    # ---- text -> board ----
    def set_text(self, text):
        """Replace the live text. Only characters that change will flip."""
        self.text = text
        words, lines, cur = text.upper().split(), [], ""
        for w in words:
            w = w[:COLS]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= COLS:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        self.lines = lines[-ROWS:]

    @staticmethod
    def _centered(word):
        row = [" "] * COLS
        start = (COLS - len(word)) // 2
        for i, ch in enumerate(word):
            row[start + i] = ch
        return row

    def _idle_row(self):
        return self._centered(IDLE_WORD)

    def _targets(self):
        g = [[" "] * COLS for _ in range(ROWS)]
        if self.state == "recording":
            if self.lines:
                for r, line in enumerate(self.lines):
                    for c, ch in enumerate(line[:COLS]):
                        g[r][c] = ch
            else:
                g[0] = self._centered(LISTEN_WORD)
        elif self.state == "processing":
            g[0] = self._centered(PROC_WORD)
        else:
            g[0] = self._idle_row()
        return g

    # ---- sizing ----
    @staticmethod
    def _size_for(cols, rows, lamps):
        w = cols * PITCH_X + 2 * PAD_X + lamps * LAMP_COL
        h = rows * CELL_H + (rows - 1) * (PITCH_Y - CELL_H) + 2 * PAD_V
        return w, h

    def _target_size(self):
        if self.state == "recording":
            # Keep stable width across listening so it doesn't jump
            rows = max(1, len(self.lines))
            return self._size_for(COLS, rows, 1.0)
        if self.state == "processing":
            return self._size_for(len(PROC_WORD), 1, 1.0)
        return self._size_for(len(IDLE_WORD), 1, 0.0)

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt, k=300.0, c=27.0):
        v += (k * (target - x) - c * v) * dt
        return x + v * dt, v

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 1 / 30)
        self.last = now

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))

        tw, th = self._target_size()
        self.pw, self.pwv = self._spring(self.pw, self.pwv, tw, dt)
        self.ph, self.phv = self._spring(self.ph, self.phv, th, dt)
        f = 1.0 - math.exp(-9.0 * dt)
        self.lamp += ((0.0 if self.state == "idle" else 1.0) - self.lamp) * f
        want_shift = PITCH_X * 0.5 if self.state == "idle" else 0.0   # QUOTAL is even
        self.shift += (want_shift - self.shift) * f

        grid = self._targets()
        for r in range(ROWS):
            for c in range(COLS):
                self.cells[r][c].step(dt, grid[r][c], self.rnd)

        if self.state == "processing":              # a wave of flips along the word
            self.kick_t -= dt
            if self.kick_t <= 0.0:
                self.kick_t = 0.085
                start = (COLS - len(PROC_WORD)) // 2
                self.cells[0][start + self.kick_i % len(PROC_WORD)].kick(self.rnd)
                self.kick_i += 1
        elif self.state == "idle":                  # the board is alive
            self.idle_t -= dt
            if self.idle_t <= 0.0:
                self.idle_t = self.rnd.uniform(2.2, 4.2)
                start = (COLS - len(IDLE_WORD)) // 2
                self.cells[0][start + self.rnd.randrange(len(IDLE_WORD))].kick(self.rnd)
        self.update()

    # ---- painting helpers ----
    def _half(self, p, ch, x, y, top, sy, shade):
        """One half of a flap tile; sy squashes it about the hinge (a flap in motion)."""
        w, h = CELL_W, CELL_H
        hy = y + h / 2
        half = QRectF(x, y if top else hy, w, h / 2)
        p.save()
        if sy != 1.0:
            p.translate(0, hy)
            p.scale(1.0, max(0.001, sy))
            p.translate(0, -hy)
        p.setClipRect(half, Qt.IntersectClip)
        g = QLinearGradient(0, half.top(), 0, half.bottom())
        if top:
            g.setColorAt(0.0, QColor(44, 45, 50))
            g.setColorAt(1.0, QColor(29, 30, 34))
        else:
            g.setColorAt(0.0, QColor(24, 25, 29))
            g.setColorAt(1.0, QColor(15, 16, 19))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(x, y, w, h), 2.4, 2.4)
        if ch != " ":
            p.setPen(QColor(*self.c_ink))
            p.drawText(QRectF(x, y + 0.6, w, h), Qt.AlignCenter, ch)
        if shade > 0.0:
            p.fillRect(half, QColor(0, 0, 0, int(255 * shade)))
        p.restore()

    def _cell(self, p, cell, x, y):
        if not cell.flipping:
            self._half(p, cell.cur, x, y, True, 1.0, 0.0)
            self._half(p, cell.cur, x, y, False, 1.0, 0.0)
        else:
            t = cell.t
            self._half(p, cell.nxt, x, y, True, 1.0, 0.0)        # new top, behind
            self._half(p, cell.cur, x, y, False, 1.0, 0.0)       # old bottom, behind
            if t < 0.5:                                          # old top falls away
                s = math.cos(t * math.pi)
                self._half(p, cell.cur, x, y, True, s, 0.55 * (1 - s))
            else:                                                # new bottom lands
                s = -math.cos(t * math.pi)
                self._half(p, cell.nxt, x, y, False, s, 0.55 * (1 - s))
        hy = y + CELL_H / 2                                      # the hinge
        p.fillRect(QRectF(x, hy - 0.55, CELL_W, 1.1), QColor(0, 0, 0, 230))
        p.fillRect(QRectF(x, hy + 0.55, CELL_W, 0.7), QColor(255, 255, 255, 14))

    def _glow_at(self, p, x, y, radius, opacity):
        if opacity <= 0.01:
            return
        p.setOpacity(min(1.0, opacity))
        p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                     self.glow, QRectF(0, 0, self.glow_size * 2, self.glow_size * 2))
        p.setOpacity(1.0)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        cx = w / 2
        now = time.perf_counter()
        t = now - self.t0
        pw, ph = self.pw, self.ph
        rect = QRectF(cx - pw / 2, h - MARGIN_BOTTOM - ph, pw, ph)
        rr = min(ph / 2, 22.0)
        lamp = clamp01(self.lamp)
        lr, lg, lb = self.c_lamp

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            rad = min(r.height() / 2, rr + g)
            p.setBrush(QColor(0, 0, 0, int(max(0, 28 - i * 3.0))))
            p.drawRoundedRect(r, rad, rad)

        # brushed-metal capsule
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(66, 68, 75))
        grad.setColorAt(0.45, QColor(30, 31, 36))
        grad.setColorAt(1.0, QColor(13, 14, 17))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        # recessed board window
        panel_rect = rect.adjusted(4.5, 4.5, -4.5, -4.5)
        pr_ = max(3.0, rr - 4.5)
        panel = QPainterPath()
        panel.addRoundedRect(panel_rect, pr_, pr_)
        p.setBrush(QColor(5, 5, 7))
        p.drawPath(panel)

        p.save()
        p.setClipPath(panel)

        # --- the board ----------------------------------------------------
        lamp_off = lamp * LAMP_COL / 2
        board_cx = rect.center().x() + lamp_off + self.shift
        top = rect.top() + PAD_V
        p.setFont(self.font)
        p.save()                                # board window: exactly the tiles that fit
        win_left = rect.left() + PAD_X + lamp * LAMP_COL - 1.0
        p.setClipRect(QRectF(win_left, rect.top(), rect.right() - PAD_X + 1.0 - win_left,
                             rect.height()), Qt.IntersectClip)
        for r in range(ROWS):
            ry = top + r * PITCH_Y
            if ry > rect.bottom():
                continue
            for c in range(COLS):
                x = board_cx + (c - (COLS - 1) / 2) * PITCH_X - CELL_W / 2
                if x + CELL_W < panel_rect.left() or x > panel_rect.right():
                    continue
                self._cell(p, self.cells[r][c], x, ry)

        p.restore()

        # --- lamp column: a VU meter in the metal ----------------------------
        if lamp > 0.02:
            lx = rect.left() + PAD_X + 2.0
            n = 5
            gap = max(4.7, min(7.0, (ph - 14.0) / 5.0))
            for i in range(n):                                  # i = 0 is the bottom lamp
                ly = rect.center().y() + ((n - 1) / 2 - i) * gap
                if self.state == "processing":                  # chase up and down
                    pos = 2.0 + 2.2 * math.sin(t * 5.0)
                    lit = math.exp(-((i - pos) ** 2) / 0.9)
                else:
                    lit = clamp01(self.level * (n + 0.4) - i)
                lit *= lamp
                col = mix((lr, lg, lb), self.c_hot, i / (n - 1) * 0.7)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(col[0], col[1], col[2], int(255 * (0.13 * lamp + 0.87 * lit))))
                p.drawEllipse(QPointF(lx + 3.0, ly), 1.7, 1.7)
                if lit > 0.05:
                    p.setCompositionMode(QPainter.CompositionMode_Plus)
                    self._glow_at(p, lx + 3.0, ly, 7.5, lit * 0.8)
                    p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # lamp light washing over the board when you are loud
        if self.level > 0.05:
            wash = QRadialGradient(QPointF(rect.left() + PAD_X, rect.center().y()), pw * 0.75)
            wash.setColorAt(0.0, QColor(lr, lg, lb, int(34 * self.level)))
            wash.setColorAt(1.0, QColor(lr, lg, lb, 0))
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.fillRect(panel_rect, QBrush(wash))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # glass over the board
        glass = QLinearGradient(panel_rect.left(), panel_rect.top(),
                                panel_rect.left() + pw * 0.55, panel_rect.bottom())
        glass.setColorAt(0.0, QColor(255, 255, 255, 30))
        glass.setColorAt(0.5, QColor(255, 255, 255, 5))
        glass.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(panel_rect, QBrush(glass))
        p.restore()

        # inner shadow of the window, then the capsule edge
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 210), 2.4))
        p.drawRoundedRect(panel_rect.adjusted(-0.8, -0.8, 0.8, 0.8), pr_ + 0.8, pr_ + 0.8)
        p.setPen(QPen(QColor(255, 255, 255, 22), 0.8))
        p.drawRoundedRect(panel_rect.adjusted(0.4, 0.4, -0.4, -0.4), pr_, pr_)
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 110))
        edge.setColorAt(0.5, QColor(255, 255, 255, 30))
        edge.setColorAt(1.0, QColor(255, 255, 255, 16))
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
        for name in STYLES:
            m.addAction(f"Board: {name}", lambda n=name: self.set_style(n))
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
                frames.append((rnd.uniform(0.30, 0.42), " ".join(shown + [MISHEAR[w]])))
                frames.append((0.34, " ".join(shown + [w])))
            else:
                frames.append((rnd.uniform(0.26, 0.42), " ".join(shown + [w])))
            shown.append(w)
        return frames

    def _on_state(self, st):
        self.timer.stop()
        if st == "recording":
            self.frames, self.idx = self._build(), 0
            self.timer.start(1400)               # let the board say LISTENING first
        elif st == "processing":
            QTimer.singleShot(2600, lambda: self.pill.state_request.emit("idle")
                              if self.pill.state == "processing" else None)
        elif st == "idle" and self.autoplay:
            QTimer.singleShot(2200, lambda: self.pill.state_request.emit("recording"))

    def _step(self):
        if not self.pill.recording:
            return
        if self.idx < len(self.frames):
            delay, text = self.frames[self.idx]
            self.pill.text_request.emit(text)
            self.idx += 1
            self.timer.start(int(delay * 1000))
        elif self.autoplay:                      # sentence finished: stop dictating
            QTimer.singleShot(1400, lambda: self.pill.state_request.emit("processing"))


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="fake audio AND a fake streaming transcript")
    ap.add_argument("--autoplay", action="store_true",
                    help="with --demo: loop record -> process -> idle forever")
    ap.add_argument("--style", choices=sorted(STYLES), default="classic")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, style=args.style)
    if args.demo:
        DemoTranscriber(pill, autoplay=args.autoplay)
    pill.place_default()
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
