"""
native_orb_host.py - Pure-Python voice-reactive orb overlay (no WebView).

This is the "Nova" visual: a recreation of the Bloom/WebGL orb spirit using only
numpy + Pillow + tkinter (all already Quotal dependencies / stdlib). It runs as a
dedicated subprocess, mirroring the web_pill_host.py stdin protocol:

    show [state]   - show the orb (state: listening/transcribing/done/error)
    theme [style]  - ignored unless style == "nova" (kept for protocol parity)
    state [state]  - switch semantic state
    level [0..1]   - microphone energy
    hide           - conceal the orb
    quit           - exit the process

Rendering: a fixed-seed procedural orb (deep-teal body, cyan/amber nebula bands,
twinkling stars, rim light, voice-driven brightness/pulse) rasterized with
vectorized numpy at ~30fps, supersampled for a smooth circular edge. The window
uses chroma-key transparency, lives outside Alt+Tab/Taskbar, never steals focus,
supports drag-to-move with position persistence, and double-click resets position.
"""

import sys
import os
import time
import math
import queue
import threading

import numpy as np
from PIL import Image

# --------------------------------------------------------------------------
# Renderer (numpy + Pillow only — importable/testable without a display)
# --------------------------------------------------------------------------

CHROMA = (0, 0, 1)  # near-black chroma key, same trick as preview_pill.py

# Baked palette — strictly teal/cyan/ice on deep sea-teal (no rainbow).
_C_BASE = np.array([7.0, 38.0, 43.0]) / 255.0
_C_TEAL = np.array([0.0, 194.0, 168.0]) / 255.0
_C_CYAN = np.array([56.0, 225.0, 255.0]) / 255.0
_C_ICE = np.array([214.0, 242.0, 255.0]) / 255.0
_C_DUST = np.array([0.62, 0.74, 0.86])
_C_WHITE = np.array([1.0, 0.97, 0.9])
_C_EDGE = np.array([2.0, 6.0, 9.0]) / 255.0  # near-chroma limb fade

_STATE_TINTS = {
    "listening": None,
    "transcribing": np.array([0.55, 0.85, 1.0]),  # pale cyan, stays in family
    "done": np.array([0.85, 0.95, 1.0]),          # white flash, not green
    "error": np.array([1.0, 0.30, 0.34]),
}


