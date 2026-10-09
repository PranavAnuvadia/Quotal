"""
dashboard_host.py - Quotal Dashboard (WebView2, GPU-accelerated HTML UI).

Runs as its own lightweight process so the UI is always smooth and never
blocks dictation. Talks to the main Quotal engine over localhost:
  - Engine port 48291: "show", "model:<key>"
  - Dashboard port 48292: "show" (bring existing dashboard to front)
"""

import os
import sys
import json
import socket
import threading
import ctypes

try:
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
except Exception:
    pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quotal.Voice.App")
except Exception:
    pass

import tempfile
import webview
import webview.platforms.winforms as wf
wf.cache_dir = os.path.join(tempfile.gettempdir(), "quotal_dash_wv")
from webview.platforms.winforms import BrowserView
import System
import System.Windows.Forms as WinForms
from System.Drawing import Icon

import history_manager
import settings_manager
import win_startup
import smart_enhancer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    os.chdir(BASE_DIR)
except Exception:
    pass
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
HTML_PATH = os.path.join(BASE_DIR, "dashboard.html")
ICON_PATH = os.path.join(BASE_DIR, "quotal.ico")
ENGINE_PORT = 48291
DASH_PORT = 48292

MODELS = [
    {"key": "base", "title": "Whisper Base", "tag": "Fastest (~300ms)", "size": "75 MB", "device": "GPU",
     "desc": "Ultra-fast everyday English dictation with near-zero latency."},
    {"key": "hinglish_swift", "title": "Hinglish Swift", "tag": "Hindi + English", "size": "145 MB", "device": "GPU",
     "desc": "Tuned for Romanized Hindi & conversational Hinglish."},
    {"key": "small", "title": "Whisper Small", "tag": "High Accuracy", "size": "240 MB", "device": "GPU",
     "desc": "Superior accuracy for technical jargon, names, and noisy rooms."},
]

window = None
_main_hwnd = None


def _send_engine(msg: str):
    try:
        s = socket.create_connection(("127.0.0.1", ENGINE_PORT), timeout=1.5)
        s.sendall(msg.encode() + b"\n")
        s.close()
    except Exception:
        pass


def _query_engine(msg: str, timeout=1.0) -> str:
    """Sends a message and waits for a one-line reply. Returns '' on failure."""
    try:
        s = socket.create_connection(("127.0.0.1", ENGINE_PORT), timeout=timeout)
        s.settimeout(timeout)
        try:
            s.sendall(msg.encode() + b"\n")
            data = s.recv(64)
        finally:
            s.close()
        return data.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _history_mtime() -> float:
    try:
        return os.path.getmtime(history_manager.HISTORY_FILE)
    except Exception:
        return 0.0


class Api:
    def get_state(self):
        return {
            "history": history_manager.get_recent(limit=200),
            "mtime": _history_mtime(),
            "settings": settings_manager.load_settings(),
            "autostart": win_startup.is_autostart_enabled(),
            "models": MODELS,
            "llm_status": smart_enhancer.get_llm_status(),
        }

    def poll(self, mtime):
        m = _history_mtime()
        llm = smart_enhancer.get_llm_status()
        if m != mtime:
            return {
                "changed": True,
                "mtime": m,
                "history": history_manager.get_recent(limit=200),
                "llm_status": llm
            }
        return {"changed": False, "llm_status": llm}

    def download_llm(self):
        smart_enhancer.start_llm_download()
        return smart_enhancer.get_llm_status()

    def copy(self, text):
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:
            return False

    def clear_history(self):
        history_manager.clear_history()
        return {
            "success": True,
            "history": [],
            "mtime": _history_mtime()
        }

    def delete_entry(self, epoch=None, timestamp=None):
        ok = history_manager.delete_entry(epoch=epoch, timestamp=timestamp)
        return {
            "success": ok,
            "history": history_manager.get_recent(limit=200),
            "mtime": _history_mtime()
        }

    def delete_entries(self, epochs):
        count = history_manager.delete_entries(epochs or [])
        return {
            "success": count > 0,
            "count": count,
            "history": history_manager.get_recent(limit=200),
            "mtime": _history_mtime()
        }

    def delete_day(self, date_prefix):
        count = history_manager.delete_day(date_prefix)
        return {
            "success": count > 0,
            "count": count,
            "history": history_manager.get_recent(limit=200),
            "mtime": _history_mtime()
        }

    def set_setting(self, key, value):
        s = settings_manager.load_settings()
        s[key] = value
        settings_manager.save_settings(s)
        if key == "theme":
            update_title_bar(value)
        return True

    def set_theme(self, theme):
        return self.set_setting("theme", theme)

    def set_autostart(self, value):
        return win_startup.set_autostart(bool(value))

    def select_model(self, key):
        s = settings_manager.load_settings()
        s["model_key"] = key
        settings_manager.save_settings(s)
        _send_engine(f"model:{key}")
        return True

    def select_pill(self, key):
        # if key not in ("orb", "ember", "meter", "halo", "bloom", "nova", "pulse", "wispr", "bloomcs"):
        if key not in ("bloom", "wispr"):
            return {"ok": False, "live": False}
        s = settings_manager.load_settings()
        s["pill_style"] = key
        settings_manager.save_settings(s)
        # Only the new engine answers the handshake; an old/absent engine
        # would silently swallow the message, so report that instead.
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"pill:{key}")
        return {"ok": True, "live": live}


