"""
app.py - Main desktop application for Quotal voice dictation on Windows.
Includes Hardware Key Polling, Floating Overlay Pill, System Tray, Model Switcher, and AI Enhancer.
"""

import sys
import os
import re
import time
import threading
import ctypes
import winsound
import subprocess
import socket
import io
import numpy as np

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Quotal.Voice.App")
except Exception:
    pass

try:
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
try:
    os.chdir(PROJECT_ROOT)
except Exception:
    pass
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

LOG_FILE = os.path.join(PROJECT_ROOT, "quotal.log")

class SafeLogIO:
    def __init__(self, log_path):
        self.log_path = log_path
    def write(self, msg):
        if not msg:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(msg)
        except Exception:
            pass
    def writelines(self, lines):
        for line in lines:
            self.write(line)
    def flush(self):
        pass
    def isatty(self):
        return False

if sys.stdout is None:
    sys.stdout = SafeLogIO(LOG_FILE)
else:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = SafeLogIO(LOG_FILE)
else:
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import traceback

def handle_uncaught_exception(exc_type, exc_val, exc_tb):
    err = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[CRASH / UNCAUGHT EXCEPTION]\n{err}\n")
    except Exception:
        pass
    if sys.__stderr__:
        try:
            sys.__stderr__.write(err)
        except Exception:
            pass

sys.excepthook = handle_uncaught_exception

if hasattr(threading, "excepthook"):
    def handle_thread_exception(args):
        err = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n[CRASH / THREAD EXCEPTION {args.thread.name}]\n{err}\n")
        except Exception:
            pass
        if sys.__stderr__:
            try:
                sys.__stderr__.write(err)
            except Exception:
                pass
    threading.excepthook = handle_thread_exception

from audio_recorder import AudioRecorder
from transcriber import Transcriber
from cleaner import clean_text, devanagari_to_hinglish
from smart_enhancer import enhance_text, enhance_text_dispatch
from injector import paste_text
from overlay_pill import OverlayPill
from tray_manager import TrayManager
from single_instance import SingleInstance
import history_manager
import settings_manager

# Windows Virtual Key Codes
VK_RMENU = 0xA5    # Right Alt (works on US, UK, and Indian AltGr layouts)
VK_F8 = 0x77       # F8 (alternative trigger key)