class NovaRenderer:
    """Procedural voice-reactive orb. render() returns a PIL RGB image."""

    def __init__(self, n=152, seed=7):
        self.n = n
        y, x = np.mgrid[0:n, 0:n].astype(np.float64)
        self.xx = (x / (n - 1)) * 2.0 - 1.0
        self.yy = (y / (n - 1)) * 2.0 - 1.0
        self.r = np.sqrt(self.xx ** 2 + self.yy ** 2)
        self.disc = self.r <= 1.0
        rc = np.minimum(self.r, 0.9995)
        depth = np.sqrt(np.maximum(0.0, 1.0 - rc ** 2))
        self.rim = (1.0 - depth) ** 2.4
        # Limb fade: melt the last ring into near-chroma so the downscaled
        # circle edge is perfectly smooth (no stair-step pixels).
        limb = np.clip((self.r - 0.90) / 0.10, 0.0, 1.0)
        self.limb = limb * limb * (3.0 - 2.0 * limb)

        # Stars in polar coords so they can orbit + breathe with the voice.
        rng = np.random.default_rng(seed + 9)
        pts = rng.random((170, 2)) * 2.0 - 1.0
        pr = np.sqrt((pts ** 2).sum(axis=1))
        pts = pts[pr <= 0.90]
        self.star_r = pr[pr <= 0.90]
        self.star_a = np.arctan2(pts[:, 1], pts[:, 0])
        self.star_phase = rng.random(len(self.star_a)) * 6.2832
        self.star_speed = 1.0 + rng.random(len(self.star_a)) * 3.0
        self.star_size = 0.35 + rng.random(len(self.star_a)) ** 2 * 1.4

    def render(self, t, voice, tint=None, tint_strength=0.0):
        """t: seconds (warped clock), voice: 0..1 smoothed energy."""
        n = self.n
        xx, yy = self.xx, self.yy

        # Evolving organic clouds from drifting sine octaves — analytic, so
        # there are no grid/block artifacts and motion is perfectly continuous
        # (no integer stepping like rolled noise fields).
        w1 = np.sin(2.3 * xx + 1.1 * yy + t * 0.30 + 1.4 * np.sin(1.1 * yy - t * 0.20 + 0.7))
        w2 = np.sin(1.2 * xx - 1.9 * yy - t * 0.24 + 1.2 * np.sin(2.2 * xx + t * 0.16))
        w3 = np.sin(3.1 * xx + 2.7 * yy + t * 0.18 + 1.0 * np.sin(1.7 * yy + 2.0 * xx - t * 0.28))
        c1 = 0.5 + 0.5 * w1
        c2 = 0.5 + 0.5 * w2
        c3 = 0.5 + 0.5 * w3

        # Galactic band + clouds
        plane = yy + 0.30 * np.sin(3.0 * xx + t * 0.45) + 0.45 * w2
        band = np.exp(-plane ** 2 * 7.0)
        cloud = (0.45 + 0.55 * c1) * (0.45 + 0.55 * c2)
        galaxy = np.clip(band * cloud, 0.0, 1.0)

        # Dust color ramp: slate -> teal -> ice cyan (strictly cool family).
        w = np.clip(c1 * 1.5 - 0.12, 0.0, 1.0)[..., None]
        dust = _C_DUST * (1 - w) + (_C_TEAL * 0.45 + _C_CYAN * 0.55) * w
        w2m = np.clip((c3 - 0.62) * 2.4, 0.0, 1.0)[..., None]
        dust = dust * (1 - w2m) + _C_ICE * w2m * 0.45

        col = np.zeros((n, n, 3), dtype=np.float64)
        col += dust * galaxy[..., None] * 0.70

        # Faint inner glow, slowly orbiting — a whisper, never a lamp.
        ang = t * 0.5
        cx, cy = 0.30 * math.cos(ang), 0.26 * math.sin(ang * 0.8)
        core = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) * 6.0)
        col += (_C_ICE * 0.20 + _C_CYAN * 0.12) * core[..., None]

        # Living stars: slow orbit + voice-driven swirl, radius breathing,
        # twinkle that brightens as you speak.
        swirl = 0.12 + 1.1 * voice
        a2 = self.star_a + t * swirl
        rr = self.star_r * (1.0 + 0.07 * voice * np.sin(t * 2.5 + self.star_phase))
        half = (n - 1) * 0.5
        sxs = np.clip((half + np.cos(a2) * rr * half).astype(np.int32), 0, n - 1)
        sys = np.clip((half + np.sin(a2) * rr * half).astype(np.int32), 0, n - 1)
        tw = 0.5 + 0.5 * np.sin(t * self.star_speed + self.star_phase)
        energy = 0.30 + 0.70 * voice
        vals = self.star_size * (0.20 + 0.80 * tw) * (0.45 + 0.55 * energy)
        star_layer = np.zeros((n, n), dtype=np.float64)
        np.add.at(star_layer, (sys, sxs), vals)
        col += (_C_ICE * 0.75 + _C_CYAN * 0.25) * star_layer[..., None] * 0.65

        # Aurora arc near the bottom limb, voice-excited (teal/cyan only).
        aur_lon = np.arctan2(xx, yy)
        wave = (0.5 + 0.5 * np.sin(aur_lon * 3.0 + t * 0.9)) ** 3
        hang = np.clip((yy + 0.15) * 1.6, 0.0, 1.0)
        aur = wave * hang * (0.22 + 0.85 * voice)
        aur_col = _C_TEAL * 0.45 + _C_CYAN * 0.55
        col += aur_col * aur[..., None] * 0.55

        # Sphere shading: dark body + soft moving key light + rim
        void = _C_BASE * (0.08 + 0.42 * self.rim)[..., None]
        body = np.ones_like(col) * _C_BASE * 0.035
        body = body * (1 - galaxy[..., None] * 0.7) + void * 0.55
        col = body + col * 0.9
        lx, ly = math.sin(t * 0.5) * 0.8, 0.45 * math.sin(t * 0.33 + 1.2)
        diff = np.clip(1.0 - np.sqrt((xx - lx * 0.4) ** 2 + (yy - ly * 0.4) ** 2) * 0.9, 0.0, 1.0)
        col *= (0.45 + 0.60 * diff)[..., None] * (1.0 + 0.40 * voice)

        # Voice heat: faint center lift + rim energy (never a halo).
        heat = np.exp(-(self.r ** 2) * 8.0) * voice
        col += _C_ICE * heat[..., None] * 0.20
        col += (_C_CYAN * 0.6 + 0.08) * (self.rim * voice)[..., None] * 0.6

        # Semantic state tint (gentle wash, never a repaint).
        if tint is not None and tint_strength > 0.001:
            k = min(1.0, tint_strength)
            col = col * (1 - 0.22 * k) + tint * k * (0.12 + 0.45 * self.rim)[..., None] * 0.9

        # Glass edge ring (subtle) + limb fade into near-chroma for a
        # perfectly smooth circular silhouette.
        edge = np.clip((self.r - 0.86) / 0.14, 0.0, 1.0) * self.disc
        col += _C_ICE * (edge * 0.16)[..., None]

        col = np.clip(col, 0.0, 1.0)
        fade = self.limb[..., None]
        col = col * (1.0 - fade) + _C_EDGE * fade

        img = np.zeros((n, n, 3), dtype=np.uint8)
        img[..., 0], img[..., 1], img[..., 2] = CHROMA
        img[self.disc] = (col[self.disc] * 255.0).astype(np.uint8)
        return Image.fromarray(img, "RGB")