def _form():
    if window and window.uid in BrowserView.instances:
        return BrowserView.instances[window.uid]
    return None


def bring_to_front():
    f = _form()
    if not f:
        return

    def _do():
        if f.WindowState == WinForms.FormWindowState.Minimized:
            f.WindowState = WinForms.FormWindowState.Normal
        f.Show()
        f.TopMost = True
        f.Activate()
        f.TopMost = False

    try:
        f.Invoke(System.Action(_do))
    except Exception:
        pass


def _listen(sock):
    while True:
        try:
            conn, _ = sock.accept()
            data = conn.recv(1024)
            conn.close()
            if b"show" in data:
                bring_to_front()
        except Exception:
            break


def update_title_bar(theme: str = None):
    global _main_hwnd
    hwnd = _main_hwnd
    if not hwnd:
        f = _form()
        if f:
            try:
                hwnd = int(f.Handle.ToInt64())
            except AttributeError:
                hwnd = int(f.Handle.ToInt32())
            _main_hwnd = hwnd
    if not hwnd:
        return

    try:
        if not theme:
            s = settings_manager.load_settings()
            theme = s.get("theme", "dark")
        is_dark = (str(theme).lower() != "light")

        # 1. Immersive dark mode (attr 20 on Win11/Win10 2004+, attr 19 on older Win10)
        # 1 = dark mode, 0 = light mode
        val = ctypes.c_int(1 if is_dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                break

        # 2. Caption and text colors (Windows 11 build 22000+)
        # COLORREF format: 0x00BBGGRR
        if is_dark:
            caption_color = ctypes.c_int(0x00141617)  # #171614 (obsidian panel)
            text_color = ctypes.c_int(0x00DDE7EC)     # #ece7dd (ink)
        else:
            caption_color = ctypes.c_int(0x00E2EAED)  # #edeae2 (warm parchment panel)
            text_color = ctypes.c_int(0x00171A1C)     # #1c1a17 (charcoal ink)

        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption_color), 4)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), 4)

        # 3. Force DWM to redraw the window frame immediately
        try:
            user32 = ctypes.windll.user32
            # SWP_NOMOVE(0x2) | SWP_NOSIZE(0x1) | SWP_NOZORDER(0x4) | SWP_FRAMECHANGED(0x20) = 0x27
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass
    except Exception:
        pass


def on_start():
    global _main_hwnd
    window.events.loaded.wait(10)
    f = None
    for _ in range(100):
        f = _form()
        if f:
            break
        import time; time.sleep(0.05)
    if not f:
        return
    try:
        _main_hwnd = int(f.Handle.ToInt64())
    except AttributeError:
        _main_hwnd = int(f.Handle.ToInt32())

    s = settings_manager.load_settings()
    theme = s.get("theme", "dark")
    update_title_bar(theme)

    def _icon():
        if os.path.exists(ICON_PATH):
            f.Icon = Icon(ICON_PATH)
    try:
        f.Invoke(System.Action(_icon))
    except Exception:
        pass
    bring_to_front()


def main():
    global window
    # Single dashboard instance
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", DASH_PORT))
        sock.listen(2)
    except OSError:
        try:
            c = socket.create_connection(("127.0.0.1", DASH_PORT), timeout=1.5)
            c.sendall(b"show\n")
            c.close()
        except Exception:
            pass
        return
    threading.Thread(target=_listen, args=(sock,), daemon=True).start()

    s = settings_manager.load_settings()
    is_dark = (s.get("theme", "dark") != "light")
    bg_color = "#131210" if is_dark else "#edeae2"

    window = webview.create_window(
        "Quotal",
        url=HTML_PATH,
        js_api=Api(),
        width=1180,
        height=780,
        min_size=(900, 600),
        background_color=bg_color,
        text_select=False,
    )
    webview.start(on_start, gui="edgechromium")


if __name__ == "__main__":
    main()
