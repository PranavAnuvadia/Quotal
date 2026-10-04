"""
app.py - Main desktop application for WinVoice (Wispr Flow / Simple Voice for Windows).
Includes Hardware Key Polling, Floating Overlay Pill, System Tray, Model Switcher, and AI Enhancer.
"""

import sys
import os
import sys
import os
import time
import threading
import ctypes
import winsound
import subprocess
import io

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

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotal.log")

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
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n[CRASH / UNCAUGHT EXCEPTION]\n{err}\n")
    if sys.__stderr__:
        sys.__stderr__.write(err)

sys.excepthook = handle_uncaught_exception

if hasattr(threading, "excepthook"):
    def handle_thread_exception(args):
        err = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[CRASH / THREAD EXCEPTION {args.thread.name}]\n{err}\n")
    threading.excepthook = handle_thread_exception

from audio_recorder import AudioRecorder
from transcriber import Transcriber
from cleaner import clean_text
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
    """Check if physical or logical key is currently held down using Windows API."""
    return bool((ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000) or (ctypes.windll.user32.GetKeyState(vk_code) & 0x8000))


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

        # 1. Overlay Pill
        print("[App] Initializing Floating Pill...")
        self.overlay = OverlayPill()
        
        # 2. Audio Recorder
        print("[App] Initializing Audio Recorder...")
        self.recorder = AudioRecorder(sample_rate=16000)
        
        # 3. Transcriber (Starts with chosen model, default: Whisper Base on CPU)
        print(f"[App] Loading model: {model_cfg['name']}...")
        self.transcriber = Transcriber(
            model_path=model_cfg["path"],
            device=model_cfg["device"]
        )
        
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

        self.is_recording = False
        self._meter_running = False
        self._lock = threading.Lock()
        self._running = True

        print("\n" + "-" * 65)
        print(" ✅ READY! Quotal is active in your System Tray!")
        print(f"    • Active Model: {model_cfg['name']}")
        print("    • Hold [Right Alt] or [F8] to Speak.")
        print("    • Release to paste automatically into any app.")
        print("    • Click the tray icon to switch models or view History.")
        print("-" * 65 + "\n")

    def switch_model(self, model_key: str):
        """Switch speech model dynamically from Dashboard UI."""
        cfg = settings_manager.MODEL_CONFIGS.get(model_key)
        if not cfg:
            return

        def _loader():
            print(f"\n[App] Switching to {cfg['name']}...")
            try:
                new_transcriber = Transcriber(
                    model_path=cfg["path"],
                    device=cfg["device"]
                )
                with self._lock:
                    self.transcriber = new_transcriber
                print(f"[App] Successfully switched to {cfg['name']}!\n")
            except Exception as e:
                print(f"[App] Failed to switch model: {e}")

        threading.Thread(target=_loader, daemon=True).start()

    def _open_dashboard_threadsafe(self, message: str = "show"):
        """Handle IPC commands (e.g. from single_instance.py or tray_manager.py)."""
        if not message:
            message = "show"
        if message == "show":
            base_dir = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(base_dir, "dashboard_host.py")
            venv_quotal = os.path.join(base_dir, ".venv", "Scripts", "Quotal.exe")
            if os.path.exists(venv_quotal):
                exe_path = venv_quotal
            else:
                exe_path = os.path.join(os.path.dirname(sys.executable), "Quotal.exe")
                if not os.path.exists(exe_path):
                    exe_path = sys.executable
            
            # Start dashboard_host.py. It will handle its own single-instance check
            subprocess.Popen(
                [exe_path, script_path],
                creationflags=subprocess.CREATE_NO_WINDOW if "pythonw" in exe_path.lower() or "quotal" in exe_path.lower() else 0
            )
        elif message.startswith("model:"):
            key = message.split(":", 1)[1]
            self.switch_model(key)

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

        self._meter_running = True
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def stop_dictation(self):
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False

        self._meter_running = False
        try:
            self.overlay.set_processing()
        except Exception as e:
            print(f"[Overlay Error] {e}")
        print("[⚡ Transcribing...]")

        threading.Thread(target=self._process_and_paste, daemon=True).start()

    def _process_and_paste(self):
        try:
            audio = self.recorder.stop()
            duration = len(audio) / 16000.0
            
            if duration < 0.2:
                return

            # 1. Transcribe with local model
            raw_text, elapsed = self.transcriber.transcribe(audio)
            
            # 2. Hesitation cleanup (simple-voice deterministic engine)
            cleaned = clean_text(raw_text)

            # 3. AI Smart Enhancer pass (Rules or Qwen 0.5B LLM)
            curr_settings = settings_manager.load_settings()
            mode = curr_settings.get("enhancer_mode")
            if not mode:
                mode = "rules" if curr_settings.get("ai_enhance", True) else "off"
            cleaned = enhance_text_dispatch(cleaned, mode=mode)

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
                paste_text(cleaned)
                print("📋 Pasted directly at cursor!")
                self.overlay.set_done()
                time.sleep(0.4)
            else:
                print("⚠️  No speech detected.")
        except Exception as err:
            print(f"[App Error] Transcription error: {err}")
            import traceback
            traceback.print_exc()
            try:
                self.overlay.set_error()
            except Exception:
                pass
            time.sleep(0.5)
        finally:
            try:
                self.overlay.hide()
            except Exception:
                pass

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
        if hasattr(self, "single_instance") and self.single_instance:
            self.single_instance.close()
        if self.tray:
            self.tray.stop()
        sys.exit(0)


WinVoiceApp = QuotalApp

if __name__ == "__main__":
    app = QuotalApp()
    app.run()
