"""
Orbit recording pill (PySide6) - lightweight: pure vector drawing, no
per-pixel work. A few dozen cheap shapes per frame.

    pip install PySide6 sounddevice numpy
    python orbit_pill.py                       # real microphone
    python orbit_pill.py --demo                # fake speech, no mic needed
    python orbit_pill.py --accent solar        # ion | solar | plasma | mint | mono

Design
    A tiny orbital system in a dark glass capsule: a glowing core, eight
    satellites on three tilted orbits, comet tails that follow each orbit, and
    depth: satellites pass in front of and behind the core.

    Idle       : core breathing, one satellite on a small orbit
    Recording  : the orbits breathe outward with your voice (inner ring first,
                 outer rings a beat later), satellites swell and speed up,
                 and the core flares
    Processing : every satellite locks onto one fast ring and spins

Same public API as the other pills (Pill, AudioSource, state_request).

Standalone controls
    click       idle -> recording -> processing -> (auto) idle
    drag        move the pill        right click   menu

Inside your own app (thread-safe, call from anywhere):
    pill.state_request.emit("recording")
    pill.state_request.emit("processing")   # while your AI polishing runs
    pill.state_request.emit("idle")         # when text has been pasted
    pill.set_accent("solar")                # switch colour live
"""
import sys
import math
import time
import argparse
import random
from collections import deque

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPainterPath,
                           QLinearGradient, QRadialGradient, QPixmap,
                           QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (62.0, 30.0)       # logical pill sizes (before SCALE)
REC_SIZE = (150.0, 40.0)
PROC_SIZE = (96.0, 34.0)
MARGIN = 24                    # transparent room for the shadow

# three orbits (rx, ry at full volume) and the satellites riding them
ORBITS = [(22.0, 8.5), (40.0, 12.5), (58.0, 14.5)]
ORBIT_SPEED = [3.6, 2.3, 1.6]          # rad/s at rest
ORBIT_DIR = [1, -1, 1]
SATS = [  # (orbit, phase)
    (0, 0.0), (0, math.pi),
    (1, 0.4), (1, 2.5), (1, 4.6),
    (2, 1.1), (2, 3.2), (2, 5.3),
]
IDLE_ORBIT = (14.0, 6.5)
RING = (30.0, 11.5)            # processing ring
TRAIL_SEGS = 11

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
ORBIT_DELAY = 0.14             # seconds between orbits reacting to your voice

