"""
Cassette recording pill (PySide6) - a tape deck in a capsule. Your words are
RECORDED onto tape.

    pip install PySide6 sounddevice numpy
    python cassette_pill.py --demo                  # fake speech + fake transcript
    python cassette_pill.py --demo --autoplay       # loops hands-free
    python cassette_pill.py --body black            # orange | black | cream

How it behaves
    Idle        compact deck, reels at rest, QUOTAL printed on the tape
    Listening   the reels turn and the tape runs ONLY while you speak (speed
                follows your loudness; a smaller tape pack spins faster, like
                the real thing). The record head glows red and your words are
                written onto the tape at the head, then ride left toward the
                take-up reel. The tape pack slowly moves from one reel to the
                other
    Processing  REWIND: both reels spin backwards, fast, and the tape winds
                back while your AI polishes the text

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # rewinds
    pill.state_request.emit("idle")
    pill.set_body("black")

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
                           QRadialGradient, QPixmap, QPolygonF, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (150.0, 40.0)      # deck sizes
LISTEN_SIZE = (304.0, 96.0)
PROC_SIZE = (190.0, 40.0)
CPS = 60.0                     # typing speed, characters per second
TAPE_SPEED = (40.0, 260.0)     # px/s: at silence, at full volume
REWIND_SPEED = 430.0
MARGIN = 26
MARGIN_BOTTOM = 20

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
CREAM = (250, 238, 216)
HOT = (255, 172, 92)
REC = (255, 56, 40)

BODIES = {
    "orange": dict(top=(255, 156, 76), mid=(242, 112, 34), bot=(190, 76, 16),
                   edge=(96, 36, 8), hi=(255, 255, 255)),
    "black":  dict(top=(74, 76, 82), mid=(38, 39, 44), bot=(16, 17, 20),
                   edge=(0, 0, 0), hi=(255, 255, 255)),
    "cream":  dict(top=(248, 246, 240), mid=(230, 227, 219), bot=(204, 200, 191),
                   edge=(60, 56, 48), hi=(255, 255, 255)),
}
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


def mix(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)),
            int(lerp(c1[2], c2[2], t)))


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe: change state
    text_request = Signal(str)           # thread-safe: set the live text
    state_changed = Signal(str)          # emitted after every state change

    def __init__(self, audio, auto_finish=0, body="orange"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"

        self.pw, self.pwv = IDLE_SIZE[0], 0.0
        self.ph, self.phv = IDLE_SIZE[1], 0.0
        self.level = 0.0
        self.prog = 0.0                          # how much tape has moved reels
        self.tape_off = 0.0                      # tape tick-mark scroll
        self.ang = [0.0, 0.0]                    # reel angles
        self.vx = 0.0                            # current tape velocity (px/s)
        self.needle, self.needle_v, self.counter = 0.0, 0.0, 0.0

        self.text, self.shown = "", 0.0          # typewriter state

        fams = set(QFontDatabase.families())
        mono = next((f for f in FONT_MONO if f in fams), None)
        self.mono = QFont(mono) if mono else QFont()
        self.mono.setStyleHint(QFont.Monospace)
        self.mono.setPixelSize(14)
        self.mono.setWeight(QFont.Bold)
        self.mfm = QFontMetricsF(self.mono)
        self.cw = self.mfm.horizontalAdvance("M")
        ui = next((f for f in FONT_UI if f in fams), None)
        self.ui = QFont(ui) if ui else QFont()
        self.ui.setPixelSize(10)
        self.ui.setWeight(QFont.Bold)
        self.ui.setLetterSpacing(QFont.AbsoluteSpacing, 2.2)

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        self.resize(int((LISTEN_SIZE[0] + MARGIN * 2) * SCALE),
                    int((LISTEN_SIZE[1] + MARGIN + MARGIN_BOTTOM) * SCALE))

        self.set_body(body)
        self._make_glow()
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    def set_body(self, name):
        """'orange' | 'black' | 'cream'. Safe to call any time."""
        self.body_name = name if name in BODIES else "orange"
        self.b = BODIES[self.body_name]

    def _make_glow(self):
        size = 36
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        r, g, b = REC
        grad = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        grad.setColorAt(0.0, QColor(r, g, b, 210))
        grad.setColorAt(0.35, QColor(r, g, b, 60))
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
                self.text, self.shown = "", 0.0
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
        """Replace the live text. Letters already recorded stay recorded."""
        if not self.recording:
            return
        k = 0
        while k < len(text) and k < len(self.text) and text[k] == self.text[k]:
            k += 1
        self.shown = min(self.shown, float(k))
        self.text = text

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt, k=300.0, c=27.0):
        v += (k * (target - x) - c * v) * dt
        return x + v * dt, v

    def _target_size(self):
        return {"recording": LISTEN_SIZE, "processing": PROC_SIZE,
                "idle": IDLE_SIZE}[self.state]

    def _pack_radii(self, R):
        hub = R * 0.44
        mx = R * 0.93
        take = lerp(hub + 1.6, mx, self.prog)        # left reel winds up
        supply = lerp(mx, hub + 1.6, self.prog)      # right reel runs down
        return take, supply

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

        # the tape only runs while you speak
        if self.recording:
            v = lerp(TAPE_SPEED[0], TAPE_SPEED[1], self.level) * (1.0 if self.level > 0.04 else 0.25)
            target_vx = -v                                   # tape travels right -> left
            self.prog = min(1.0, self.prog + dt * (0.002 + 0.05 * self.level))
        elif self.state == "processing":
            target_vx = REWIND_SPEED
            self.prog = max(0.0, self.prog - dt * 0.9)
        else:
            target_vx = 0.0
            self.prog = max(0.0, self.prog - dt * 0.3)
        self.vx += (target_vx - self.vx) * (1.0 - math.exp(-9.0 * dt))
        self.tape_off = (self.tape_off + self.vx * dt) % 14.0

        R = lerp(14.0, 18.0, self._big())
        take, supply = self._pack_radii(R)
        for i, rp in enumerate((take, supply)):
            w_ = max(-18.0, min(18.0, self.vx / max(rp, 4.0)))
            self.ang[i] += -w_ * dt                          # tape left => reels turn CCW

        # VU needle is a real spring, so it overshoots and settles; counter follows the tape
        tgt = self.level if self.recording else 0.0
        self.needle_v += (170.0 * (tgt - self.needle) - 11.5 * self.needle_v) * dt
        self.needle += self.needle_v * dt
        self.counter = max(0.0, self.counter - self.vx * dt / 45.0)

        if self.shown < len(self.text):
            self.shown = min(float(len(self.text)),
                             self.shown + (CPS + 3.0 * (len(self.text) - self.shown)) * dt)
        self.update()

    def _big(self):
        return clamp01((self.ph - 40.0) / 56.0)

    def _label(self, p, lab):
        """The cassette label: a working VU meter, a title stripe and a tape counter."""
        a = clamp01((lab.height() - 8.0) / 16.0)
        if a <= 0.01:
            return
        p.save()
        p.setOpacity(a)
        path = QPainterPath()
        path.addRoundedRect(lab, 5.0, 5.0)
        g = QLinearGradient(0, lab.top(), 0, lab.bottom())
        g.setColorAt(0.0, QColor(253, 248, 236))
        g.setColorAt(1.0, QColor(233, 224, 202))
        p.setPen(QPen(QColor(0, 0, 0, 55), 1.0))
        p.setBrush(QBrush(g))
        p.drawPath(path)
        p.setClipPath(path)

        dial = QRectF(lab.left(), lab.top(), lab.width() * 0.38, lab.height())
        info = QRectF(dial.right(), lab.top(), lab.width() - dial.width(), lab.height())

        # VU dial
        px, py = dial.center().x(), dial.bottom() + 1.0
        r = min(dial.height() * 0.95, dial.width() * 0.52)
        arc = QRectF(px - r * 0.94, py - r * 0.94, r * 1.88, r * 1.88)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(40, 38, 36, 170), 1.0))
        p.drawArc(arc, 60 * 16, 80 * 16)
        p.setPen(QPen(QColor(205, 44, 32), 1.8))
        p.drawArc(arc, 40 * 16, 20 * 16)
        for k in range(11):
            th = math.radians(-50.0 + 10.0 * k)
            ri = r * (0.80 if k % 5 == 0 else 0.86)
            ro = r * 0.94
            p.setPen(QPen(QColor(205, 44, 32) if k >= 8 else QColor(40, 38, 36, 190),
                          1.5 if k % 5 == 0 else 0.9))
            p.drawLine(QPointF(px + ri * math.sin(th), py - ri * math.cos(th)),
                       QPointF(px + ro * math.sin(th), py - ro * math.cos(th)))
        f = QFont(self.ui)
        f.setPixelSize(7)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        p.setFont(f)
        p.setPen(QColor(40, 38, 36, 150))
        p.drawText(QPointF(dial.left() + 7.0, dial.top() + 10.0), "VU")
        th = math.radians(-48.0 + 96.0 * max(0.0, min(1.1, self.needle)))
        pen = QPen(QColor(186, 28, 22), 1.3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(px, py), QPointF(px + r * 0.9 * math.sin(th), py - r * 0.9 * math.cos(th)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(34, 30, 28))
        p.drawEllipse(QPointF(px, py), 2.8, 2.8)

        # title stripe, ruled lines, counter
        stripe = QRectF(info.left() + 8.0, lab.top() + 5.0, info.width() - 16.0, 11.0)
        p.setBrush(QColor(238, 104, 28))
        p.drawRoundedRect(stripe, 2.0, 2.0)
        f2 = QFont(self.ui)
        f2.setPixelSize(8)
        f2.setLetterSpacing(QFont.AbsoluteSpacing, 1.6)
        p.setFont(f2)
        p.setPen(QColor(255, 246, 228))
        p.drawText(stripe, Qt.AlignCenter, "QUOTAL  \u00b7  SIDE A")
        p.setPen(QPen(QColor(90, 120, 175, 90), 0.8))
        for yy in (lab.top() + 24.0, lab.top() + 31.0):
            p.drawLine(QPointF(info.left() + 8.0, yy), QPointF(info.right() - 56.0, yy))
        box = QRectF(info.right() - 48.0, lab.bottom() - 19.0, 40.0, 14.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(32, 27, 25))
        p.drawRoundedRect(box, 2.5, 2.5)
        p.setFont(self.mono)
        f3 = QFont(self.mono)
        f3.setPixelSize(10)
        p.setFont(f3)
        p.setPen(QColor(255, 150, 60))
        p.drawText(box, Qt.AlignCenter, "%03d" % (int(self.counter) % 1000))
        p.restore()

    # ---- painting helpers ----
    def _glow_at(self, p, x, y, radius, opacity):
        if opacity <= 0.01:
            return
        p.setOpacity(min(1.0, opacity))
        p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                     self.glow, QRectF(0, 0, self.glow_size * 2, self.glow_size * 2))
        p.setOpacity(1.0)

    def _reel(self, p, cx, cy, R, rpack, ang):
        hub = R * 0.44
        g = QRadialGradient(QPointF(cx, cy), rpack)
        g.setColorAt(0.0, QColor(84, 56, 40))
        g.setColorAt(0.65, QColor(128, 88, 62))
        g.setColorAt(1.0, QColor(176, 128, 92))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), rpack, rpack)
        p.setBrush(Qt.NoBrush)                               # winding rings
        p.setPen(QPen(QColor(255, 255, 255, 22), 0.5))
        r = hub + 2.4
        while r < rpack - 0.5:
            p.drawEllipse(QPointF(cx, cy), r, r)
            r += 2.3
        hl = QRadialGradient(QPointF(cx - rpack * 0.38, cy - rpack * 0.42), rpack * 0.8)
        hl.setColorAt(0.0, QColor(255, 255, 255, 52))
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(hl))
        p.drawEllipse(QPointF(cx, cy), rpack, rpack)
        p.setBrush(Qt.NoBrush)                               # clear flange
        p.setPen(QPen(QColor(255, 255, 255, 80), 1.0))
        p.drawEllipse(QPointF(cx, cy), R, R)
        p.setPen(QPen(QColor(255, 255, 255, 120), 1.4))
        p.drawArc(QRectF(cx - R + 1.5, cy - R + 1.5, 2 * R - 3, 2 * R - 3), 105 * 16, 65 * 16)

        p.save()                                             # the spinning hub
        p.translate(cx, cy)
        p.rotate(math.degrees(ang))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(238, 234, 224))
        p.drawEllipse(QPointF(0, 0), hub, hub)
        p.setBrush(QColor(22, 20, 22))
        p.drawEllipse(QPointF(0, 0), hub * 0.64, hub * 0.64)
        p.setBrush(QColor(238, 234, 224))
        for k in range(6):
            p.save()
            p.rotate(k * 60.0)
            p.drawRoundedRect(QRectF(-0.95, -hub * 0.66, 1.9, hub * 0.34), 0.5, 0.5)
            p.restore()
        p.restore()

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
        big = self._big()
        rr = lerp(min(ph / 2, 20.0), 11.0, big)
        b = self.b
        win_h = lerp(30.0, 44.0, big)
        label_h = max(0.0, ph - win_h - 15.0)
        R = lerp(14.0, 18.0, big)

        # shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 32 - i * 3.4))))
            p.drawRoundedRect(r, min(r.height() / 2, rr + g), min(r.height() / 2, rr + g))

        # plastic shell
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(*b["top"]))
        grad.setColorAt(0.5, QColor(*b["mid"]))
        grad.setColorAt(1.0, QColor(*b["bot"]))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        # corner screws + label (only once the shell has grown)
        if label_h > 14.0:
            sa_ = clamp01((label_h - 14.0) / 14.0)
            for sx in (rect.left() + 9.0, rect.right() - 9.0):
                sy = rect.top() + 9.0
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, int(60 * sa_)))
                p.drawEllipse(QPointF(sx, sy + 0.7), 3.0, 3.0)
                p.setBrush(QColor(214, 210, 202, int(255 * sa_)))
                p.drawEllipse(QPointF(sx, sy), 2.7, 2.7)
                p.setPen(QPen(QColor(70, 66, 60, int(230 * sa_)), 0.9))
                p.drawLine(QPointF(sx - 1.6, sy - 0.8), QPointF(sx + 1.6, sy + 0.8))
        if label_h > 8.0:
            self._label(p, QRectF(rect.left() + 17.0, rect.top() + 5.0, pw - 34.0, label_h))

        # tape window
        win = QRectF(rect.left() + 5.0, rect.bottom() - 5.0 - win_h, pw - 10.0, win_h)
        wr = max(4.0, lerp(rr - 5.0, 8.0, big))
        panel = QPainterPath()
        panel.addRoundedRect(win, wr, wr)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(22, 17, 15))
        p.drawPath(panel)
        cy = win.center().y()

        p.save()
        p.setClipPath(panel)
        x_l = win.left() + 5.0 + R
        x_r = win.right() - 5.0 - R
        take, supply = self._pack_radii(R)

        # the tape ribbon, with moving tick marks
        th = lerp(15.0, 26.0, big)
        ribbon = QRectF(x_l, cy - th / 2, x_r - x_l, th)
        tg = QLinearGradient(0, ribbon.top(), 0, ribbon.bottom())
        tg.setColorAt(0.0, QColor(150, 108, 80))
        tg.setColorAt(0.5, QColor(92, 62, 44))
        tg.setColorAt(1.0, QColor(60, 40, 30))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(tg))
        p.drawRect(ribbon)
        p.setPen(QPen(QColor(255, 232, 204, 90), 1.0))
        p.drawLine(QPointF(ribbon.left(), ribbon.top() + 1.2), QPointF(ribbon.right(), ribbon.top() + 1.2))
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        x = ribbon.left() - 14.0 + self.tape_off
        while x < ribbon.right():
            p.drawLine(QPointF(x, ribbon.top() + 3), QPointF(x, ribbon.bottom() - 3))
            x += 14.0

        # record head
        head_x = x_r - R - 14.0
        head_a = big * (0.35 + 0.65 * clamp01(self.level * 2.0 + 0.4)) if self.recording else 0.0
        if big > 0.1:
            hg = QLinearGradient(head_x - 6, 0, head_x + 6, 0)
            hg.setColorAt(0.0, QColor(190, 192, 198))
            hg.setColorAt(1.0, QColor(96, 98, 104))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(hg))
            p.setOpacity(big)
            p.drawRoundedRect(QRectF(head_x - 6, cy - th / 2 - 3, 12, th + 6), 2.5, 2.5)
            p.setOpacity(1.0)
            p.setBrush(QColor(*REC, int(255 * clamp01(0.3 + head_a))))
            p.drawEllipse(QPointF(head_x, cy - th / 2 + 2.6), 2.2, 2.2)
            if head_a > 0.02:
                p.setCompositionMode(QPainter.CompositionMode_Plus)
                self._glow_at(p, head_x, cy - th / 2 + 2.6, 15.0, head_a * 0.9)
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # reels
        self._reel(p, x_l, cy, R, take, self.ang[0])
        self._reel(p, x_r, cy, R, supply, self.ang[1])

        # what is written on the tape
        text_left = x_l + R + 8.0
        text_end = head_x - 11.0
        n = self.shown
        have_text = self.recording and len(self.text) > 0
        label = None
        if not have_text:
            label = {"idle": "QUOTAL", "recording": "LISTENING",
                     "processing": "REWIND"}[self.state]
        if have_text:
            p.save()
            p.setClipRect(QRectF(text_left - 2, ribbon.top(), text_end - text_left + 6, th))
            p.setFont(self.mono)
            fm = self.mfm
            base = cy + (fm.ascent() - fm.descent()) / 2
            first = max(0, int(n) - 60)
            for i in range(first, min(len(self.text), int(math.ceil(n)))):
                ch = self.text[i]
                if ch == " ":
                    continue
                xc = text_end - (n - i) * self.cw
                a = clamp01((xc - text_left) / 34.0) * clamp01(n - i)
                if a <= 0.01:
                    continue
                age = n - 1 - i
                col = mix(CREAM, HOT, clamp01(1.0 - age / 7.0) * 0.9)
                p.setPen(QColor(0, 0, 0, int(130 * a)))
                p.drawText(QPointF(xc, base + 1.0), ch)
                p.setPen(QColor(col[0], col[1], col[2], int(255 * a)))
                p.drawText(QPointF(xc, base), ch)
            p.restore()
        else:
            p.setFont(self.ui)
            blink = 1.0
            if self.state == "recording":
                blink = 0.5 + 0.5 * (0.5 + 0.5 * math.sin(t * 5.0))
            p.setPen(QColor(CREAM[0], CREAM[1], CREAM[2], int(235 * blink)))
            lab_left = x_l + R + 4.0
            lab_right = (head_x - 8.0) if big > 0.5 else (x_r - R - 4.0)
            if self.state == "processing":                   # rewind arrows
                ax = (lab_left + lab_right) / 2 - 36.0
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(*HOT))
                for k in range(2):
                    x0 = ax + k * 7.0
                    p.drawPolygon(QPolygonF([QPointF(x0 + 6, cy - 4), QPointF(x0, cy),
                                             QPointF(x0 + 6, cy + 4)]))
                p.setPen(QColor(CREAM[0], CREAM[1], CREAM[2], 235))
                p.drawText(QRectF(ax + 16, ribbon.top(), 80, th), Qt.AlignVCenter | Qt.AlignLeft, label)
            else:
                p.drawText(QRectF(lab_left, ribbon.top(), lab_right - lab_left, th),
                           Qt.AlignCenter, label)

        # glass over the window
        glass = QLinearGradient(win.left(), win.top(), win.left() + pw * 0.5, win.bottom())
        glass.setColorAt(0.0, QColor(255, 255, 255, 38))
        glass.setColorAt(0.5, QColor(255, 255, 255, 7))
        glass.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(win, QBrush(glass))
        p.restore()

        # bezel edges
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 200), 2.2))
        p.drawRoundedRect(win.adjusted(-0.8, -0.8, 0.8, 0.8), wr + 0.8, wr + 0.8)
        er, eg, eb = b["edge"]
        p.setPen(QPen(QColor(er, eg, eb, 110), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)
        hi = QLinearGradient(0, rect.top(), 0, rect.bottom())
        hi.setColorAt(0.0, QColor(255, 255, 255, 150))
        hi.setColorAt(0.4, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hi), 1.0))
        p.drawRoundedRect(rect.adjusted(1.4, 1.4, -1.4, -1.4), max(2.0, rr - 1.0), max(2.0, rr - 1.0))

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
    ap.add_argument("--body", choices=sorted(BODIES), default="orange")
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
