"""
Mercury recording pill (PySide6).

    pip install PySide6 sounddevice numpy
    python mercury_pill.py                     # real microphone
    python mercury_pill.py --demo              # fake speech, no mic needed
    python mercury_pill.py --accent ice        # orange | ice | violet | mint | mono

Design
    Liquid chrome. Seven mercury droplets are rendered as true metaballs
    (their fields add up, so they stretch, bridge and fuse like real liquid),
    shaded with a studio reflection: bright sky, dark horizon, a bounce of
    accent colour from the ground, and a sharp specular glint.

    Idle       : one bead breathing, two tiny droplets orbiting it
    Recording  : beads spread across the pill. Quiet = separate beads
                 (a dotted line). As your voice rises they swell, bridge and
                 fuse into a liquid ridge, and the accent glow grows with them
    Processing : the droplets whirl in a molten loop

Same public API as the other pills (Pill, AudioSource, state_request).

Standalone controls
    click       idle -> recording -> processing -> (auto) idle
    drag        move the pill        right click   menu

Inside your own app (thread-safe, call from anywhere):
    pill.state_request.emit("recording")
    pill.state_request.emit("processing")   # while your AI polishing runs
    pill.state_request.emit("idle")         # when text has been pasted
    pill.set_accent("ice")                  # switch reflection colour live
"""
import sys
import math
import time
import argparse
from collections import deque

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPainterPath,
                           QLinearGradient, QImage, QGuiApplication)
from PySide6.QtWidgets import QApplication, QWidget, QMenu

try:
    import sounddevice as sd
except Exception:  # missing package or missing PortAudio
    sd = None

# ----------------------------------------------------------------- tuning --
SCALE = 1.0                    # overall size multiplier

IDLE_SIZE = (62.0, 30.0)       # logical pill sizes (before SCALE)
REC_SIZE = (146.0, 40.0)
PROC_SIZE = (92.0, 34.0)
MARGIN = 24                    # transparent room for the shadow

NUM_BLOBS = 7
RES = 2.0                      # render pixels per logical pixel (lower = faster)
AREA = REC_SIZE                # region the metaballs are rendered into

ATTACK, RELEASE = 34.0, 10.0   # loudness smoothing speeds (1/s)
TRAIL = 0.30                   # seconds of loudness history across the beads

ACCENTS = {
    "orange": (255, 136, 44),
    "ice": (96, 190, 255),
    "violet": (164, 112, 255),
    "mint": (70, 235, 170),
    "mono": (205, 210, 222),
}
COOL = (216, 228, 248)         # base chrome tint

THRESH = 0.30                  # metaball iso-level
KERNEL_R = 1.0 / math.sqrt(1.0 - THRESH ** (1.0 / 3.0))   # support / surface radius

# studio reflection: brightness as a function of "how far up" a surface looks
UP_STOPS = np.array([-1.0, -0.7, -0.35, -0.08, 0.05, 0.2, 0.5, 0.8, 1.0], dtype=np.float32)
LUM_STOPS = np.array([0.62, 0.70, 0.34, 0.12, 0.08, 0.34, 0.78, 0.95, 1.00], dtype=np.float32)
DOME = 1.2 / math.sqrt(1.0 - THRESH)       # dome height ~ 1.2 x bead radius


