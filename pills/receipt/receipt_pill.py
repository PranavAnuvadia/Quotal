"""
Receipt recording pill (PySide6) - a tiny thermal printer in a capsule.
Your live transcript is PRINTED on a paper strip that feeds out of the top.

    pip install PySide6 sounddevice numpy
    python receipt_pill.py --demo                  # fake speech + fake transcript
    python receipt_pill.py --demo --autoplay       # loops hands-free
    python receipt_pill.py --body graphite         # cream | graphite

How it behaves
    Idle        compact cream capsule: status LED, QUOTAL label, a paper slit
    Listening   the capsule widens, the LED glows and a LED meter follows your
                voice. When words arrive the paper feeds out of the slit and a
                glowing print head sweeps along each line, printing it. The
                paper flutters with your loudness and scrolls up line by line
    Processing  the strip is TORN OFF and flung away; the meter becomes a
                scanner while your AI polishes the text

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # tears the paper off
    pill.state_request.emit("idle")
    pill.set_body("graphite")

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
                           QRadialGradient, QPixmap, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (112.0, 32.0)      # capsule sizes
LISTEN_SIZE = (252.0, 36.0)
PROC_SIZE = (176.0, 32.0)

PAPER_W = 216.0
PAPER_PADX, PAPER_PAD_T, PAPER_PAD_B = 13.0, 13.0, 9.0
LINE_H = 17.0
MAX_LINES = 3                  # lines visible on the strip
FONT_PX = 12
PRINT_SPEED = 560.0            # px/s: how fast the print head sweeps
TEAR_T = 0.6                   # seconds for the torn strip to fly away

MARGIN = 26
MARGIN_BOTTOM = 20
TOP_ROOM = 160                 # room above the capsule for paper and the tear-off

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)

BODIES = {
    "cream": dict(top=(246, 244, 238), mid=(228, 225, 217), bot=(204, 201, 192),
                  edge=(40, 38, 34), unlit=(182, 178, 168), label=(96, 93, 86),
                  shadow=(40, 34, 20)),
    "graphite": dict(top=(70, 72, 78), mid=(36, 37, 42), bot=(16, 17, 20),
                     edge=(0, 0, 0), unlit=(62, 63, 68), label=(150, 152, 160),
                     shadow=(0, 0, 0)),
}
INK = (28, 27, 31)
PAPER = (254, 252, 246)
ORANGE = (255, 112, 30)
RED = (255, 58, 38)
FONT_MONO = ["JetBrains Mono", "SF Mono", "Menlo", "Consolas", "Roboto Mono",
             "DejaVu Sans Mono", "Liberation Mono", "Courier New"]
FONT_UI = ["Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue", "Roboto",
           "Noto Sans", "Arial"]

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
    state_request = Signal(str)          # thread-safe: change state
    text_request = Signal(str)           # thread-safe: set the live text
    state_changed = Signal(str)          # emitted after every state change

    def __init__(self, audio, auto_finish=0, body="cream"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"

        self.pw, self.pwv = IDLE_SIZE[0], 0.0           # animated capsule size
        self.ph, self.phv = IDLE_SIZE[1], 0.0
        self.paper_h, self.paper_v = 0.0, 0.0           # animated strip height
        self.cur, self.curv = 0.0, 0.0                  # animated "current line"
        self.tear = 0.0                                 # 0 = attached .. 1 = gone
        self.level = 0.0

        self.text = ""
        self.lines = []
        self.printed = []                               # px printed on each line

        fams = set(QFontDatabase.families())
        mono = next((f for f in FONT_MONO if f in fams), None)
        self.mono = QFont(mono) if mono else QFont()
        self.mono.setStyleHint(QFont.Monospace)
        self.mono.setPixelSize(FONT_PX)
        self.mono.setWeight(QFont.Medium)
        self.mfm = QFontMetricsF(self.mono)
        self.char_w = self.mfm.horizontalAdvance("M")
        self.cols = max(8, int((PAPER_W - 2 * PAPER_PADX) / self.char_w))
        ui = next((f for f in FONT_UI if f in fams), None)
        self.ui = QFont(ui) if ui else QFont()
        self.ui.setPixelSize(9)
        self.ui.setWeight(QFont.Bold)
        self.ui.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        self.resize(int((max(LISTEN_SIZE[0], PAPER_W) + MARGIN * 2 + 70) * SCALE),
                    int((TOP_ROOM + LISTEN_SIZE[1] + MARGIN_BOTTOM + 14) * SCALE))

        self.set_body(body)
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- body colour / sprites ----
    def set_body(self, name):
        """'cream' | 'graphite'. Safe to call any time."""
        self.body_name = name if name in BODIES else "cream"
        self.b = BODIES[self.body_name]
        size = 36
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        r, g, b = ORANGE
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, QColor(r, g, b, 200))
        grad.setColorAt(0.35, QColor(r, g, b, 60))
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
            if prev != "recording":                    # fresh strip of paper
                self.text, self.lines, self.printed = "", [], []
                self.tear, self.cur, self.paper_h = 0.0, 0.0, 0.0
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

    # ---- text -> paper ----
    def set_text(self, text):
        """Replace the live text. Already-printed text stays printed."""
        if not self.recording:
            return
        self.text = text
        lines, cur = [], ""
        for w in text.split():
            w = w[:self.cols]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= self.cols:
                cur += " " + w
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        self.lines = lines
        while len(self.printed) < len(lines):
            self.printed.append(0.0)
        del self.printed[len(lines):]

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt, k=300.0, c=27.0):
        v += (k * (target - x) - c * v) * dt
        return x + v * dt, v

    def _target_size(self):
        if self.state == "recording":
            return LISTEN_SIZE
        if self.state == "processing":
            return PROC_SIZE
        return IDLE_SIZE

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

        # print head: sweeps along the newest line; older lines are complete
        n = len(self.lines)
        for i in range(n):
            full = len(self.lines[i]) * self.char_w
            if i < n - 1:
                self.printed[i] = full
            else:
                self.printed[i] = min(full, self.printed[i] + PRINT_SPEED * dt)

        # paper feed
        target_h = 0.0
        if self.lines:
            target_h = PAPER_PAD_T + min(n, MAX_LINES) * LINE_H + PAPER_PAD_B
        if self.recording or self.tear <= 0.0:
            self.paper_h, self.paper_v = self._spring(self.paper_h, self.paper_v,
                                                      target_h, dt, 260.0, 24.0)
            self.paper_h = max(0.0, self.paper_h)
        self.cur, self.curv = self._spring(self.cur, self.curv,
                                           float(max(0, n - 1)), dt, 230.0, 25.0)

        # tear-off once we stop recording
        if not self.recording and self.lines:
            self.tear = min(1.0, self.tear + dt / TEAR_T)
            if self.tear >= 1.0:
                self.text, self.lines, self.printed = "", [], []
                self.paper_h = self.paper_v = 0.0
        self.update()

    # ---- painting helpers ----
    def _glow_at(self, p, x, y, radius, opacity):
        if opacity <= 0.01:
            return
        p.setOpacity(min(1.0, opacity))
        p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                     self.glow, QRectF(0, 0, self.glow_size * 2, self.glow_size * 2))
        p.setOpacity(1.0)

    def _paper(self, p, rect, now, t):
        h = self.paper_h
        if h < 2.0:
            return
        slot_x, slot_y = rect.center().x(), rect.top() + 5.0
        e = self.tear * self.tear
        lv = self.level
        flutter = (1.1 * lv * math.sin(t * 17.0) + 0.3 * math.sin(t * 2.3)) * (1 - self.tear)
        p.save()
        p.translate(slot_x + 0.5 * lv * math.sin(t * 29.0) + e * 34.0,
                    slot_y - e * 84.0)
        p.rotate(flutter + e * 11.0)
        p.translate(-slot_x, -slot_y)
        base_op = clamp01(1.0 - e * 1.05)
        p.setOpacity(base_op)

        x0 = slot_x - PAPER_W / 2
        top = slot_y - h
        tooth, depth = 6.0, 3.0
        path = QPainterPath()
        path.moveTo(x0, slot_y + 4)
        path.lineTo(x0, top + depth)
        n = int(PAPER_W / tooth)
        for i in range(n):                                   # torn zig-zag edge
            path.lineTo(x0 + i * tooth + tooth / 2, top + (0 if i % 2 == 0 else depth))
            path.lineTo(x0 + (i + 1) * tooth, top + depth)
        path.lineTo(x0 + PAPER_W, slot_y + 4)
        path.closeSubpath()

        sh = QPainterPath(path)                              # drop shadow
        sh.translate(1.6, 2.2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 46))
        p.drawPath(sh)

        g = QLinearGradient(x0, 0, x0 + PAPER_W, 0)
        g.setColorAt(0.0, QColor(238, 234, 222))
        g.setColorAt(0.08, QColor(*PAPER))
        g.setColorAt(0.92, QColor(*PAPER))
        g.setColorAt(1.0, QColor(236, 232, 220))
        p.setBrush(QBrush(g))
        p.drawPath(path)

        p.save()
        p.setClipPath(path)
        cs = QLinearGradient(0, slot_y + 4, 0, slot_y - 18)  # contact shadow at the slit
        cs.setColorAt(0.0, QColor(0, 0, 0, 105))
        cs.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(cs))
        p.drawRect(QRectF(x0, slot_y - 18, PAPER_W, 22))

        # printed lines
        p.setFont(self.mono)
        fm = self.mfm
        off = (LINE_H - fm.height()) / 2 + fm.ascent()
        first = max(0.0, self.cur - (MAX_LINES - 1))
        text_left = x0 + PAPER_PADX
        n = len(self.lines)
        for i, line in enumerate(self.lines):
            row = i - first
            la = clamp01(row + 1.0)                          # lines fade out as they scroll off
            if la <= 0.01 or row > MAX_LINES:
                continue
            p.setOpacity(base_op * la)
            ytop = top + PAPER_PAD_T + row * LINE_H
            base = ytop + off
            px = self.printed[i]
            fresh = QRectF(text_left + max(0.0, px - 9.0), ytop, 9.0 + 0.5, LINE_H)
            done = QRectF(text_left - 1, ytop, max(0.0, px - 9.0) + 1, LINE_H)
            p.save()
            p.setClipRect(done, Qt.IntersectClip)
            p.setPen(QColor(*INK))
            p.drawText(QPointF(text_left, base), line)
            p.restore()
            p.save()                                         # just-printed: still hot
            p.setClipRect(fresh, Qt.IntersectClip)
            p.setPen(QColor(INK[0] + 70, INK[1] + 38, INK[2] + 34))
            p.drawText(QPointF(text_left, base), line)
            p.restore()
            if i == n - 1 and self.recording and px < len(line) * self.char_w - 0.5:
                hx = text_left + px                          # the print head
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                self._glow_at(p, hx, ytop + LINE_H / 2, 15.0, 0.55 + 0.45 * lv)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*RED, 235))
                p.drawRoundedRect(QRectF(hx - 0.6, ytop + 1.5, 1.7, LINE_H - 3), 0.8, 0.8)
        p.restore()
        p.restore()

    def _meter(self, p, rect, t, fade):
        left, right = rect.left() + 34.0, rect.right() - 86.0
        regw = right - left
        n = int(regw / 8.2)
        if n < 3 or fade < 0.02:
            return
        pitch = regw / n
        cy = rect.center().y() + 1.0
        for i in range(n):
            if self.state == "processing":
                pos = (n - 1) * (0.5 + 0.5 * math.sin(t * 6.0))
                lit = math.exp(-((i - pos) ** 2) / 1.3)
            else:
                lit = clamp01(self.level * n * 1.05 - i + 0.35)
            col = RED if i / n > 0.78 else ORANGE
            x = left + i * pitch + (pitch - 6.0) / 2
            seg = QRectF(x, cy - 4.5, 6.0, 9.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*self.b["unlit"], int(255 * fade)))
            p.drawRoundedRect(seg, 1.6, 1.6)
            if lit > 0.02:
                p.setBrush(QColor(*col, int(255 * lit * fade)))
                p.drawRoundedRect(seg, 1.6, 1.6)
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                self._glow_at(p, x + 3.0, cy, 8.0, lit * fade * 0.7)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)

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
        rr = ph / 2
        b = self.b
        wide = clamp01((pw - IDLE_SIZE[0]) / 40.0)

        # soft shadow
        p.setPen(Qt.NoPen)
        sr, sg, sb = b["shadow"]
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(sr, sg, sb, int(max(0, 34 - i * 3.6))))
            p.drawRoundedRect(r, min(r.height() / 2, rr + g), min(r.height() / 2, rr + g))

        # capsule body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(*b["top"]))
        grad.setColorAt(0.5, QColor(*b["mid"]))
        grad.setColorAt(1.0, QColor(*b["bot"]))
        p.setBrush(QBrush(grad))
        p.drawPath(body)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 150 if self.body_name == "cream" else 40), 1.0))
        p.drawRoundedRect(rect.adjusted(1.2, 1.2, -1.2, -1.2), rr - 1.0, rr - 1.0)

        # paper strip, then the slit it comes out of
        p.save()
        p.setClipRect(QRectF(0, 0, w, rect.top() + 5.0))
        self._paper(p, rect, now, t)
        p.restore()
        slit_w = min(PAPER_W + 10.0, pw - 34.0)
        slit = QRectF(rect.center().x() - slit_w / 2, rect.top() + 3.9, slit_w, 2.8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(22, 21, 24))
        p.drawRoundedRect(slit, 1.4, 1.4)
        p.setBrush(QColor(255, 255, 255, 60 if self.body_name == "cream" else 24))
        p.drawRoundedRect(QRectF(slit.left() + 1, slit.bottom(), slit.width() - 2, 0.9), 0.4, 0.4)

        # status LED
        lx, ly = rect.left() + 18.0, rect.center().y() + 1.5
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QPointF(lx, ly + 0.6), 5.6, 5.6)
        p.setBrush(QColor(*b["unlit"]))
        p.drawEllipse(QPointF(lx, ly), 5.0, 5.0)
        if self.state == "recording":
            on = 0.72 + 0.28 * self.level + 0.1 * math.sin(t * 4.0)
            col = RED
        elif self.state == "processing":
            on = 0.5 + 0.5 * (math.sin(t * 9.0) > 0)
            col = ORANGE
        else:
            on, col = 0.22, ORANGE
        p.setBrush(QColor(*col, int(255 * clamp01(on))))
        p.drawEllipse(QPointF(lx, ly), 3.5, 3.5)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        self._glow_at(p, lx, ly, 13.0, clamp01(on) * 0.8)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # meter + label
        meter_fade = clamp01((pw - 150.0) / 40.0)
        self._meter(p, rect, t, meter_fade)
        label = {"idle": "QUOTAL", "recording": "LISTENING",
                 "processing": "ENHANCING"}[self.state]
        p.setFont(self.ui)
        p.setPen(QColor(*b["label"]))
        lab = QRectF(rect.right() - 92.0, rect.top(), 78.0, ph + 2.0)
        if self.state == "idle":
            lab = QRectF(lx + 12.0, rect.top(), rect.right() - lx - 24.0, ph + 2.0)
            p.drawText(lab, Qt.AlignVCenter | Qt.AlignHCenter, label)
        else:
            p.drawText(lab, Qt.AlignVCenter | Qt.AlignRight, label)

        # edge
        p.setBrush(Qt.NoBrush)
        er, eg, eb = b["edge"]
        p.setPen(QPen(QColor(er, eg, eb, 70), 1.0))
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
        for name in BODIES:
            m.addAction(f"Body: {name}", lambda n=name: self.set_body(n))
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
            self.timer.start(1100)
        elif st == "processing":
            QTimer.singleShot(2400, lambda: self.pill.state_request.emit("idle")
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
            QTimer.singleShot(1300, lambda: self.pill.state_request.emit("processing"))


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="fake audio AND a fake streaming transcript")
    ap.add_argument("--autoplay", action="store_true",
                    help="with --demo: loop record -> process -> idle forever")
    ap.add_argument("--body", choices=sorted(BODIES), default="cream")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, body=args.body)
    if args.demo:
        DemoTranscriber(pill, autoplay=args.autoplay)
    pill.place_default()
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
