"""
preview_pill.py - Standalone visual test and preview for the new sleek pill UI.
"""

import tkinter as tk
import time
import math
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

class GorgeousPill:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        
        # Transparent background chroma key
        self.chroma_color = "#000001"
        self.root.wm_attributes("-transparentcolor", self.chroma_color)
        self.root.config(bg=self.chroma_color)

        self.w = 320
        self.h = 56
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - self.w) // 2
        y = screen_h - self.h - 90

        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

        # Canvas for custom anti-aliased drawing
        self.canvas = tk.Canvas(
            self.root,
            width=self.w,
            height=self.h,
            bg=self.chroma_color,
            highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.state = "listening"  # "listening" or "transcribing"
        self.level = 0.2
        self.target_level = 0.2
        self.phase = 0.0

        self._draw_pill_background()
        self._animate()

    def _draw_pill_background(self):
        """Draws the rounded capsule pill container."""
        r = self.h // 2
        bg = "#131317"
        border = "#2d2d38" if self.state == "listening" else "#4338ca"
        
        self.canvas.delete("bg")
        # Left circle, right circle, middle rectangle
        self.canvas.create_oval(2, 2, self.h - 2, self.h - 2, fill=bg, outline=border, width=2, tags="bg")
        self.canvas.create_oval(self.w - self.h + 2, 2, self.w - 2, self.h - 2, fill=bg, outline=border, width=2, tags="bg")
        self.canvas.create_rectangle(r, 2, self.w - r, self.h - 2, fill=bg, outline=bg, tags="bg")
        
        # Top & bottom border lines for the middle
        self.canvas.create_line(r, 2, self.w - r, 2, fill=border, width=2, tags="bg")
        self.canvas.create_line(r, self.h - 2, self.w - r, self.h - 2, fill=border, width=2, tags="bg")

    def _animate(self):
        self.phase += 0.25
        # Smooth spring level
        self.level += (self.target_level - self.level) * 0.35

        self.canvas.delete("dynamic")

        accent_color = "#f43f5e" if self.state == "listening" else "#38bdf8"
        halo_color = "#881337" if self.state == "listening" else "#0c4a6e"

        # 1. Pulsing glowing dot on the left
        pulse = (math.sin(self.phase * 0.8) + 1.0) * 0.5
        halo_r = 14 + int(pulse * 3)
        dot_x, dot_y = 28, self.h // 2

        self.canvas.create_oval(
            dot_x - halo_r, dot_y - halo_r, dot_x + halo_r, dot_y + halo_r,
            fill=halo_color, outline="", tags="dynamic"
        )
        self.canvas.create_oval(
            dot_x - 7, dot_y - 7, dot_x + 7, dot_y + 7,
            fill=accent_color, outline="#ffffff", width=1, tags="dynamic"
        )

        # 2. Modern Status Text
        title = "Listening..." if self.state == "listening" else "Enhancing Text..."
        self.canvas.create_text(
            52, self.h // 2,
            text=title,
            fill="#f8fafc",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
            tags="dynamic"
        )

        # 3. Dynamic Waveform Bars (6 bars)
        num_bars = 6
        bar_w = 4
        spacing = 5
        total_w = num_bars * (bar_w + spacing)
        start_x = self.w - total_w - 24
        mid_y = self.h // 2

        for i in range(num_bars):
            bx = start_x + i * (bar_w + spacing)
            if self.state == "listening":
                # Reactive bouncing bars with organic sine modulation
                wave = math.sin(self.phase + i * 0.7) * 0.35 + 0.65
                bar_h = int(max(4, min(24, (self.level * 28 + 4) * wave)))
            else:
                # Transcribing wave animation
                wave = (math.sin(self.phase * 1.5 + i * 0.9) + 1.0) * 0.5
                bar_h = int(6 + wave * 14)

            bar_color = accent_color if i % 2 == 0 else "#a855f7"
            self.canvas.create_line(
                bx, mid_y - bar_h // 2, bx, mid_y + bar_h // 2,
                fill=bar_color, width=bar_w, capstyle="round", tags="dynamic"
            )

        self.root.after(30, self._animate)

if __name__ == "__main__":
    p = GorgeousPill()
    # Test switching states
    p.root.after(2000, lambda: setattr(p, "target_level", 0.8))
    p.root.after(3500, lambda: [setattr(p, "state", "transcribing"), p._draw_pill_background()])
    p.root.after(6000, p.root.destroy)
    p.root.mainloop()
