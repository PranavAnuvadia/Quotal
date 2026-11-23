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
import model_manager
import gpu_pack
import updater
import languages

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
    {
        "key": "nemotron", "title": "Nemotron Streaming 3.5", "tag": "Live Preview (~50ms)", "size": "716 MB", "device": "GPU",
        "desc": "NVIDIA 0.6B streaming ASR on Vulkan GPU. Instant real-time words as you speak with zero lag.",
        "streaming": True, "translates": False, "speed_bars": 4, "speed_text": "Instant (~50ms)",
        "languages": {"en": "A", "hi": "B-", "es": "A-", "fr": "A-", "de": "A-", "it": "B+", "pt": "B+", "ru": "B"},
    },
    {
        "key": "parakeet_v3", "title": "NVIDIA Parakeet v3", "tag": "Ultra Accurate (4.8% WER)", "size": "705 MB", "device": "CPU",
        "desc": "NVIDIA Parakeet TDT 0.6B v3 offline ASR on CPU AVX2. SOTA English accuracy with under 1s latency.",
        "streaming": False, "translates": False, "speed_bars": 3, "speed_text": "Fast (~800ms)",
        "languages": {"en": "A+", "es": "B+", "fr": "B", "de": "B", "it": "B"},
    },
    {
        "key": "base", "title": "Whisper Base", "tag": "Fastest (~300ms)", "size": "75 MB", "device": "GPU",
        "desc": "Ultra-fast everyday English & multilingual dictation with near-zero latency.",
        "streaming": False, "translates": True, "speed_bars": 4, "speed_text": "Ultra-fast (~300ms)",
        "languages": {"en": "A-", "hi": "C", "es": "B+", "fr": "B+", "de": "B+", "it": "B", "pt": "B", "ru": "B", "zh": "C", "ja": "C"},
    },
    {
        "key": "hinglish_swift", "title": "Hinglish Swift", "tag": "Hindi + English", "size": "145 MB", "device": "GPU",
        "desc": "Tuned for Romanized Hindi & conversational Hinglish ('kya haal hai bro').",
        "streaming": False, "translates": False, "speed_bars": 4, "speed_text": "Ultra-fast (~250ms)",
        "languages": {"en": "A-", "hi": "A", "hinglish": "A+"},
    },
    {
        "key": "hima", "title": "Hima ASR", "tag": "12 Indian Langs", "size": "550 MB", "device": "CPU",
        "desc": "Indian multilingual ASR with decision head. Native Hindi, Gujarati, Tamil, Telugu, and English with code-switching.",
        "streaming": False, "translates": False, "speed_bars": 4, "speed_text": "Fast (~220ms)",
        "languages": {"hi": "A+", "en": "A-", "gu": "A+", "ta": "A+", "te": "A+", "mr": "A", "bn": "A", "kn": "A", "ml": "A", "pa": "A", "or": "A", "ur": "A"},
    },
    {
        "key": "turbo", "title": "Whisper Large Turbo", "tag": "Best Multilingual", "size": "800 MB", "device": "GPU",
        "desc": "Flagship 99+ language accuracy with high speed. Best for Marathi, Hindi, and global languages.",
        "streaming": False, "translates": True, "speed_bars": 3, "speed_text": "Fast (~500ms)",
        "languages": {"en": "A+", "hi": "A", "mr": "A", "es": "A+", "fr": "A+", "de": "A+", "it": "A+", "pt": "A+", "ru": "A+", "zh": "A", "ja": "A", "ko": "A", "ar": "A"},
    },
    {
        "key": "small", "title": "Whisper Small", "tag": "High Accuracy", "size": "240 MB", "device": "GPU",
        "desc": "Superior accuracy for technical jargon, names, and noisy rooms.",
        "streaming": False, "translates": True, "speed_bars": 3, "speed_text": "Moderate (~600ms)",
        "languages": {"en": "A", "hi": "B+", "es": "A-", "fr": "A-", "de": "A-", "it": "A-", "pt": "A-", "ru": "A-", "zh": "B+", "ja": "B+"},
    },
    {
        "key": "omnilingual", "title": "Omnilingual ASR", "tag": "1,600+ Languages", "size": "328 MB", "device": "CPU",
        "desc": "Meta AI 1,600+ languages via sherpa-onnx. Instant CPU latency (~150ms) with zero translation traps.",
        "streaming": False, "translates": False, "speed_bars": 4, "speed_text": "Instant (~150ms)",
        "languages": {"en": "B+", "hi": "A-", "mr": "B+", "gu": "B+", "ta": "B+", "te": "B+", "es": "B+", "fr": "B+", "de": "B+"},
    },
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
    def _models_with_status(self):
        status = {m["key"]: m for m in model_manager.get_models_state()}
        cur_device_setting = settings_manager.load_settings().get("device", "auto")
        is_cuda = False
        if cur_device_setting != "cpu":
            try:
                from transcriber import cuda_libs_present
                import ctranslate2
                is_cuda = cuda_libs_present() and (ctranslate2.get_cuda_device_count() > 0)
            except Exception:
                is_cuda = False
        device_label = "GPU" if is_cuda else "CPU"

        out = []
        for m in MODELS:
            s = status.get(m["key"], {})
            out.append({
                **m,
                "device": "CPU" if m["key"] in ("omnilingual", "parakeet_v3") else ("GPU" if m["key"] == "nemotron" else device_label),
                "downloaded": s.get("downloaded", True),
                "downloading": s.get("downloading", False),
                "progress": s.get("progress", ""),
                "bundled": model_manager.MODEL_SOURCES.get(m["key"], {}).get("kind") == "local",
            })
        return out

    def get_state(self):
        updater.refresh_async()
        gpu_pack.refresh_async()
        return {
            "history": history_manager.get_recent(limit=200),
            "mtime": _history_mtime(),
            "settings": settings_manager.load_settings(),
            "autostart": win_startup.is_autostart_enabled(),
            "models": self._models_with_status(),
            "languages": languages.get_languages(),
            "llm_status": smart_enhancer.get_llm_status(),
            "dictionary": smart_enhancer.get_dictionary_entries(),
            "update": updater.get_status(),
            "gpu": gpu_pack.get_status(),
        }

    def set_selected_languages(self, codes):
        if not isinstance(codes, list):
            return {"ok": False}
        clean_codes = [c for c in codes if isinstance(c, str) and c]
        if not clean_codes:
            clean_codes = ["en"]
        s = settings_manager.load_settings()
        s["selected_languages"] = clean_codes
        settings_manager.save_settings(s)
        return {"ok": True, "selected_languages": clean_codes}

    def poll(self, mtime):
        m = _history_mtime()
        llm = smart_enhancer.get_llm_status()
        if m != mtime:
            return {
                "changed": True,
                "mtime": m,
                "history": history_manager.get_recent(limit=200),
                "llm_status": llm,
                "models": self._models_with_status(),
                "update": updater.get_status(),
                "gpu": gpu_pack.get_status(),
            }
        return {"changed": False, "llm_status": llm, "models": self._models_with_status(),
                "update": updater.get_status(), "gpu": gpu_pack.get_status()}

    def gpu_download(self):
        gpu_pack.start_download()
        return gpu_pack.get_status()

    def gpu_delete(self):
        try:
            st = gpu_pack.delete_pack()
            ok = bool(st.pop("ok", True))
            return {"ok": ok, "reason": st.pop("reason", ""), "gpu": st}
        except Exception:
            return {"ok": False, "reason": "", "gpu": gpu_pack.get_status()}

    def check_updates(self):
        updater.refresh_async()
        return updater.get_status()

    def download_update(self):
        updater.download_async()
        return updater.get_status()

    def install_update(self):
        st = updater.get_status()
        path = st.get("path")
        kind = st.get("kind", "patch")
        if not path or st.get("phase") != "ready":
            return {"ok": False}

        def _quit_all():
            _send_engine("quit")
            try:
                import time
                time.sleep(0.5)
            except Exception:
                pass
            try:
                if window:
                    window.destroy()
            except Exception:
                pass
            os._exit(0)

        if kind == "patch":
            ok = updater.apply_patch_and_restart(path, _quit_all)
        else:
            ok = updater.launch_installer_and_quit(path, _quit_all)
        return {"ok": ok}

    def download_model(self, key):
        model_manager.start_download(key)
        return self._models_with_status()

    def delete_model(self, key):
        if key == settings_manager.load_settings().get("model_key"):
            return {"ok": False, "reason": "active", "models": self._models_with_status()}
        res = model_manager.delete_model(key)
        return {"ok": res.get("ok", False), "reason": res.get("reason", ""),
                "models": self._models_with_status()}

    def delete_llm(self):
        try:
            st = smart_enhancer.delete_llm_files()
            ok = bool(st.pop("ok", True))
            return {"ok": ok, "reason": st.pop("reason", ""), "llm_status": st}
        except Exception:
            return {"ok": False, "reason": "", "llm_status": smart_enhancer.get_llm_status()}

    def download_llm(self):
        smart_enhancer.start_llm_download()
        return smart_enhancer.get_llm_status()

    def get_dictionary(self):
        return smart_enhancer.get_dictionary_entries()

    def add_dictionary_entry(self, spoken, written):
        try:
            entries = smart_enhancer.add_dictionary_entry(spoken, written)
            return {"ok": True, "dictionary": entries}
        except Exception:
            return {"ok": False, "dictionary": smart_enhancer.get_dictionary_entries()}

    def remove_dictionary_entry(self, spoken):
        try:
            entries = smart_enhancer.remove_dictionary_entry(spoken)
            return {"ok": True, "dictionary": entries}
        except Exception:
            return {"ok": False, "dictionary": smart_enhancer.get_dictionary_entries()}

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

    def select_device(self, key):
        if key not in ("auto", "cuda", "cpu"):
            return {"ok": False, "live": False}
        s = settings_manager.load_settings()
        s["device"] = key
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"device:{key}")
        return {"ok": True, "live": live}

    def select_pill(self, key):
        # if key not in ("orb", "ember", "meter", "halo", "bloom", "nova", "pulse", "wispr", "bloomcs"):
        if key not in ("bloom", "wispr", "quotal", "quotal-light", "aurora", "nothing", "mercury", "orbit", "scribe", "opus", "neon", "flipboard", "receipt", "face", "cassette", "crt"):
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

    def set_nothing_accent(self, value):
        import re
        v = str(value or "").strip()
        if v in ("amber", "white", "red", "mint", "ice"):
            norm = v
        else:
            m = re.match(r"^#?([0-9a-fA-F]{6})$", v)
            if not m:
                return {"ok": False, "live": False}
            norm = "#" + m.group(1).upper()
        s = settings_manager.load_settings()
        s["nothing_accent"] = norm
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"pillaccent:{norm}")
        return {"ok": True, "live": live}

    def set_mercury_accent(self, value):
        import re
        v = str(value or "").strip()
        if v in ("orange", "ice", "violet", "mint", "mono"):
            norm = v
        else:
            m = re.match(r"^#?([0-9a-fA-F]{6})$", v)
            if not m:
                return {"ok": False, "live": False}
            norm = "#" + m.group(1).upper()
        s = settings_manager.load_settings()
        s["mercury_accent"] = norm
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"mercuryaccent:{norm}")
        return {"ok": True, "live": live}

    def set_orbit_accent(self, value):
        v = str(value or "").strip()
        if v not in ("ion", "solar", "plasma", "mint", "mono"):
            return {"ok": False, "live": False}
        s = settings_manager.load_settings()
        s["orbit_accent"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"orbitaccent:{v}")
        return {"ok": True, "live": live}

    def set_scribe_accent(self, value):
        v = str(value or "").strip().lower()
        if v not in ("orange", "ice", "violet", "mint", "rose"):
            v = "orange"
        s = settings_manager.load_settings()
        s["scribe_accent"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"scribeaccent:{v}")
        return {"ok": True, "live": live}

    def set_opus_accent(self, value):
        v = str(value or "").strip().lower()
        if v not in ("orange", "ice", "violet", "mint", "rose"):
            v = "orange"
        s = settings_manager.load_settings()
        s["opus_accent"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"opusaccent:{v}")
        return {"ok": True, "live": live}

    def set_neon_theme(self, value):
        v = str(value or "").strip().lower()
        if v not in ("sunset", "cyber", "toxic", "rose", "ice"):
            v = "cyber"
        s = settings_manager.load_settings()
        s["neon_theme"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"neontheme:{v}")
        return {"ok": True, "live": live}

    def set_flipboard_style(self, value):
        v = str(value or "").strip().lower()
        if v not in ("classic", "amber", "mint", "ice"):
            v = "amber"
        s = settings_manager.load_settings()
        s["flipboard_style"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"flipboardstyle:{v}")
        return {"ok": True, "live": live}

    def set_receipt_body(self, value):
        v = str(value or "").strip().lower()
        if v not in ("cream", "graphite"):
            v = "cream"
        s = settings_manager.load_settings()
        s["receipt_body"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"receiptbody:{v}")
        return {"ok": True, "live": live}

    def set_cassette_body(self, value):
        v = str(value or "").strip().lower()
        if v not in ("orange", "black", "cream"):
            v = "orange"
        s = settings_manager.load_settings()
        s["cassette_body"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"cassettebody:{v}")
        return {"ok": True, "live": live}

    def set_crt_case(self, value):
        v = str(value or "").strip().lower()
        if v not in ("walnut", "mint", "graphite"):
            v = "walnut"
        s = settings_manager.load_settings()
        s["crt_case"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"crtcase:{v}")
        return {"ok": True, "live": live}

    def set_crt_phosphor(self, value):
        v = str(value or "").strip().lower()
        if v not in ("amber", "green", "white"):
            v = "amber"
        s = settings_manager.load_settings()
        s["crt_phosphor"] = v
        settings_manager.save_settings(s)
        live = _query_engine("version?").startswith("quotal:")
        if live:
            _send_engine(f"crtphosphor:{v}")
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