def is_key_down(vk_code: int) -> bool:
    """Check if physical key is currently held down using Windows asynchronous API."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)


class QuotalApp:
    def __init__(self):
        # 0. Single instance check
        self.single_instance = SingleInstance(on_show_requested=self._open_dashboard_threadsafe)
        if not self.single_instance.check():
            print("[App] Another instance of Quotal is already running. Signaled existing instance to show.")
            sys.exit(0)

        print("\n" + "=" * 65)
        print("   ❝  Quotal - Intelligent Voice Dictation for Windows  ❞")
        print("=" * 65)
        
        # Load user settings
        self.settings = settings_manager.load_settings()
        model_key = self.settings.get("model_key", "base")
        model_cfg = settings_manager.MODEL_CONFIGS.get(model_key, settings_manager.MODEL_CONFIGS["base"])

        self._lock = threading.Lock()
        self.is_recording = False
        self._meter_running = False
        self._running = True

        # 1. Overlay Pill
        print("[App] Initializing Floating Pill...")
        self.overlay = OverlayPill()
        current_pill = self.settings.get("pill_style", "bloom")
        if current_pill not in ("bloom", "wispr", "quotal", "quotal-light", "aurora", "nothing", "mercury", "orbit", "scribe", "opus", "neon", "flipboard"):
            current_pill = "bloom"
        self.overlay.set_style(current_pill)
        
        # 2. Audio Recorder (persistent WASAPI stream)
        print("[App] Initializing Audio Recorder...")
        self.recorder = AudioRecorder(sample_rate=16000)
        
        # 3. Transcriber (Loaded in background thread so app boots instantaneously)
        self.transcriber = None
        self.transcriber_ready = threading.Event()
        self._model_gen = 1
        boot_gen = self._model_gen

        def _init_model_worker():
            print(f"[App] Loading model in background: {model_cfg['name']}...")
            try:
                t = Transcriber(
                    model_path=model_cfg["path"],
                    device=self._resolve_device()
                )
                with self._lock:
                    if self._model_gen != boot_gen:
                        t.unload()
                        return
                    self.transcriber = t
                    self.transcriber_ready.set()
                print(f"[App] Active Model Ready: {model_cfg['name']}")
            except Exception as e:
                print(f"[App Error] Failed to load model: {e}")
                self.transcriber_ready.set()

        threading.Thread(target=_init_model_worker, daemon=True).start()
        
        # If launched by user (not system startup background), open dashboard immediately
        start_silent = ("--silent" in sys.argv or "--background" in sys.argv)
        if not start_silent:
            self._open_dashboard_threadsafe("show")
        
        # 4. System Tray
        print("[App] Initializing System Tray Icon...")
        self.tray = TrayManager(
            on_open_dashboard=lambda: self._open_dashboard_threadsafe("show"),
            on_quit=self.quit
        )
        self.tray.start()

        print("\n" + "-" * 65)
        print(" ✅ READY! Quotal is active in your System Tray!")
        print(f"    • Active Model: {model_cfg['name']}")
        print("    • Hold [Right Alt] or [F8] to Speak.")
        print("    • Release to paste automatically into any app.")
        print("    • Click the tray icon to switch models or view History.")
        print("-" * 65 + "\n")

    def _resolve_device(self) -> str:
        """Compute device from settings: auto (probe) | cuda (force GPU) | cpu (force CPU)."""
        try:
            dev = settings_manager.load_settings().get("device", "auto")
        except Exception:
            dev = "auto"
        return dev if dev in ("auto", "cuda", "cpu") else "auto"

    def switch_model(self, model_key: str):
        """Switch speech model dynamically from Dashboard UI."""
        cfg = settings_manager.MODEL_CONFIGS.get(model_key)
        if not cfg:
            return

        with self._lock:
            self._model_gen += 1
            current_gen = self._model_gen
            self.transcriber_ready.clear()

        def _loader():
            print(f"\n[App] Switching to {cfg['name']}...")
            try:
                new_transcriber = Transcriber(
                    model_path=cfg["path"],
                    device=self._resolve_device()
                )
                with self._lock:
                    if self._model_gen != current_gen:
                        new_transcriber.unload()
                        return
                    old = self.transcriber
                    self.transcriber = new_transcriber
                    self.transcriber_ready.set()
                if old:
                    old.unload()
                print(f"[App] Successfully switched to {cfg['name']}!\n")
            except Exception as e:
                print(f"[App Error] Failed to switch model: {e}")
                with self._lock:
                    self.transcriber_ready.set()

        threading.Thread(target=_loader, daemon=True).start()

    def switch_device(self, device: str):
        """Switch compute device (auto/cuda/cpu) and reload current model."""
        if device not in ("auto", "cuda", "cpu"):
            return
        s = settings_manager.load_settings()
        s["device"] = device
        settings_manager.save_settings(s)
        print(f"\n[App] Compute device -> {device}, reloading model...")
        self.switch_model(s.get("model_key", "base"))

    def _open_dashboard_threadsafe(self, message: str = "show"):
        """Handle IPC commands (e.g. from single_instance.py or tray_manager.py)."""
        if not message:
            message = "show"
        if message == "show":
            # Check if dashboard is already running on DASH_PORT (48292); if so, signal in < 1ms
            try:
                s = socket.create_connection(("127.0.0.1", 48292), timeout=0.25)
                s.sendall(b"show\n")
                s.close()
                return
            except Exception:
                pass

            base_dir = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(base_dir, "dashboard_host.py")
            venv_py = os.path.join(base_dir, ".venv", "Scripts", "pythonw.exe")
            if not os.path.exists(venv_py):
                venv_py = os.path.join(base_dir, ".venv", "Scripts", "python.exe")
            if os.path.exists(venv_py):
                exe_path = venv_py
            else:
                exe_path = sys.executable
            
            # Start dashboard_host.py only if not already running
            subprocess.Popen(
                [exe_path, script_path],
                cwd=base_dir
            )
        elif message.startswith("model:"):
            key = message.split(":", 1)[1]
            self.switch_model(key)
        elif message.startswith("device:"):
            self.switch_device(message.split(":", 1)[1].strip())
        elif message.startswith("pill:"):
            key = message.split(":", 1)[1]
            self.preview_pill_style(key)
        elif message.startswith("pillaccent:"):
            value = message.split(":", 1)[1]
            self.set_nothing_accent(value)
        elif message.startswith("mercuryaccent:"):
            value = message.split(":", 1)[1]
            self.set_mercury_accent(value)
        elif message.startswith("orbitaccent:"):
            value = message.split(":", 1)[1]
            self.set_orbit_accent(value)
        elif message.startswith("scribeaccent:"):
            value = message.split(":", 1)[1]
            self.set_scribe_accent(value)
        elif message.startswith("opusaccent:"):
            value = message.split(":", 1)[1]
            self.set_opus_accent(value)
        elif message.startswith("neontheme:"):
            value = message.split(":", 1)[1]
            self.set_neon_theme(value)
        elif message.startswith("flipboardstyle:"):
            value = message.split(":", 1)[1]
            self.set_flipboard_style(value)
        elif message.startswith("receiptbody:"):
            value = message.split(":", 1)[1]
            self.set_receipt_body(value)
        elif message.startswith("cassettebody:"):
            value = message.split(":", 1)[1]
            self.set_cassette_body(value)
        elif message.startswith("crtcase:"):
            value = message.split(":", 1)[1]
            self.set_crt_case(value)
        elif message.startswith("crtphosphor:"):
            value = message.split(":", 1)[1]
            self.set_crt_phosphor(value)
        elif message == "quit":
            self.quit()

    def preview_pill_style(self, key: str):
        """Apply a new pill theme and flash it briefly so the user sees it live."""
        # if key not in ("orb", "ember", "meter", "halo", "bloom", "nova", "pulse", "wispr", "bloomcs"):
        if key not in ("bloom", "wispr", "quotal", "quotal-light", "aurora", "nothing", "mercury", "orbit", "scribe", "opus", "neon", "flipboard", "receipt", "face", "cassette", "crt"):
            return
        try:
            s = settings_manager.load_settings()
            s["pill_style"] = key
            settings_manager.save_settings(s)
            self.settings["pill_style"] = key
        except Exception:
            pass
        try:
            with self._lock:
                recording = self.is_recording
            # Push theme to the live pill host (no restart needed)
            self.overlay.set_style(key)
            if recording:
                return  # Don't hijack the pill mid-dictation
            self.overlay.show("Listening...")
            threading.Timer(1.4, self._hide_pill_preview).start()
        except Exception as e:
            print(f"[App] Pill preview failed: {e}")

    def set_nothing_accent(self, value: str):
        """Saves the Nothing pill LED color and pushes it live (no restart)."""
        import re
        v = str(value or "").strip()
        if v in ("amber", "white", "red", "mint", "ice"):
            norm = v
        else:
            m = re.match(r"^#?([0-9a-fA-F]{6})$", v)
            if not m:
                return
            norm = "#" + m.group(1).upper()
        try:
            s = settings_manager.load_settings()
            s["nothing_accent"] = norm
            settings_manager.save_settings(s)
            self.settings["nothing_accent"] = norm
        except Exception:
            pass
        try:
            self.overlay.set_nothing_accent(norm)
        except Exception as e:
            print(f"[App] Nothing accent push failed: {e}")

    def set_mercury_accent(self, value: str):
        """Saves the Mercury pill reflection color and pushes it live (no restart)."""
        import re
        v = str(value or "").strip()
        if v in ("orange", "ice", "violet", "mint", "mono"):
            norm = v
        else:
            m = re.match(r"^#?([0-9a-fA-F]{6})$", v)
            if not m:
                return
            norm = "#" + m.group(1).upper()
        try:
            s = settings_manager.load_settings()
            s["mercury_accent"] = norm
            settings_manager.save_settings(s)
            self.settings["mercury_accent"] = norm
        except Exception:
            pass
        try:
            self.overlay.set_mercury_accent(norm)
        except Exception as e:
            print(f"[App] Mercury accent push failed: {e}")

    def set_orbit_accent(self, value: str):
        """Saves the Orbit pill colour and pushes it live (no restart)."""
        v = str(value or "").strip()
        if v not in ("ion", "solar", "plasma", "mint", "mono"):
            return
        try:
            s = settings_manager.load_settings()
            s["orbit_accent"] = v
            settings_manager.save_settings(s)
            self.settings["orbit_accent"] = v
        except Exception:
            pass
        try:
            self.overlay.set_orbit_accent(v)
        except Exception as e:
            print(f"[App] Orbit accent push failed: {e}")

    def set_opus_accent(self, value: str):
        """Saves the Opus pill accent color and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("orange", "ice", "violet", "mint", "rose"):
            v = "orange"
        try:
            s = settings_manager.load_settings()
            s["opus_accent"] = v
            settings_manager.save_settings(s)
            self.settings["opus_accent"] = v
        except Exception:
            pass
        try:
            self.overlay.set_opus_accent(v)
        except Exception as e:
            print(f"[App] Opus accent push failed: {e}")

    def set_neon_theme(self, value: str):
        """Saves the Neon pill theme and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("sunset", "cyber", "toxic", "rose", "ice"):
            v = "cyber"
        try:
            s = settings_manager.load_settings()
            s["neon_theme"] = v
            settings_manager.save_settings(s)
            self.settings["neon_theme"] = v
        except Exception:
            pass
        try:
            self.overlay.set_neon_theme(v)
        except Exception as e:
            print(f"[App] Neon theme push failed: {e}")

    def set_flipboard_style(self, value: str):
        """Saves the Flipboard pill style and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("classic", "amber", "mint", "ice"):
            v = "amber"
        try:
            s = settings_manager.load_settings()
            s["flipboard_style"] = v
            settings_manager.save_settings(s)
            self.settings["flipboard_style"] = v
        except Exception:
            pass
        try:
            self.overlay.set_flipboard_style(v)
        except Exception as e:
            print(f"[App] Flipboard style push failed: {e}")

    def set_receipt_body(self, value: str):
        """Saves the Receipt pill body and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("cream", "graphite"):
            v = "cream"
        try:
            s = settings_manager.load_settings()
            s["receipt_body"] = v
            settings_manager.save_settings(s)
            self.settings["receipt_body"] = v
        except Exception:
            pass
        try:
            self.overlay.set_receipt_body(v)
        except Exception as e:
            print(f"[App] Receipt body push failed: {e}")

    def set_cassette_body(self, value: str):
        """Saves the Cassette pill body and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("orange", "black", "cream"):
            v = "orange"
        try:
            s = settings_manager.load_settings()
            s["cassette_body"] = v
            settings_manager.save_settings(s)
            self.settings["cassette_body"] = v
        except Exception:
            pass
        try:
            self.overlay.set_cassette_body(v)
        except Exception as e:
            print(f"[App] Cassette body push failed: {e}")

    def set_crt_case(self, value: str):
        """Saves the CRT pill case and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("walnut", "mint", "graphite"):
            v = "walnut"
        try:
            s = settings_manager.load_settings()
            s["crt_case"] = v
            settings_manager.save_settings(s)
            self.settings["crt_case"] = v
        except Exception:
            pass
        try:
            self.overlay.set_crt_case(v)
        except Exception as e:
            print(f"[App] CRT case push failed: {e}")

    def set_crt_phosphor(self, value: str):
        """Saves the CRT pill phosphor and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("amber", "green", "white"):
            v = "amber"
        try:
            s = settings_manager.load_settings()
            s["crt_phosphor"] = v
            settings_manager.save_settings(s)
            self.settings["crt_phosphor"] = v
        except Exception:
            pass
        try:
            self.overlay.set_crt_phosphor(v)
        except Exception as e:
            print(f"[App] CRT phosphor push failed: {e}")

    def set_scribe_accent(self, value: str):
        """Saves the Scribe pill accent color and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("orange", "ice", "violet", "mint", "rose"):
            v = "orange"
        try:
            s = settings_manager.load_settings()
            s["scribe_accent"] = v
            settings_manager.save_settings(s)
            self.settings["scribe_accent"] = v
        except Exception:
            pass
        try:
            self.overlay.set_scribe_accent(v)
        except Exception as e:
            print(f"[App] Scribe accent push failed: {e}")

    def _hide_pill_preview(self):
        """Hides the preview pill, unless a real dictation started meanwhile."""
        with self._lock:
            if self.is_recording:
                return
        try:
            self.overlay.hide()
        except Exception:
            pass

    def _play_chime(self, freq: int, duration: int):
        """Plays subtle audio feedback tone in background."""
        try:
            threading.Thread(target=lambda: winsound.Beep(freq, duration), daemon=True).start()
        except Exception:
            pass

    def _meter_loop(self):
        """Streams audio volume level to the floating pill."""
        while self._meter_running:
            level = self.recorder.get_level()
            self.overlay.set_level(level)
            time.sleep(0.03)

    def start_dictation(self):
        with self._lock:
            if self.is_recording:
                return
            self.is_recording = True

        self._play_chime(800, 35)
        print("\n[🎙️ Listening...] (Keep holding key while speaking)")

        # Show pill overlay immediately so visual response is instant
        try:
            self.overlay.show("Listening...")
        except Exception as e:
            print(f"[Overlay Error] {e}")

        # Start audio recording safely
        try:
            self.recorder.start()
        except Exception as e:
            print(f"[Recorder Error] {e}")

        # If streaming model (Nemotron), start stream session and consumer thread
        with self._lock:
            transcriber = self.transcriber
        is_streaming = transcriber is not None and getattr(transcriber, "is_streaming_model", False)

        if is_streaming:
            import queue
            self._stream_queue = queue.Queue()

            curr_settings = settings_manager.load_settings()
            selected_langs = curr_settings.get("selected_languages", ["en"])
            has_hindi = "hi" in selected_langs
            has_hinglish = "hinglish" in selected_langs
            is_english_only = (selected_langs == ["en"] or (not has_hindi and not has_hinglish))

            def _on_partial(txt):
                if not txt:
                    return
                # If English only or Hinglish, clean any stray Devanagari in live partials immediately
                if (is_english_only or has_hinglish) and re.search(r'[\u0900-\u097F]', txt):
                    txt = devanagari_to_hinglish(txt)
                self.overlay.set_text(txt)

            target_stream_lang = "en" if is_english_only else ("hi" if has_hindi else "auto")
            try:
                transcriber.start_stream(on_partial=_on_partial, language=target_stream_lang)
            except Exception as e:
                print(f"[Streaming Error] start_stream failed: {e}")
            self.recorder.set_chunk_callback(lambda chunk: self._stream_queue.put(chunk))
            self._stream_thread = threading.Thread(target=self._stream_infer_loop, daemon=True)
            self._stream_thread.start()
        else:
            self._stream_thread = None
            self.recorder.set_chunk_callback(None)

        self._meter_running = True
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def _stream_infer_loop(self):
        """Worker loop feeding audio chunks to the streaming transcriber."""
        buf = []
        buf_len = 0
        CHUNK_SIZE = 1024  # 64ms instant chunks
        while self.is_recording or (hasattr(self, "_stream_queue") and not self._stream_queue.empty()):
            try:
                chunk = self._stream_queue.get(timeout=0.04)
            except Exception:
                continue
            if chunk is None:
                break
            buf.append(chunk)
            buf_len += len(chunk)
            while buf_len >= CHUNK_SIZE:
                cat = np.concatenate(buf, axis=0)
                to_feed = cat[:CHUNK_SIZE]
                rem = cat[CHUNK_SIZE:]
                buf = [rem] if len(rem) > 0 else []
                buf_len = len(rem)
                with self._lock:
                    transcriber = self.transcriber
                if transcriber and getattr(transcriber, "is_streaming_model", False):
                    try:
                        transcriber.feed_stream(to_feed)
                    except Exception as e:
                        print(f"[Stream Feed Error] {e}")

        # Feed any remaining tail samples
        if buf and buf_len > 0:
            cat = np.concatenate(buf, axis=0)
            with self._lock:
                transcriber = self.transcriber
            if transcriber and getattr(transcriber, "is_streaming_model", False):
                try:
                    transcriber.feed_stream(cat)
                except Exception:
                    pass

    def stop_dictation(self):
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False
            # Immediately capture recorded audio to eliminate cross-thread race conditions
            audio = self.recorder.stop()

        self._meter_running = False
        if hasattr(self, "_stream_queue") and self._stream_queue:
            self._stream_queue.put(None)
        self.recorder.set_chunk_callback(None)
        try:
            self.overlay.set_processing()
        except Exception as e:
            print(f"[Overlay Error] {e}")
        print("[⚡ Transcribing...]")

        threading.Thread(target=self._process_and_paste, args=(audio,), daemon=True).start()

    def _process_and_paste(self, audio: np.ndarray = None):
        try:
            if audio is None or len(audio) == 0:
                audio = self.recorder.stop()
            duration = len(audio) / 16000.0
            
            if duration < 0.2:
                self.overlay.hide()
                return

            # 1. Transcribe with local model (ensuring background loader completed)
            if hasattr(self, "transcriber_ready") and not self.transcriber_ready.is_set():
                if not self.transcriber_ready.wait(timeout=10.0):
                    print("[App Error] Timed out waiting for model to load.")
                    self.overlay.set_error()
                    threading.Timer(0.5, lambda: self.overlay.hide()).start()
                    return

            with self._lock:
                transcriber = self.transcriber
            if not transcriber:
                print("[App Error] No active transcriber.")
                self.overlay.set_error()
                threading.Timer(0.5, lambda: self.overlay.hide()).start()
                return

            t0 = time.time()
            curr_settings = settings_manager.load_settings()
            selected_langs = curr_settings.get("selected_languages", ["en"])
            has_hindi = "hi" in selected_langs
            has_hinglish = "hinglish" in selected_langs
            is_english_only = (selected_langs == ["en"] or (not has_hindi and not has_hinglish))
            is_streaming = transcriber is not None and getattr(transcriber, "is_streaming_model", False)

            if is_streaming:
                # Wait for any in-flight audio chunks in the stream queue to finish feeding
                if hasattr(self, "_stream_thread") and self._stream_thread and self._stream_thread.is_alive():
                    self._stream_thread.join(timeout=1.0)
                # Streaming Nemotron: finalize stream for complete transcript
                raw_text = transcriber.finish_stream(full_audio=audio)
                if not raw_text and len(audio) > 0:
                    raw_text, _ = transcriber.transcribe(audio, selected_languages=selected_langs)
                elapsed = time.time() - t0
            else:
                raw_text, elapsed = transcriber.transcribe(audio, selected_languages=selected_langs)

            # Strict Language Policy:
            # 1. If Hinglish selected: Romanize Devanagari to Hinglish.
            # 2. If English only selected (Hindi NOT selected): strictly prevent Devanagari from showing up,
            #    mapping any accent-tripped loanwords/Devanagari directly to English words.
            # 3. If Hindi explicitly selected: leave intact in native Devanagari script.
            has_deva = bool(re.search(r'[\u0900-\u097F]', raw_text))
            if has_deva:
                if has_hinglish or is_english_only:
                    raw_text = devanagari_to_hinglish(raw_text)

            # 2. Hesitation cleanup (simple-voice deterministic engine)
            cleaned = clean_text(raw_text)

            # 3. AI Smart Enhancer pass (Rules or Qwen 0.5B LLM)
            mode = curr_settings.get("enhancer_mode")
            if not mode:
                mode = "rules" if curr_settings.get("ai_enhance", True) else "off"
            cleaned = enhance_text_dispatch(cleaned, mode=mode)

            # Push cleaned enhanced text to caption pill (soft flash on changed words)
            if cleaned:
                self.overlay.set_text(cleaned)

            print(f"⏱️  Spoke: {duration:.1f}s | Latency: {elapsed*1000:.0f}ms")
            print(f"📝 Raw:     \"{raw_text}\"")
            print(f"✨ Cleaned: \"{cleaned}\"")

            # 4. Save to local history log
            if cleaned:
                history_manager.add_entry(
                    raw_text=raw_text,
                    cleaned_text=cleaned,
                    duration_sec=duration,
                    latency_ms=elapsed * 1000.0
                )
                self._play_chime(1200, 40)
                paste_text(cleaned, restore_clipboard=False)
                print("📋 Pasted directly at cursor & copied to clipboard!")
                self.overlay.set_done()
                # Non-blocking auto-hide: free this worker thread immediately (0ms delay)
                threading.Timer(0.35, lambda: self.overlay.hide()).start()
            else:
                print("⚠️  No speech detected.")
                self.overlay.hide()
        except Exception as err:
            print(f"[App Error] Transcription error: {err}")
            import traceback
            traceback.print_exc()
            try:
                self.overlay.set_error()
                threading.Timer(0.5, lambda: self.overlay.hide()).start()
            except Exception:
                self.overlay.hide()

    def _key_poll_loop(self):
        """Hardware polling loop in dedicated worker thread."""
        key_was_down = False
        while self._running:
            try:
                key_is_down = is_key_down(VK_RMENU) or is_key_down(VK_F8)
                
                if key_is_down and not key_was_down:
                    key_was_down = True
                    self.start_dictation()
                elif not key_is_down and key_was_down:
                    key_was_down = False
                    self.stop_dictation()
            except Exception as e:
                print(f"[KeyPoll Loop Error] {e}")
                import traceback
                traceback.print_exc()
                
            time.sleep(0.015)

    def run(self):
        """Main event loop."""
        poll_thread = threading.Thread(target=self._key_poll_loop, daemon=True)
        poll_thread.start()

        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.quit()

    def quit(self):
        """Clean shutdown."""
        self._running = False
        if self.overlay:
            try:
                self.overlay.close()
            except Exception:
                pass
        if hasattr(self, "recorder") and self.recorder:
            try:
                self.recorder.close()
            except Exception:
                pass
        if hasattr(self, "single_instance") and self.single_instance:
            self.single_instance.close()
        if self.tray:
            self.tray.stop()
        os._exit(0)


WinVoiceApp = QuotalApp

if __name__ == "__main__":
    app = QuotalApp()
    app.run()