def _unit(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


HK = _unit(_unit([-0.45, -0.75, 0.50]) + [0, 0, 1])    # key light half-vector
HR = _unit(_unit([0.60, 0.65, 0.45]) + [0, 0, 1])      # rim light half-vector

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
    state_request = Signal(str)          # thread-safe way to change state

    def __init__(self, audio, auto_finish=2.5, accent="orange"):
        super().__init__()
        self.audio = audio
        self.auto_finish = auto_finish   # standalone demo: fake "enhance" time
        self.state = "idle"

        self.s = 0.0 ; self.sv = 0.0     # recording spring (0..1)
        self.pr = 0.0 ; self.prv = 0.0   # processing spring (0..1)

        self.level = 0.0                 # smoothed loudness
        self.hist = deque()              # (time, level) trail
        self.flow = 0.0                  # animation clock, speeds up with voice
        self.spin = 0.0                  # orbit angle while processing

        # droplets: columns are x, y, radius (logical px, relative to centre)
        self.bp = np.zeros((NUM_BLOBS, 3))
        self.bv = np.zeros((NUM_BLOBS, 3))

        # render grid
        self.W = int(AREA[0] * RES)
        self.H = int(AREA[1] * RES)
        xs = (np.arange(self.W, dtype=np.float32) + 0.5) / RES - AREA[0] / 2
        ys = (np.arange(self.H, dtype=np.float32) + 0.5) / RES - AREA[1] / 2
        self.gx, self.gy = np.meshgrid(xs, ys)
        self._buf = None

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
        """'orange' | 'ice' | 'violet' | 'mint' | 'mono'. Safe to call any time."""
        self.accent = name if name in ACCENTS else "orange"
        self.acc = np.array(ACCENTS[self.accent], dtype=np.float32) / 255.0

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
        t = now - self.t0

        self.s, self.sv = self._spring(
            self.s, self.sv, 1.0 if self.recording else 0.0, dt)
        self.pr, self.prv = self._spring(
            self.pr, self.prv, 1.0 if self.state == "processing" else 0.0, dt)
        sa, pr = clamp01(self.s), clamp01(self.pr)

        raw = self.audio.level() if self.recording else 0.0
        speed = ATTACK if raw > self.level else RELEASE
        self.level += (raw - self.level) * (1.0 - math.exp(-speed * dt))
        self.hist.append((now, self.level))
        while len(self.hist) > 2 and now - self.hist[0][0] > 0.9:
            self.hist.popleft()
        self.flow += dt * (0.8 + 3.0 * self.level * sa)
        self.spin += dt * (3.1 * pr)

        # --- where should every droplet be? -------------------------------
        pw, ph = self._size()
        w_idle, w_rec = (1 - sa) * (1 - pr), sa * (1 - pr)
        tgt = np.zeros((NUM_BLOBS, 3))
        mid = NUM_BLOBS // 2
        for i in range(NUM_BLOBS):
            sl = (i - mid) / mid                       # -1 .. 1 across the pill

            # idle: centre bead + two tiny orbiting droplets
            if i == mid:
                ix, iy = 2.0 * math.sin(t * 0.9), 0.0
                ir = ph * 0.27 * (1.0 + 0.05 * math.sin(t * 2.0))
            elif i in (mid - 1, mid + 1):
                ang = t * 1.1 + (0.0 if i < mid else math.pi)
                ix, iy = math.cos(ang) * pw * 0.31, math.sin(ang) * ph * 0.20
                ir = ph * 0.075
            else:
                ix, iy, ir = 0.0, 0.0, 0.0

            # recording: beads across the pill; voice inflates them
            lv = 0.55 * self.level + 0.45 * self._sample(now - abs(sl) * TRAIL)
            rx = sl * (pw / 2 - 23.0) + math.sin(self.flow * 1.7 + i * 2.1) * 2.5 * lv
            ry = ph * 0.075 * math.sin(self.flow * (1.1 + 0.31 * i) + i * 1.7) \
                * (0.25 + lv)
            rr = ph * (0.115 + 0.27 * lv) * (1.0 - 0.30 * abs(sl) ** 1.5)

            # processing: a molten loop
            if 1 <= i <= NUM_BLOBS - 2:
                ang = self.spin + (i - 1) * 2 * math.pi / (NUM_BLOBS - 2)
                px, py = math.cos(ang) * pw * 0.29, math.sin(ang) * ph * 0.27
                prr = ph * 0.20 * (1.0 + 0.12 * math.sin(self.flow * 3.0 + i))
            else:
                px, py, prr = 0.0, 0.0, 0.0

            tgt[i, 0] = w_idle * ix + w_rec * rx + pr * px
            tgt[i, 1] = w_idle * iy + w_rec * ry + pr * py
            tgt[i, 2] = w_idle * ir + w_rec * rr + pr * prr

        # springy, slightly bouncy follow -> jelly motion
        k = np.array([190.0, 190.0, 320.0])
        c = np.array([19.0, 19.0, 23.0])
        self.bv += (k * (tgt - self.bp) - c * self.bv) * dt
        self.bp += self.bv * dt
        self.bp[:, 2] = np.maximum(self.bp[:, 2], 0.0)
        self.update()

    # ---- liquid chrome renderer ----
    def _render(self):
        X, Y = self.gx, self.gy
        F = np.zeros_like(X)             # summed metaball field
        RW = np.zeros_like(X)            # field-weighted radius (for dome height)
        any_blob = False

        for bx, by, r in self.bp:
            if r < 0.4:
                continue
            any_blob = True
            R = r * KERNEL_R
            dx = X - bx
            dy = Y - by
            q = np.maximum(1.0 - (dx * dx + dy * dy) / (R * R), 0.0)
            cf = q * q * q
            F += cf
            RW += cf * r

        rgba = np.zeros((self.H, self.W, 4), dtype=np.uint8)
        if any_blob:
            # anti-aliased coverage from the signed distance to the iso-line
            gy_, gx_ = np.gradient(F)
            mag = np.hypot(gx_, gy_) + 1e-4
            alpha = np.clip((F - THRESH) / mag + 0.5, 0.0, 1.0)

            # liquid surface = a dome over the merged field -> seamless fusion
            reff = RW / np.maximum(F, 1e-4)
            dome = np.sqrt(np.maximum(F - THRESH, 0.0)) * (reff * RES * DOME)
            hy, hx = np.gradient(dome)
            ln = np.sqrt(hx * hx + hy * hy + 1.0)
            nx, ny, nz = -hx / ln, -hy / ln, 1.0 / ln

            # reflection vector (viewer looks straight down -z)
            up = -2.0 * nz * ny + 0.16 * nx          # slight sideways skew
            rz = 2.0 * nz * nz - 1.0
            sky = np.interp(up, UP_STOPS, LUM_STOPS).astype(np.float32)
            frontness = np.clip((rz - 0.55) / 0.40, 0.0, 1.0)
            frontness = frontness * frontness * (3.0 - 2.0 * frontness)
            front = np.clip(0.50 + 0.28 * up, 0.0, 1.0)
            lum = sky * (1.0 - frontness) + front * frontness

            # ground bounce picks up the accent colour
            cool = np.array(COOL, dtype=np.float32) / 255.0
            g = np.clip((-up - 0.15) * 1.35, 0.0, 1.0) * 0.70 * (1.0 - frontness)
            tint = cool[None, None, :] * (1 - g)[..., None] \
                + self.acc[None, None, :] * g[..., None]
            col = lum[..., None] * tint

            # key-light glint + faint accent rim light + fresnel edge
            spec = np.clip(nx * HK[0] + ny * HK[1] + nz * HK[2], 0, 1) ** 80
            spec2 = np.clip(nx * HR[0] + ny * HR[1] + nz * HR[2], 0, 1) ** 40
            rim = (1.0 - nz) ** 3
            col = col + 0.95 * spec[..., None] \
                + 0.40 * spec2[..., None] * self.acc[None, None, :] \
                + 0.12 * rim[..., None] * cool[None, None, :]
            col = np.clip(col, 0.0, 1.0)

            # accent halo: the field that leaks outside the surface
            halo = np.clip(F / THRESH, 0.0, 1.0) ** 2.4 * 0.30 * (1.0 - alpha)

            prem = col * alpha[..., None] + self.acc[None, None, :] * halo[..., None]
            a_tot = np.clip(alpha + halo, 0.0, 1.0)
            rgba[..., :3] = np.clip(prem * 255.0, 0, 255).astype(np.uint8)
            rgba[..., 3] = (a_tot * 255.0).astype(np.uint8)
        self._buf = np.ascontiguousarray(rgba)
        return QImage(self._buf.data, self.W, self.H, self.W * 4,
                      QImage.Format_RGBA8888_Premultiplied)

    # ---- painting ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(SCALE, SCALE)
        w, h = self.width() / SCALE, self.height() / SCALE
        cx, cy = w / 2, h / 2
        pw, ph = self._size()
        rect = QRectF(cx - pw / 2, cy - ph / 2, pw, ph)
        rr = ph / 2

        # soft layered shadow
        p.setPen(Qt.NoPen)
        for i in range(9):
            g = i * 1.8
            r = rect.adjusted(-g, -g + 4, g, g + 4)
            p.setBrush(QColor(0, 0, 0, int(max(0, 26 - i * 2.8))))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # dark steel body
        body = QPainterPath()
        body.addRoundedRect(rect, rr, rr)
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(36, 38, 46))
        grad.setColorAt(0.5, QColor(15, 16, 21))
        grad.setColorAt(1.0, QColor(6, 7, 10))
        p.setBrush(QBrush(grad))
        p.drawPath(body)

        # the liquid
        p.save()
        p.setClipPath(body)
        img = self._render()
        p.drawImage(QRectF(cx - AREA[0] / 2, cy - AREA[1] / 2, AREA[0], AREA[1]), img)
        sheen = QLinearGradient(0, rect.top(), 0, rect.center().y())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 20))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), ph / 2))
        p.restore()

        # recessed bezel + hairline
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 130), 2.0))
        p.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), rr - 1.4, rr - 1.4)
        edge = QLinearGradient(0, rect.top(), 0, rect.bottom())
        edge.setColorAt(0.0, QColor(255, 255, 255, 92))
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
        for name in ACCENTS:
            m.addAction(f"Reflection: {name}", lambda n=name: self.set_accent(n))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(e.globalPos())


# ------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake audio, no mic")
    ap.add_argument("--start", action="store_true", help="start recording")
    ap.add_argument("--accent", choices=sorted(ACCENTS), default="orange")
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
