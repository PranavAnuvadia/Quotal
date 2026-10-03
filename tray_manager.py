"""
tray_manager.py - Windows System Tray integration using pystray.
Sits in the taskbar notification area next to the clock.
"""

import os
import threading
from PIL import Image, ImageDraw
import pystray
from typing import Callable


def _get_tray_icon() -> Image.Image:
    """Load Quotal icon for system tray, with fallback."""
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotal_icon.png")
    if os.path.exists(icon_path):
        try:
            img = Image.open(icon_path).convert("RGBA")
            return img.resize((64, 64), Image.Resampling.LANCZOS)
        except Exception:
            pass

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill="#4f46e5")
    draw.rounded_rectangle((24, 14, 40, 36), radius=7, fill="#ffffff")
    draw.arc((18, 22, 46, 44), start=0, end=180, fill="#ffffff", width=4)
    draw.line((32, 44, 32, 52), fill="#ffffff", width=4)
    draw.line((24, 52, 40, 52), fill="#ffffff", width=4)
    return image


class TrayManager:
    def __init__(self, on_open_dashboard: Callable, on_quit: Callable):
        self.on_open_dashboard = on_open_dashboard
        self.on_quit = on_quit
        self.icon = None
        self._thread = None

    def start(self):
        """Start the system tray icon in a background thread."""
        image = _get_tray_icon()

        menu = pystray.Menu(
            pystray.MenuItem("Open Quotal", self._on_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Quotal", self._on_exit)
        )

        self.icon = pystray.Icon(
            "quotal",
            image,
            "Quotal (Hold Right Alt to Speak)",
            menu=menu
        )

        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def _on_show(self, icon=None, item=None):
        if self.on_open_dashboard:
            self.on_open_dashboard()

    def _on_exit(self, icon=None, item=None):
        if self.icon:
            self.icon.stop()
        if self.on_quit:
            self.on_quit()

    def stop(self):
        if self.icon:
            self.icon.stop()