# --------------------------------------------------------------------------
# Tk overlay host (Windows)
# --------------------------------------------------------------------------

WIN = 76          # native window, px (matches the small Bloom sizing)
FPS = 30

try:
    import tkinter as tk
    import ctypes
    from ctypes import wintypes
    _TK_OK = True
except Exception:
    _TK_OK = False


class NovaHost:
    def __init__(self):
        import settings_manager

        self.settings_mod = settings_manager
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.chroma = "#%02x%02x%02x" % CHROMA
        self.root.wm_attributes("-transparentcolor", self.chroma)
        self.root.wm_attributes("-topmost", True)
        self.root.config(bg=self.chroma)

        self.W = WIN
        self._place_initial()

        self.label = tk.Label(self.root, bg=self.chroma, bd=0, highlightthickness=0)
        self.label.pack(fill="both", expand=True)

        self.renderer = NovaRenderer(n=152)
        self.photo = None

        # Voice / state smoothing (mirrors Orbloom motion + 180ms state blend)
        self.t = 0.0
        self.voice = 0.0
        self.target_voice = 0.08
        self.state = "listening"
        self.tint_strength = 0.0
        self.visible = False
        self.cmd_q = queue.Queue()

        self.root.withdraw()
        self._apply_win_styles()
        self.root.after(33, self._tick)
        self.root.after(50, self._pump_cmds)

        # Drag to move + double-click to reset
        self._drag = None
        self.label.bind("<ButtonPress-1>", self._on_press)
        self.label.bind("<B1-Motion>", self._on_drag)
        self.label.bind("<ButtonRelease-1>", self._on_release)
        self.label.bind("<Double-Button-1>", lambda e: self._reset_position())

        threading.Thread(target=self._stdin_loop, daemon=True).start()

    # -- placement ------------------------------------------------------
    def _work_area(self):
        try:
            r = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
            return r.left, r.top, r.right, r.bottom
        except Exception:
            return 0, 0, 1920, 1040

    def _place_initial(self):
        l, t, r, b = self._work_area()
        try:
            s = self.settings_mod.load_settings()
            cx, cy = s.get("pill_x"), s.get("pill_y")
        except Exception:
            cx, cy = None, None
        if cx is not None and cy is not None:
            x = max(l + 4, min(r - self.W - 4, int(cx)))
            y = max(t + 4, min(b - self.W - 4, int(cy)))
        else:
            x = (l + r - self.W) // 2
            y = b - self.W - 24
        self.root.geometry(f"{self.W}x{self.W}+{x}+{y}")

    def _apply_win_styles(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            GWL_EX = -20
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EX)
            ex = (ex & ~0x00040000) | 0x00000080 | 0x08000000 | 0x00000008
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EX, ex)
        except Exception:
            pass

    def _show_noactivate(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            ctypes.windll.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    # -- mouse ----------------------------------------------------------
    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _on_release(self, e):
        self._drag = None
        try:
            s = self.settings_mod.load_settings()
            s["pill_x"] = self.root.winfo_x()
            s["pill_y"] = self.root.winfo_y()
            self.settings_mod.save_settings(s)
        except Exception:
            pass

    def _reset_position(self):
        try:
            s = self.settings_mod.load_settings()
            s["pill_x"] = None
            s["pill_y"] = None
            self.settings_mod.save_settings(s)
        except Exception:
            pass
        self._place_initial()

    # -- command loop ---------------------------------------------------
    def _stdin_loop(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            self.cmd_q.put((parts[0], parts[1] if len(parts) > 1 else ""))

    def _pump_cmds(self):
        try:
            while True:
                cmd, arg = self.cmd_q.get_nowait()
                self._handle(cmd, arg)
        except queue.Empty:
            pass
        self.root.after(50, self._pump_cmds)

    def _handle(self, cmd, arg):
        if cmd == "show":
            self.set_state(arg or "listening")
            self._show()
        elif cmd == "state":
            self.set_state(arg or "listening")
        elif cmd == "level":
            try:
                self.target_voice = max(0.0, min(1.0, float(arg)))
            except ValueError:
                pass
        elif cmd == "theme":
            pass  # only "nova" is routed here by OverlayPill
        elif cmd == "hide":
            self.visible = False
            self.root.withdraw()
        elif cmd == "quit":
            try:
                self.root.destroy()
            finally:
                os._exit(0)

    def set_state(self, s):
        if s in ("listening", "transcribing", "done", "error"):
            if s != self.state:
                self.state = s
                self.tint_strength = 0.0
            if s == "transcribing":
                self.target_voice = max(self.target_voice, 0.35)

    def _show(self):
        self._place_initial()
        self.visible = True
        self.root.deiconify()
        self.root.lift()
        self._show_noactivate()

    # -- frame loop -----------------------------------------------------
    def _tick(self):
        dt = 1.0 / FPS
        if self.visible:
            # Voice smoothing: fast attack, slow release (Orbloom-style)
            tc = 0.11 if self.target_voice > self.voice else 0.30
            self.voice += (self.target_voice - self.voice) * (1 - math.exp(-dt / tc))
            self.t += dt * (1.0 + self.voice * 1.4)
            # State tint blend (~180ms)
            target = 0.0 if self.state == "listening" else 1.0
            self.tint_strength += (target - self.tint_strength) * (1 - math.exp(-dt / 0.18))
            pulse = 1.0
            if self.state == "transcribing":
                pulse = 0.75 + 0.25 * math.sin(self.t * 5.0)
            try:
                frame = self.renderer.render(
                    self.t,
                    self.voice * pulse,
                    _STATE_TINTS[self.state],
                    self.tint_strength,
                ).resize((self.W, self.W), Image.LANCZOS)
                from PIL import ImageTk
                self.photo = ImageTk.PhotoImage(frame)
                self.label.config(image=self.photo)
            except Exception:
                pass
        self.root.after(int(1000 / FPS), self._tick)

    def run(self):
        self.root.mainloop()


def main():
    if not _TK_OK:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quotal.Voice.App")
    except Exception:
        pass
    NovaHost().run()


if __name__ == "__main__":
    main()
