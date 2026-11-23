"""
CRT recording pill (PySide6) - a tiny 1970s television. Your live transcript
is broadcast onto its phosphor screen.

    pip install PySide6 sounddevice numpy
    python crt_pill.py --demo                  # fake speech + fake transcript
    python crt_pill.py --demo --autoplay       # loops hands-free
    python crt_pill.py --case mint --phosphor green
        case: walnut | mint | graphite      phosphor: amber | green | white

How it behaves
    Idle        the TV is OFF: a dark glass screen with one faint phosphor dot
    Listening   POWER-ON: a bright line stretches across, then opens into the
                full picture. Your words are typed in glowing phosphor with a
                blinking block cursor and a little RGB split that grows with
                your loudness. An oscilloscope trace rides the bottom of the
                screen. The volume knob turns with your voice, the speaker grille
                is a VU meter, the rabbit ears jitter with "signal", and the
                REC lamp glows
    Processing  channel change: a burst of static, then colour bars and a
                blinking PLEASE STAND BY while the channel knob clicks around.
                Back to idle, the picture collapses to a line, then a dot

Feeding it text (thread-safe, call from anywhere, e.g. your ASR callback):
    pill.state_request.emit("recording")
    pill.text_request.emit("hey team quick update")      # replaces the text
    pill.state_request.emit("processing")                # while enhancing
    pill.state_request.emit("idle")
    pill.set_case("mint"); pill.set_phosphor("green")

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
                           QRadialGradient, QPixmap, QImage, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (154.0, 54.0)      # TV sizes (body)
LISTEN_SIZE = (312.0, 88.0)
PROC_SIZE = (232.0, 62.0)
PAD = 8.0
CPS = 60.0                     # typing speed, characters per second
LINE_H = 17.0
MARGIN = 26
MARGIN_BOTTOM = 20
TOP_ROOM = 46                  # room above the body for the antenna

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)

CASES = {
    "walnut":   dict(top=(176, 118, 70), mid=(128, 82, 46), bot=(86, 52, 28),
                     grain=(58, 32, 14), plate=(228, 216, 190), plate2=(204, 190, 160)),
    "mint":     dict(top=(168, 224, 206), mid=(120, 190, 170), bot=(80, 150, 130),
                     grain=(40, 100, 84), plate=(240, 236, 222), plate2=(214, 208, 190)),
    "graphite": dict(top=(100, 102, 108), mid=(58, 60, 66), bot=(30, 31, 35),
                     grain=(0, 0, 0), plate=(170, 172, 178), plate2=(132, 134, 140)),
}
PHOSPHORS = {
    "amber": dict(main=(255, 178, 64), hot=(255, 240, 210)),
    "green": dict(main=(118, 255, 150), hot=(226, 255, 232)),
    "white": dict(main=(206, 226, 255), hot=(250, 252, 255)),
}
BARS = [(224, 224, 224), (224, 224, 0), (0, 224, 224), (0, 224, 0),
        (224, 0, 224), (224, 0, 0), (0, 0, 224)]
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


def ease(x):
    x = clamp01(x)
    return x * x * (3.0 - 2.0 * x)


class Pill(QWidget):
    state_request = Signal(str)          # thread-safe: change state
    text_request = Signal(str)           # thread-safe: set the live text
    state_changed = Signal(str)          # emitted after every state change

    def __init__(self, audio, auto_finish=0, case="walnut", phosphor="amber"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # >0: processing -> idle after N seconds
        self.state = "idle"

        self.pw, self.pwv = IDLE_SIZE[0], 0.0
        self.ph, self.phv = IDLE_SIZE[1], 0.0
        self.awake, self.awakev = 0.0, 0.0       # screen power (spring)
        self.level = 0.0
        self.hist = deque()
        self.burst = 0.0                         # static burst on channel change
        self.cur, self.curv = 0.0, 0.0
        self.chan, self.chan_t, self.chan_next = 0.0, 0.0, 0.0
        self.text, self.shown = "", 0.0
        self.lines = []
        self.rnd = random.Random(9)

        fams = set(QFontDatabase.families())
        mono = next((f for f in FONT_MONO if f in fams), None)
        self.mono = QFont(mono) if mono else QFont()
        self.mono.setStyleHint(QFont.Monospace)
        self.mono.setPixelSize(13)
        self.mono.setWeight(QFont.Bold)
        self.mfm = QFontMetricsF(self.mono)
        self.cw = self.mfm.horizontalAdvance("M")
        ui = next((f for f in FONT_UI if f in fams), None)
        self.ui = QFont(ui) if ui else QFont()
        self.ui.setPixelSize(9)
        self.ui.setWeight(QFont.Bold)
        self.ui.setLetterSpacing(QFont.AbsoluteSpacing, 1.3)
        self.cols = max(10, int((LISTEN_SIZE[0] - 62 - 24 - 18) / self.cw))

        self.t0 = time.perf_counter()
        self.last = self.t0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)
        self.resize(int((LISTEN_SIZE[0] + MARGIN * 2) * SCALE),
                    int((LISTEN_SIZE[1] + TOP_ROOM + MARGIN_BOTTOM + 6) * SCALE))

        self._make_assets()
        self.set_case(case)
        self.set_phosphor(phosphor)
        self.state_request.connect(self.set_state)
        self.text_request.connect(self.set_text)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- look ----
    def set_case(self, name):
        """'walnut' | 'mint' | 'graphite'. Safe to call any time."""
        self.case = name if name in CASES else "walnut"
        self.c = CASES[self.case]

    def set_phosphor(self, name):
        """'amber' | 'green' | 'white'. Safe to call any time."""
        self.phos_name = name if name in PHOSPHORS else "amber"
        self.phos = PHOSPHORS[self.phos_name]

    def _make_assets(self):
        rng = np.random.default_rng(3)                     # a few frames of TV snow
        self.snow = []
        for _ in range(4):
            arr = rng.integers(0, 256, (68, 120), dtype=np.uint8)
            img = QImage(arr.data, 120, 68, 120, QImage.Format_Grayscale8).copy()
            self.snow.append(QPixmap.fromImage(img))
        tile = QPixmap(4, 4)                               # scanlines
        tile.setDevicePixelRatio(2.0)
        tile.fill(Qt.transparent)
        q = QPainter(tile)
        q.fillRect(QRectF(0, 0, 2, 1), QColor(0, 0, 0, 78))
        q.end()
        self.scan = tile

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
        if state != "idle":
            self.burst = 1.0                               # static on every channel change
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
        """Replace the live text. Letters already on screen stay on screen."""
        if not self.recording:
            return
        k = 0
        while k < len(text) and k < len(self.text) and text[k] == self.text[k]:
            k += 1
        self.shown = min(self.shown, float(k))
        self.text = text
        lines, start, last_end = [], None, 0
        for m in re.finditer(r"\S+", text):
            if start is None:
                start, last_end = m.start(), m.end()
            elif m.end() - start <= self.cols:
                last_end = m.end()
            else:
                lines.append((start, last_end))
                start, last_end = m.start(), m.end()
        if start is not None:
            lines.append((start, last_end))
        self.lines = lines

    # ---- animation ----
    @staticmethod
    def _spring(x, v, target, dt, k=300.0, c=27.0):
        v += (k * (target - x) - c * v) * dt
        return x + v * dt, v

    def _sample(self, tt):
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

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.9:
            self.hist.popleft()

        tw, th = {"recording": LISTEN_SIZE, "processing": PROC_SIZE,
                  "idle": IDLE_SIZE}[self.state]
        self.pw, self.pwv = self._spring(self.pw, self.pwv, tw, dt)
        self.ph, self.phv = self._spring(self.ph, self.phv, th, dt)
        self.awake, self.awakev = self._spring(
            self.awake, self.awakev, 0.0 if self.state == "idle" else 1.0, dt, 170.0, 24.0)
        self.burst = max(0.0, self.burst - dt * 2.2)

        # the channel knob clicks around while processing
        if self.state == "processing":
            self.chan_t -= dt
            if self.chan_t <= 0.0:
                self.chan_t = 0.28
                self.chan_next += 32.0
        self.chan += (self.chan_next - self.chan) * (1.0 - math.exp(-16.0 * dt))

        if self.shown < len(self.text):
            self.shown = min(float(len(self.text)),
                             self.shown + (CPS + 3.0 * (len(self.text) - self.shown)) * dt)
        n = int(self.shown)
        typed = [1 for (s, e) in self.lines if s < n]
        self.cur, self.curv = self._spring(self.cur, self.curv,
                                           float(max(0, len(typed) - 1)), dt, 230.0, 25.0)
        self.update()

    # ---- painting helpers ----
    def _knob(self, p, cx, cy, r, angle):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QPointF(cx, cy + 1.0), r + 1.2, r + 1.2)
        sk = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.4), r * 1.5)
        sk.setColorAt(0.0, QColor(70, 66, 62))
        sk.setColorAt(1.0, QColor(24, 22, 20))
        p.setBrush(QBrush(sk))
        p.drawEllipse(QPointF(cx, cy), r, r)
        for k in range(14):                                 # knurled edge
            a = math.radians(k * 360.0 / 14.0)
            p.setBrush(QColor(255, 255, 255, 36))
            p.drawEllipse(QPointF(cx + (r - 0.9) * math.cos(a), cy + (r - 0.9) * math.sin(a)), 0.6, 0.6)
        top = QRadialGradient(QPointF(cx - r * 0.25, cy - r * 0.35), r)
        top.setColorAt(0.0, QColor(244, 238, 224))
        top.setColorAt(1.0, QColor(176, 168, 150))
        p.setBrush(QBrush(top))
        p.drawEllipse(QPointF(cx, cy), r * 0.74, r * 0.74)
        a = math.radians(angle - 90.0)
        pen = QPen(QColor(40, 34, 30), 1.3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.66 * math.cos(a), cy + r * 0.66 * math.sin(a)))

    def _screen(self, p, scr, t, now):
        """Everything on the glass."""
        rad = 11.0
        path = QPainterPath()
        path.addRoundedRect(scr, rad, rad)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 8, 7))
        p.drawPath(path)
        cx, cy = scr.center().x(), scr.center().y()
        main, hot = self.phos["main"], self.phos["hot"]
        aw = clamp01(self.awake)

        p.save()
        p.setClipPath(path)

        if aw < 0.05:                                       # OFF: one faint dot
            dot = clamp01(1.0 - aw / 0.05)
            g = QRadialGradient(QPointF(cx, cy), 7.0)
            g.setColorAt(0.0, QColor(*main, int(150 * dot * (0.6 + 0.4 * math.sin(t * 2.0)))))
            g.setColorAt(1.0, QColor(*main, 0))
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), 7.0, 7.0)
        else:
            hx = ease(aw * 2.4)                             # line stretches, then opens
            vy = ease((aw - 0.28) / 0.72)
            rw, rh = scr.width() * hx, max(2.0, scr.height() * vy)
            reg = QRectF(cx - rw / 2, cy - rh / 2, rw, rh)
            p.save()
            p.setClipRect(reg, Qt.IntersectClip)

            if self.state == "processing":
                self._bars(p, scr, t)
            else:
                self._picture(p, scr, t, now)

            if self.burst > 0.01:                           # static burst
                p.setRenderHint(QPainter.SmoothPixmapTransform, False)
                p.setOpacity(clamp01(self.burst * 0.9))
                p.drawPixmap(scr, self.snow[int(t * 30) % 4], QRectF(self.snow[0].rect()))
                p.setOpacity(1.0)
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            if vy < 0.98:                                   # the power-on flash
                p.fillRect(reg, QColor(255, 255, 240, int(235 * (1.0 - vy))))
            p.restore()
            if vy < 0.98:                                   # bloom around the bright line
                bl = QLinearGradient(0, cy - 9, 0, cy + 9)
                bl.setColorAt(0.0, QColor(*main, 0))
                bl.setColorAt(0.5, QColor(255, 255, 240, int(150 * (1.0 - vy) * hx)))
                bl.setColorAt(1.0, QColor(*main, 0))
                p.fillRect(QRectF(cx - rw / 2, cy - 9, rw, 18), QBrush(bl))

            # scanlines, rolling refresh bar, vignette
            p.fillRect(scr, QBrush(self.scan))
            if self.state != "idle":
                yb = scr.top() + ((t * 0.30) % 1.4 - 0.2) * scr.height()
                rb = QLinearGradient(0, yb - 12, 0, yb + 12)
                rb.setColorAt(0.0, QColor(255, 255, 255, 0))
                rb.setColorAt(0.5, QColor(255, 255, 255, 16))
                rb.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.fillRect(scr, QBrush(rb))

        p.save()                                            # vignette (curved glass)
        p.translate(cx, cy)
        p.scale(scr.width() / max(1.0, scr.height()), 1.0)
        vg = QRadialGradient(QPointF(0, 0), scr.height() * 0.78)
        vg.setColorAt(0.0, QColor(0, 0, 0, 0))
        vg.setColorAt(0.62, QColor(0, 0, 0, 0))
        vg.setColorAt(1.0, QColor(0, 0, 0, 190))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(vg))
        p.drawRect(QRectF(-scr.width(), -scr.height(), scr.width() * 2, scr.height() * 2))
        p.restore()

        hl = QLinearGradient(scr.left(), scr.top(), scr.left() + scr.width() * 0.5, scr.bottom())
        hl.setColorAt(0.0, QColor(255, 255, 255, 54))
        hl.setColorAt(0.35, QColor(255, 255, 255, 8))
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hl))
        p.drawRect(QRectF(scr.left(), scr.top(), scr.width(), scr.height() * 0.55))
        p.restore()

        p.setBrush(Qt.NoBrush)                              # glass rim
        p.setPen(QPen(QColor(0, 0, 0, 220), 2.4))
        p.drawRoundedRect(scr.adjusted(-0.8, -0.8, 0.8, 0.8), rad + 0.8, rad + 0.8)

    def _bars(self, p, scr, t):
        n = len(BARS)
        bw = scr.width() / n
        for i, col in enumerate(BARS):
            p.fillRect(QRectF(scr.left() + i * bw, scr.top(), bw + 0.6, scr.height() * 0.78), QColor(*col))
        for i, col in enumerate([(0, 0, 224), (16, 16, 16), (224, 0, 224), (16, 16, 16),
                                 (0, 224, 224), (16, 16, 16), (224, 224, 224)]):
            p.fillRect(QRectF(scr.left() + i * bw, scr.top() + scr.height() * 0.78, bw + 0.6,
                              scr.height() * 0.22), QColor(*col))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)  # a little snow on top
        p.setOpacity(0.16)
        p.drawPixmap(scr, self.snow[int(t * 24) % 4], QRectF(self.snow[0].rect()))
        p.setOpacity(1.0)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if int(t * 1.7) % 2 == 0 or self.burst > 0.2:           # PLEASE STAND BY, blinking
            p.setFont(self.ui)
            label = "PLEASE STAND BY"
            lw = QFontMetricsF(self.ui).horizontalAdvance(label) + 14.0
            box = QRectF(scr.center().x() - lw / 2, scr.center().y() - 8.5, lw, 17.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 215))
            p.drawRoundedRect(box, 2.5, 2.5)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box, Qt.AlignCenter, label)

    def _picture(self, p, scr, t, now):
        main, hot = self.phos["main"], self.phos["hot"]
        lv = self.level
        p.fillRect(scr, QColor(5, 11, 7))                   # phosphor glow behind the text
        gl = QRadialGradient(QPointF(scr.center().x(), scr.center().y() - 4), scr.width() * 0.62)
        gl.setColorAt(0.0, QColor(*main, int(26 + 40 * lv)))
        gl.setColorAt(1.0, QColor(*main, 0))
        p.fillRect(scr, QBrush(gl))

        p.setFont(self.mono)
        fm = self.mfm
        asc = fm.ascent()
        off = (LINE_H - fm.height()) / 2 + asc
        left, top = scr.left() + 10.0, scr.top() + 7.0
        d = 0.5 + 1.7 * lv                                   # RGB split grows with loudness
        n = int(self.shown)
        typed = [(s, e) for (s, e) in self.lines if s < n]

        def line(vis, x, y, alpha):
            p.setPen(QColor(255, 70, 60, int(96 * alpha)))
            p.drawText(QPointF(x - d, y), vis)
            p.setPen(QColor(60, 190, 255, int(96 * alpha)))
            p.drawText(QPointF(x + d, y), vis)
            p.setPen(QColor(*main, int(42 * alpha)))         # glow
            for ox, oy in ((-1.1, 0), (1.1, 0), (0, -1.1), (0, 1.1)):
                p.drawText(QPointF(x + ox, y + oy), vis)
            p.setPen(QColor(*main, int(255 * alpha)))
            p.drawText(QPointF(x, y), vis)
            if len(vis) > 2:                                 # newest letters burn white-hot
                tail = vis[-3:]
                p.setPen(QColor(*hot, int(255 * alpha)))
                p.drawText(QPointF(x + fm.horizontalAdvance(vis[:-3]), y), tail)

        blink = 0.0 if int(t * 2.4) % 2 else 1.0
        if not typed:
            line("> LISTENING", left, top + off, 0.95)
            cxp = left + fm.horizontalAdvance("> LISTENING ")
            ytop = top
        else:
            first = max(0.0, self.cur - 1.0)
            end_x = ytop = None
            for li, (s, e) in enumerate(typed):
                row = li - first
                la = clamp01(row + 1.0) * (1.0 - 0.5 * clamp01(self.cur - li))
                if la <= 0.01 or row > 2:
                    continue
                vis = self.text[s:min(e, n)]
                y = top + row * LINE_H
                line(vis, left, y + off, la)
                end_x, ytop = left + fm.horizontalAdvance(vis), y
            cxp = (end_x or left) + 1.5
        if self.recording and ytop is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*main, int(230 * blink)))
            p.drawRect(QRectF(cxp, ytop + 3.0, self.cw * 0.78, LINE_H - 6.0))

        # oscilloscope trace: amplitude envelope = your voice
        M = 46
        base = scr.bottom() - 11.0
        x0, x1 = scr.left() + 10.0, scr.right() - 10.0
        pts = []
        for i in range(M):
            u = i / (M - 1)
            env = self._sample(now - (1.0 - u) * 0.55)
            pts.append(QPointF(x0 + (x1 - x0) * u,
                               base + env * 7.5 * math.sin(i * 1.15 - t * 31.0)))
        sp = QPainterPath(pts[0])
        for q in pts[1:]:
            sp.lineTo(q)
        p.setBrush(Qt.NoBrush)
        pen = QPen(QColor(*main, 70), 3.4)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPath(sp)
        p.setPen(QPen(QColor(*hot, 235), 1.1))
        p.drawPath(sp)

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
        big = clamp01((ph - 54.0) / 34.0)
        aw = clamp01(self.awake)
        c = self.c
        lv = self.level

        # rabbit-ear antenna (behind the case)
        ax, ay = rect.left() + pw * 0.34, rect.top() + 2.0
        spread = lerp(9.0, 30.0, aw)
        length = lerp(16.0, 27.0, aw)
        sway = 2.4 * math.sin(t * 1.3)
        for sgn in (-1, 1):
            jit = lv * (2.6 * math.sin(t * 37.0 + sgn) + 1.8 * math.sin(t * 53.0))
            th = math.radians(sgn * spread + sway + jit)
            tip = QPointF(ax + length * math.sin(th), ay - length * math.cos(th))
            pen = QPen(QColor(120, 122, 128), 2.6)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(ax, ay), tip)
            pen = QPen(QColor(222, 224, 230), 1.4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(ax - 0.4, ay), QPointF(tip.x() - 0.4, tip.y()))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(236, 238, 242))
            p.drawEllipse(tip, 2.5, 2.5)

        # shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 34 - i * 3.6))))
            p.drawRoundedRect(r, 12 + g, 12 + g)

        # the case, with wood grain
        body = QPainterPath()
        body.addRoundedRect(rect, 12.0, 12.0)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(*c["top"]))
        grad.setColorAt(0.5, QColor(*c["mid"]))
        grad.setColorAt(1.0, QColor(*c["bot"]))
        p.setBrush(QBrush(grad))
        p.drawPath(body)
        p.save()
        p.setClipPath(body)
        p.setBrush(Qt.NoBrush)
        for k, fy in enumerate((0.1, 0.19, 0.3, 0.42, 0.55, 0.64, 0.77, 0.9)):
            p.setPen(QPen(QColor(*c["grain"], 34 if k % 2 else 22), 0.8))
            yy = rect.top() + fy * ph
            p.drawLine(QPointF(rect.left(), yy), QPointF(rect.right(), yy + 1.5 * math.sin(k * 1.9)))
        p.restore()
        hi = QLinearGradient(0, rect.top(), 0, rect.bottom())
        hi.setColorAt(0.0, QColor(255, 255, 255, 120))
        hi.setColorAt(0.35, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hi), 1.0))
        p.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), 11.0, 11.0)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 12.0, 12.0)
        p.setPen(Qt.NoPen)                                  # antenna mount
        p.setBrush(QColor(60, 60, 66))
        p.drawEllipse(QPointF(ax, ay + 0.5), 4.6, 2.6)

        # control panel
        panel_w = lerp(46.0, 62.0, big)
        scr_w = pw - panel_w - 24.0
        scr = QRectF(rect.left() + PAD, rect.top() + PAD, scr_w, ph - 2 * PAD)
        pan = QRectF(scr.right() + PAD, rect.top() + PAD, panel_w, ph - 2 * PAD)
        pg = QLinearGradient(0, pan.top(), 0, pan.bottom())
        pg.setColorAt(0.0, QColor(*c["plate"]))
        pg.setColorAt(1.0, QColor(*c["plate2"]))
        p.setPen(QPen(QColor(0, 0, 0, 70), 1.0))
        p.setBrush(QBrush(pg))
        p.drawRoundedRect(pan, 8.0, 8.0)
        pcx = pan.center().x()
        kr = lerp(6.4, 8.4, big)
        vol = -130.0 + 260.0 * clamp01(lv) if self.recording else -130.0
        self._knob(p, pcx - kr * 1.45, pan.top() + 24.0, kr, vol)
        self._knob(p, pcx + kr * 1.45, pan.top() + 24.0, kr, self.chan)
        led_on = 0.25
        led_col = (255, 60, 40)
        if self.recording:
            led_on = 0.75 + 0.25 * lv
        elif self.state == "processing":
            led_on, led_col = (1.0 if math.sin(t * 9.0) > 0 else 0.25), (255, 170, 40)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*led_col, int(255 * led_on)))
        p.drawEllipse(QPointF(pcx, pan.top() + 8.0), 2.6, 2.6)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        gg = QRadialGradient(QPointF(pcx, pan.top() + 8.0), 11.0)
        gg.setColorAt(0.0, QColor(*led_col, int(150 * led_on)))
        gg.setColorAt(1.0, QColor(*led_col, 0))
        p.setBrush(QBrush(gg))
        p.drawEllipse(QPointF(pcx, pan.top() + 8.0), 11.0, 11.0)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        slots = int((pan.height() - 46.0) / 5.2)             # speaker grille = VU meter
        for i in range(max(0, slots)):
            yy = pan.bottom() - 8.0 - (i + 1) * 5.2
            lit = clamp01(lv * (slots + 0.4) - i) if self.recording else 0.0
            col = QColor(*self.phos["main"]) if lit > 0.05 else QColor(70, 56, 42, 160)
            if lit > 0.05:
                col.setAlpha(int(255 * (0.4 + 0.6 * lit)))
            p.setBrush(col)
            p.drawRoundedRect(QRectF(pan.left() + 10.0, yy, pan.width() - 20.0, 2.2), 1.1, 1.1)

        # screen bezel + glass
        bz = scr.adjusted(-3.0, -3.0, 3.0, 3.0)
        bg = QLinearGradient(0, bz.top(), 0, bz.bottom())
        bg.setColorAt(0.0, QColor(16, 16, 18))
        bg.setColorAt(1.0, QColor(42, 42, 46))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(bz, 14.0, 14.0)
        self._screen(p, scr, t, now)

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
        for name in CASES:
            m.addAction(f"Case: {name}", lambda n=name: self.set_case(n))
        for name in PHOSPHORS:
            m.addAction(f"Phosphor: {name}", lambda n=name: self.set_phosphor(n))
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
            self.timer.start(1400)               # let the picture warm up first
        elif st == "processing":
            QTimer.singleShot(3000, lambda: self.pill.state_request.emit("idle")
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
    ap.add_argument("--case", choices=sorted(CASES), default="walnut")
    ap.add_argument("--phosphor", choices=sorted(PHOSPHORS), default="amber")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, case=args.case, phosphor=args.phosphor)
    if args.demo:
        DemoTranscriber(pill, autoplay=args.autoplay)
    pill.place_default()
    pill.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