ACCENTS = {
    "ion":    dict(core=(228, 246, 255), glow=(70, 190, 255),  sat=(170, 232, 255)),
    "solar":  dict(core=(255, 240, 214), glow=(255, 138, 44),  sat=(255, 198, 130)),
    "plasma": dict(core=(246, 232, 255), glow=(168, 110, 255), sat=(214, 178, 255)),
    "mint":   dict(core=(226, 255, 242), glow=(60, 235, 165),  sat=(160, 255, 215)),
    "mono":   dict(core=(255, 255, 255), glow=(200, 206, 220), sat=(235, 238, 245)),
}

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
    state_request = Signal(str)          # thread-safe way to change state

    def __init__(self, audio, auto_finish=2.5, accent="ion"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # standalone demo: fake "enhance" time
        self.state = "idle"

        self.s = 0.0 ; self.sv = 0.0     # recording spring (0..1)
        self.pr = 0.0 ; self.prv = 0.0   # processing spring (0..1)

        self.level = 0.0                 # smoothed loudness
        self.hist = deque()              # (time, level) trail

        n = len(ORBITS)
        self.oang = [0.0] * n            # orbit angles
        self.orx = [IDLE_ORBIT[0]] + [0.0] * (n - 1)   # smoothed radii
        self.ory = [IDLE_ORBIT[1]] + [0.0] * (n - 1)
        self.olv = [0.0] * n             # per-orbit loudness
        self.spin = 0.0                  # ring angle while processing

        rnd = random.Random(7)           # a few faint stars for depth
        self.stars = [(rnd.uniform(-70, 70), rnd.uniform(-16, 16),
                       rnd.uniform(0, 6.28), rnd.uniform(0.6, 1.8))
                      for _ in range(16)]

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

        self.set_accent(accent)
        self.state_request.connect(self.set_state)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(8)

        self._press = self._origin = None
        self._moved = False

    # ---- accent ----
    def set_accent(self, name):
        """'ion' | 'solar' | 'plasma' | 'mint' | 'mono'. Safe to call any time."""
        self.accent = name if name in ACCENTS else "ion"
        a = ACCENTS[self.accent]
        self.c_core, self.c_glow, self.c_sat = a["core"], a["glow"], a["sat"]
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

    def _size(self):
        pw = lerp(lerp(IDLE_SIZE[0], REC_SIZE[0], self.s), PROC_SIZE[0], self.pr)
        ph = lerp(lerp(IDLE_SIZE[1], REC_SIZE[1], self.s), PROC_SIZE[1], self.pr)
        return pw, ph

    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 1 / 30)
        self.last = now

        self.s, self.sv = self._spring(
            self.s, self.sv, 1.0 if self.recording else 0.0, dt)
        self.pr, self.prv = self._spring(
            self.pr, self.prv, 1.0 if self.state == "processing" else 0.0, dt)
        sa = clamp01(self.s)

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.9:
            self.hist.popleft()

        follow = 1.0 - math.exp(-14.0 * dt)
        for o, (bx, by) in enumerate(ORBITS):
            lv = 0.55 * self.level + 0.45 * self._sample(now - o * ORBIT_DELAY)
            self.olv[o] = lv
            sc = 0.38 + 0.62 * lv                       # orbits breathe with voice
            rec_rx, rec_ry = bx * sc, by * (0.45 + 0.55 * sc)
            idle_rx, idle_ry = IDLE_ORBIT if o == 0 else (0.0, 0.0)
            self.orx[o] += (lerp(idle_rx, rec_rx, sa) - self.orx[o]) * follow
            self.ory[o] += (lerp(idle_ry, rec_ry, sa) - self.ory[o]) * follow
            self.oang[o] += dt * ORBIT_DIR[o] * ORBIT_SPEED[o] * \
                (0.55 + 2.2 * self.level * sa)
        self.spin += dt * 4.6 * clamp01(self.pr)
        self.update()

    # ---- painting ----
    def _glow_at(self, p, x, y, radius, opacity):
        if opacity <= 0.01:
            return
        p.setOpacity(min(1.0, opacity))
        p.drawPixmap(QRectF(x - radius, y - radius, radius * 2, radius * 2),
                     self.glow, QRectF(0, 0, self.glow_size * 2, self.glow_size * 2))
        p.setOpacity(1.0)

    def _sat(self, j, cx, cy, sa, pr, t):
        """Everything needed to draw satellite j: trail points, depth, size."""
        o, phase = SATS[j]
        lv = self.olv[o]
        d = ORBIT_DIR[o]

        thf = self.oang[o] + phase
        rxf, ryf = self.orx[o], self.ory[o]
        dsf = 0.075 + 0.05 * lv
        n_ring = len(SATS)
        thp = self.spin + j * 2 * math.pi / n_ring
        rxp = RING[0] * (1.0 + 0.04 * math.sin(t * 5.0))
        ryp = RING[1] * (1.0 + 0.04 * math.sin(t * 5.0))
        dsp = 0.13

        pts = []
        for k in range(TRAIL_SEGS + 1):
            af = thf - d * k * dsf
            ap = thp - k * dsp
            pts.append((lerp(cx + rxf * math.cos(af), cx + rxp * math.cos(ap), pr),
                        lerp(cy + ryf * math.sin(af), cy + ryp * math.sin(ap), pr)))
        dz = lerp(math.sin(thf), math.sin(thp), pr)

        vis_free = 1.0 if j == 0 else 0.0
        vis = lerp(lerp(vis_free, 1.0, sa), 1.0, pr)
        size = lerp(1.35 + 1.4 * lv * sa, 1.9, pr)
        return pts, dz, vis, size, lv

    def _draw_sat(self, p, sat, pr):
        pts, dz, vis, size, lv = sat
        if vis < 0.02:
            return
        depth_a = 0.70 + 0.30 * dz
        depth_s = 0.84 + 0.26 * dz

        # comet tail: fades and thins along the orbit
        pen = QPen()
        pen.setCapStyle(Qt.RoundCap)
        for k in range(TRAIL_SEGS):
            f = k / TRAIL_SEGS
            a = (1.0 - f) ** 1.7 * vis * depth_a * (0.55 + 0.45 * lv + 0.35 * pr)
            if a < 0.02:
                continue
            col = mix(self.c_sat, self.c_glow, f)
            pen.setColor(QColor(col[0], col[1], col[2], int(255 * min(1.0, a))))
            pen.setWidthF(max(0.35, size * 1.5 * depth_s * (1.0 - f * 0.85)))
            p.setPen(pen)
            p.drawLine(QPointF(*pts[k]), QPointF(*pts[k + 1]))

        # head: soft glow + bright core
        x, y = pts[0]
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        self._glow_at(p, x, y, (5.5 + 5.0 * lv) * depth_s, vis * depth_a * 0.9)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(Qt.NoPen)
        r_, g_, b_ = self.c_core
        p.setBrush(QColor(r_, g_, b_, int(255 * min(1.0, vis * (0.8 + 0.2 * dz)))))
        p.drawEllipse(QPointF(x, y), size * depth_s * 0.9, size * depth_s * 0.9)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        cx, cy = w / 2, h / 2
        now = time.perf_counter()
        t = now - self.t0
        sa, pr = clamp01(self.s), clamp01(self.pr)
        pw, ph = self._size()
        rect = QRectF(cx - pw / 2, cy - ph / 2, pw, ph)
        rr = ph / 2
        gr, gg, gb = self.c_glow

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 26 - i * 2.8))))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # deep-space glass body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(24, 28, 40))
        grad.setColorAt(0.5, QColor(11, 13, 21))
        grad.setColorAt(1.0, QColor(5, 6, 11))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        p.save()
        p.setClipPath(body)

        # faint twinkling stars
        p.setPen(Qt.NoPen)
        for sx, sy, ph0, sz in self.stars:
            a = 0.10 + 0.14 * (0.5 + 0.5 * math.sin(t * 1.3 + ph0))
            p.setBrush(QColor(220, 232, 255, int(255 * a)))
            p.drawEllipse(QPointF(cx + sx, cy + sy), sz * 0.35, sz * 0.35)

        # orbit guides: thin schematic ellipses
        guide = QPen(QColor(gr, gg, gb, 0), 0.7)
        p.setBrush(Qt.NoBrush)
        for o in range(len(ORBITS)):
            rx, ry = self.orx[o], self.ory[o]
            if rx > 2.0:
                guide.setColor(QColor(gr, gg, gb, int(46 * (1 - pr))))
                p.setPen(guide)
                p.drawEllipse(QPointF(cx, cy), rx, ry)
        if pr > 0.02:
            guide.setColor(QColor(gr, gg, gb, int(80 * pr)))
            p.setPen(guide)
            p.drawEllipse(QPointF(cx, cy), RING[0], RING[1])

        # satellites behind the core, then the core, then the ones in front
        sats = [self._sat(j, cx, cy, sa, pr, t) for j in range(len(SATS))]
        for s in sats:
            if s[1] < 0:
                self._draw_sat(p, s, pr)

        beat = 0.5 + 0.5 * math.sin(t * 5.2)
        core_r = (2.3 + 0.25 * math.sin(t * 2.2)) * (1 - sa) * (1 - pr) \
            + (2.6 + 3.4 * self.level) * sa * (1 - pr) \
            + (3.0 + 0.9 * beat) * pr
        glow_r = (13.0 + 22.0 * self.level * sa + 8.0 * beat * pr)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        self._glow_at(p, cx, cy, glow_r, 0.55 + 0.45 * max(self.level * sa, pr))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(Qt.NoPen)
        r_, g_, b_ = self.c_core
        p.setBrush(QColor(r_, g_, b_, 255))
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)

        for s in sats:
            if s[1] >= 0:
                self._draw_sat(p, s, pr)

        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # hairline edge, tinted by the glow while active
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 88))
        edge.setColorAt(0.5, QColor(255, 255, 255, 28))
        edge.setColorAt(1.0, QColor(255, 255, 255, 16))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(edge), 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rr, rr)
        act = max(sa * (0.3 + 0.7 * self.level), pr * 0.5)
        if act > 0.02:
            p.setPen(QPen(QColor(gr, gg, gb, int(110 * act)), 1.0))
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
            m.addAction(f"Colour: {name}", lambda n=name: self.set_accent(n))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake audio, no mic")
    ap.add_argument("--start", action="store_true", help="start recording")
    ap.add_argument("--accent", choices=sorted(ACCENTS), default="ion")
    ap.add_argument("--process-seconds", type=float, default=2.5,
                    help="how long the fake 'enhancing' state lasts (0 = stay)")
    args, qt_args = ap.parse_known_args()

    app = QApplication([sys.argv[0]] + qt_args)
    audio = AudioSource(demo=args.demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, auto_finish=args.process_seconds, accent=args.accent)
    pill.place_default()
    pill.show()
    if args.start:
        pill.set_state("recording")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
