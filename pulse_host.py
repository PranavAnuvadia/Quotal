"""
pulse_host.py - "Pulse" minimal recording pill in pure Python (no WebView, no exe).

Design language: a small flat-black capsule with a row of white voice bars in
the middle — nothing else. Bars breathe with the mic level; idle shows tiny
dots, speech shows tall bars. States: listening/transcribing (white),
done (soft green), error (soft red).

Stdin protocol (mirrors web_pill_host.py / native_orb_host.py):
    show [state]   - show the pill (listening/transcribing/done/error)
    theme [style]  - accepted for parity (only "pulse" is routed here)
    state [state]  - switch semantic state
    level [0..1]   - microphone energy
    hide           - conceal the pill
    quit           - exit the process

Plus:  --snapshot <png>   render a few headless frames, save PNG, exit
       --selftest          render headless frames, exit 0/1

Rendering: numpy gradient + Pillow vector shapes, 2x supersampled then
downscaled for perfectly anti-aliased capsule edges. Window uses chroma-key
transparency, hides from Alt+Tab/Taskbar, never steals focus, supports
drag-to-move with position persistence and double-click to reset.
"""

import sys
import os
import time
import math
import queue
import threading

import numpy as np
from PIL import Image, ImageDraw

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

CHROMA = (0, 0, 1)  # near-black chroma key
CHROMA_HEX = "#%02x%02x%02x" % CHROMA

WIN_W, WIN_H = 118, 40       # outer window incl. small padding (~60% scale)
PILL_W, PILL_H = 110, 32     # capsule body
SS = 2                       # supersample factor for AA
FPS = 30

N_BARS = 9

BAR_COLORS = {
    "listening": (242, 242, 245),      # white
    "transcribing": (242, 242, 245),   # white
    "done": (74, 222, 128),            # soft green
    "error": (248, 113, 113),          # soft red
}


def _lerp(a, b, k):
    return tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


# --------------------------------------------------------------------------
# Renderer (no display needed — fully testable headless)
# --------------------------------------------------------------------------

class PulseRenderer:
    """Renders the minimal black voice-bar capsule. Returns a PIL RGB image."""

    def __init__(self):
        self.W, self.H = WIN_W, WIN_H
        self.pw, self.ph = PILL_W * SS, PILL_H * SS
        self.px = (WIN_W * SS - self.pw) // 2
        self.py = (WIN_H * SS - self.ph) // 2
        # near-black vertical gradient (top -> bottom), precomputed
        top = np.array([21, 21, 24], dtype=np.float64)
        bot = np.array([10, 10, 12], dtype=np.float64)
        row = np.linspace(0, 1, self.ph)[:, None]
        col_grad = top[None, :] * (1 - row) + bot[None, :] * row  # (ph, 3)
        self._bg_grad = np.tile(col_grad[:, None, :], (1, self.pw, 1))  # (ph, pw, 3)
        # capsule mask, precomputed
        self._mask = Image.new("L", (self.pw, self.ph), 0)
        ImageDraw.Draw(self._mask).rounded_rectangle(
            [0, 0, self.pw - 1, self.ph - 1], radius=self.ph // 2, fill=255)

    def render(self, t, voice, state="listening", timer_text="", state_blend=1.0):
        voice = max(0.0, min(1.0, float(voice)))
        state = state if state in BAR_COLORS else "listening"
        target_col = BAR_COLORS[state]
        # fade white -> state color on done/error so the badge change feels soft
        bar_col = _lerp((242, 242, 245), target_col, max(0.0, min(1.0, state_blend)))

        S = SS
        W, H = self.W * S, self.H * S
        base = np.zeros((H, W, 3), dtype=np.uint8)
        base[:, :, 0], base[:, :, 1], base[:, :, 2] = CHROMA
        img = Image.fromarray(base, "RGB").convert("RGBA")
        px, py, pw, ph = self.px, self.py, self.pw, self.ph
        rad = ph // 2

        # ---- flat black capsule body ----
        body_img = Image.fromarray(np.array(self._bg_grad, dtype=np.uint8), "RGB").convert("RGBA")
        body_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        body_layer.paste(body_img, (px, py), self._mask)
        img = Image.alpha_composite(img, body_layer)

        d = ImageDraw.Draw(img, "RGBA")
        # thin hairline kept just inside the pill edge
        inset = S
        d.rounded_rectangle([px + inset, py + inset, px + pw - 1 - inset, py + ph - 1 - inset],
                            radius=rad - inset,
                            outline=(255, 255, 255, 20), width=max(1, S // 2 + 1))

        # ---- centered voice bars (driven by the live mic level, not a loop) ----
        # Each bar has a FIXED sensitivity (middle bars tallest, like a level
        # meter). Heights follow `voice` directly: silence -> dots, speech ->
        # tall bars. The only time-based motion is a shimmer whose amplitude
        # is proportional to voice, so at silence the pill sits still.
        midy = py + ph // 2
        midx = px + pw // 2
        # while enhancing, bars slide left to make room for the loader
        bars_cx = midx - (11 * S if state == "transcribing" else 0)
        max_half = 10 * S          # tallest bar half-height (fits the 32px pill)
        step = 6 * S               # bar pitch
        bw = 2.6 * S               # bar width
        for i in range(N_BARS):
            off = (i - (N_BARS - 1) / 2) * step
            cx = bars_cx + off
            env = 1 - abs(i - (N_BARS - 1) / 2) / ((N_BARS + 1) / 2 + 1.5)
            shimmer = 0.5 + 0.5 * math.sin(t * 9.0 - i * 0.9)
            h = 0.10 + voice * env * (0.70 + 0.50 * shimmer)
            # gentle idle breathing so dots never look frozen (only audible at ~silence)
            h += (1.0 - min(1.0, voice * 5.0)) * 0.035 * (0.5 + 0.5 * math.sin(t * 2.0 + i * 1.3))
            h = max(0.09, min(1.0, h))
            bh = max(1.4 * S, h * max_half)
            y0, y1 = midy - bh, midy + bh
            d.rounded_rectangle([cx - bw / 2, y0, cx + bw / 2, y1],
                                radius=int(bw // 2) + 1, fill=bar_col + (255,))

        # ---- enhancing loader (transcribing only): comet spinner at the right ----
        if state == "transcribing":
            sx = px + pw - 14 * S
            sy = midy
            r_out, r_in = 7.0 * S, 4.0 * S
            d.ellipse([sx - r_out, sy - r_out, sx + r_out, sy + r_out],
                      outline=(255, 255, 255, 28), width=1)
            head = t * 5.2
            for k in range(8):
                a = head - k * (math.pi / 4)
                fade = 1.0 - k / 8.0
                alpha = int(40 + 215 * fade * fade)
                x0, y0 = sx + math.cos(a) * r_in, sy + math.sin(a) * r_in
                x1, y1 = sx + math.cos(a) * r_out, sy + math.sin(a) * r_out
                d.line([(x0, y0), (x1, y1)], fill=(242, 242, 245, alpha),
                       width=max(2, int(1.5 * S)))

        # downscale to window size for AA
        small = img.convert("RGB").resize((self.W, self.H), Image.LANCZOS)
        return small


# --------------------------------------------------------------------------
# Tk overlay host (Windows)
# --------------------------------------------------------------------------

try:
    import tkinter as tk
    import ctypes
    from ctypes import wintypes
    _TK_OK = True
except Exception:
    _TK_OK = False


class PulseHost:
    def __init__(self):
        import settings_manager

        self.settings_mod = settings_manager
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-transparentcolor", CHROMA_HEX)
        self.root.wm_attributes("-topmost", True)
        self.root.config(bg=CHROMA_HEX)

        self.W, self.H = WIN_W, WIN_H
        self._place_initial()

        self.label = tk.Label(self.root, bg=CHROMA_HEX, bd=0, highlightthickness=0)
        self.label.pack(fill="both", expand=True)

        self.renderer = PulseRenderer()
        self.photo = None

        self.t = 0.0
        self.voice = 0.0
        self.target_voice = 0.08
        self.state = "listening"
        self.state_blend = 1.0
        self.visible = False
        self.cmd_q = queue.Queue()

        self.root.withdraw()
        self._apply_win_styles()
        self.root.after(33, self._tick)
        self.root.after(50, self._pump_cmds)

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
            y = max(t + 4, min(b - self.H - 4, int(cy)))
        else:
            x = (l + r - self.W) // 2
            y = b - self.H - 24
        self.root.geometry(f"{self.W}x{self.H}+{x}+{y}")

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
            pass  # only "pulse" is routed here by OverlayPill
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
                self.state_blend = 0.0

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
            if self.state != "listening":
                # mic is no longer live: let the level fall back to idle so
                # the bars settle honestly instead of dancing on their own
                self.target_voice += (0.08 - self.target_voice) * (1 - math.exp(-dt / 0.4))
            # fast attack so bars jump with speech, slow release so they fall smoothly
            tc = 0.07 if self.target_voice > self.voice else 0.30
            self.voice += (self.target_voice - self.voice) * (1 - math.exp(-dt / tc))
            self.t += dt * (1.0 + self.voice * 1.2)
            self.state_blend = min(1.0, self.state_blend + dt / 0.18)
            pulse_voice = self.voice
            try:
                frame = self.renderer.render(
                    time.perf_counter(),
                    pulse_voice, self.state, "", self.state_blend,
                )
                from PIL import ImageTk
                self.photo = ImageTk.PhotoImage(frame)
                self.label.config(image=self.photo)
            except Exception:
                pass
        self.root.after(int(1000 / FPS), self._tick)

    def run(self):
        self.root.mainloop()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--snapshot":
        r = PulseRenderer()
        cases = [("listening", 0.10), ("listening", 0.85),
                 ("transcribing", 0.55), ("done", 0.50), ("error", 0.50)]
        base, ext = os.path.splitext(sys.argv[2])
        for i, (st, lvl) in enumerate(cases):
            img = r.render(3.2 + i * 1.7, lvl, st, "", 1.0)
            path = sys.argv[2] if i == 0 else f"{base}-{i}-{st}{ext or '.png'}"
            img.save(path)
            print(f"SNAPSHOT-OK {path}")
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        try:
            r = PulseRenderer()
            a = np.asarray(r.render(1.0, 0.10, "listening", "", 1.0)).astype(int)
            b = np.asarray(r.render(2.5, 0.90, "listening", "", 1.0)).astype(int)
            assert a.shape == (WIN_H, WIN_W, 3), a.shape
            assert abs(a - b).sum() > 20000, "frozen renderer"
            assert (a.sum(axis=2) > 30).sum() > 3000, "too dark"
            print("SELFTEST-OK pulse")
            return
        except Exception as ex:
            print(f"SELFTEST-FAIL pulse: {ex}")
            sys.exit(1)
    if not _TK_OK:
        print("tkinter unavailable")
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
    PulseHost().run()


if __name__ == "__main__":
    main()
