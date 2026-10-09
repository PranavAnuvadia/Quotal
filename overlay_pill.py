"""
overlay_pill.py - Floating pill controller hosting the hardware-accelerated WebGL Fluid Orb.
Features the exact fluid shader dynamics, responsive equalizer, and frosted glass design.
"""

import sys
import os
import subprocess
import threading
import time

HOST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_pill_host.py")
NATIVE_HOST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native_orb_host.py")
PULSE_HOST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pulse_host.py")
# Wispr runs the author's own PySide6 pill (wispr_pill.py, verbatim) through
# our thin adapter (wispr_qt_bridge.py) so the engine can drive its states.
WISPR_HOST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wispr_qt_bridge.py")
# NOTE: this used to be bloomcs/bloomcs_host.exe (C#). Windows Smart App
# Control blocks that fresh unsigned exe on this machine, so the exact same
# GPU shader now runs from bloomgl_host.py inside the trusted interpreter.
CS_HOST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bloomgl_host.py")

# Styles rendered by the WebGL WebView2 host vs the pure-Python hosts vs the C# GPU host.
# WEB_STYLES = ("orb", "ember", "meter", "halo", "bloom")
# NATIVE_STYLES = ("nova",)
# PULSE_STYLES = ("pulse",)
# CS_STYLES = ("bloomcs",)
WEB_STYLES = ("bloom",)
NATIVE_STYLES = ()
PULSE_STYLES = ()
WISPR_STYLES = ("wispr",)
CS_STYLES = ()


def _resolve_python_exe():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    venv_quotal = os.path.join(base_dir, ".venv", "Scripts", "Quotal.exe")
    if os.path.exists(venv_quotal):
        return venv_quotal
    dir_name = os.path.dirname(sys.executable)
    quotal_exe = os.path.join(dir_name, "Quotal.exe")
    if os.path.exists(quotal_exe):
        return quotal_exe
    return sys.executable


class OverlayPill:
    def __init__(self):
        self.process = None
        self.native_process = None
        self.pulse_process = None
        self.wispr_process = None
        self.cs_process = None
        self._lock = threading.Lock()
        self._is_visible = False
        # Load user's saved pill choice so it is remembered across app reopens and computer restarts
        try:
            import settings_manager
            s = settings_manager.load_settings()
            saved_style = s.get("pill_style", "bloom")
            if saved_style not in ("bloom", "wispr"):
                saved_style = "bloom"
            self._style = saved_style
        except Exception:
            self._style = "bloom"

        if self._style == "wispr":
            self._start_wispr_host()
        else:
            self._start_host()

    def _spawn(self, script):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.Popen(
            [_resolve_python_exe(), script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=creationflags
        )

    def _start_host(self):
        """Launches the dedicated WebGL WebView2 host process."""
        try:
            self.process = self._spawn(HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch WebGL host: {e}")

    def _start_native_host(self):
        """Launches the pure-Python orb host process (lazy, on first Nova use)."""
        try:
            self.native_process = self._spawn(NATIVE_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch native orb host: {e}")

    def _start_pulse_host(self):
        """Launches the pure-Python Pulse pill host process (lazy, on first Pulse use)."""
        try:
            self.pulse_process = self._spawn(PULSE_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch pulse host: {e}")

    def _start_wispr_host(self):
        """Launches the pure-Python Wispr pill host process (lazy, on first Wispr use)."""
        try:
            self.wispr_process = self._spawn(WISPR_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch wispr host: {e}")

    def _cs_available(self):
        return os.path.exists(CS_HOST_SCRIPT)

    def _start_cs_host(self):
        """Launches the Python GPU orb host (lazy, on first Bloom CS use)."""
        try:
            self.cs_process = self._spawn(CS_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch GPU orb host: {e}")

    def _send(self, cmd: str):
        with self._lock:
            if not self.process or self.process.poll() is not None:
                self._start_host()
                time.sleep(0.5)

            if self.process and self.process.stdin:
                try:
                    self.process.stdin.write(cmd + "\n")
                    self.process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Stdin write error: {e}. Restarting host...")
                    self._start_host()
                    time.sleep(0.5)
                    try:
                        if self.process and self.process.stdin:
                            self.process.stdin.write(cmd + "\n")
                            self.process.stdin.flush()
                    except Exception:
                        pass

    def _send_native(self, cmd: str):
        with self._lock:
            if not self.native_process or self.native_process.poll() is not None:
                self._start_native_host()
                time.sleep(0.8)

            if self.native_process and self.native_process.stdin:
                try:
                    self.native_process.stdin.write(cmd + "\n")
                    self.native_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Native stdin write error: {e}. Restarting host...")
                    self._start_native_host()
                    time.sleep(0.8)
                    try:
                        if self.native_process and self.native_process.stdin:
                            self.native_process.stdin.write(cmd + "\n")
                            self.native_process.stdin.flush()
                    except Exception:
                        pass

    def _send_pulse(self, cmd: str):
        with self._lock:
            if not self.pulse_process or self.pulse_process.poll() is not None:
                self._start_pulse_host()
                time.sleep(0.8)

            if self.pulse_process and self.pulse_process.stdin:
                try:
                    self.pulse_process.stdin.write(cmd + "\n")
                    self.pulse_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Pulse stdin write error: {e}. Restarting host...")
                    self._start_pulse_host()
                    time.sleep(0.8)
                    try:
                        if self.pulse_process and self.pulse_process.stdin:
                            self.pulse_process.stdin.write(cmd + "\n")
                            self.pulse_process.stdin.flush()
                    except Exception:
                        pass

    def _send_wispr(self, cmd: str):
        with self._lock:
            if not self.wispr_process or self.wispr_process.poll() is not None:
                self._start_wispr_host()
                time.sleep(0.8)

            if self.wispr_process and self.wispr_process.stdin:
                try:
                    self.wispr_process.stdin.write(cmd + "\n")
                    self.wispr_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Wispr stdin write error: {e}. Restarting host...")
                    self._start_wispr_host()
                    time.sleep(0.8)
                    try:
                        if self.wispr_process and self.wispr_process.stdin:
                            self.wispr_process.stdin.write(cmd + "\n")
                            self.wispr_process.stdin.flush()
                    except Exception:
                        pass

    def _send_cs(self, cmd: str):
        with self._lock:
            if not self.cs_process or self.cs_process.poll() is not None:
                self._start_cs_host()
                time.sleep(0.8)

            if self.cs_process and self.cs_process.stdin:
                try:
                    self.cs_process.stdin.write(cmd + "\n")
                    self.cs_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] C# stdin write error: {e}. Restarting host...")
                    self._start_cs_host()
                    time.sleep(0.8)
                    try:
                        if self.cs_process and self.cs_process.stdin:
                            self.cs_process.stdin.write(cmd + "\n")
                            self.cs_process.stdin.flush()
                    except Exception:
                        pass

    def _is_native(self):
        return self._style in NATIVE_STYLES

    def _is_pulse(self):
        return self._style in PULSE_STYLES

    def _is_wispr(self):
        return self._style in WISPR_STYLES

    def _is_cs(self):
        return self._style in CS_STYLES

    def _hide_native_quiet(self):
        """Hides the native orb only if its host is already running (never starts it)."""
        proc = self.native_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_pulse_quiet(self):
        """Hides the Pulse pill only if its host is already running (never starts it)."""
        proc = self.pulse_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_cs_quiet(self):
        """Hides the C# orb only if its host is already running (never starts it)."""
        proc = self.cs_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_wispr_quiet(self):
        """Hides the Wispr pill only if its host is already running (never starts it)."""
        proc = self.wispr_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_web_quiet(self):
        """Hides the WebGL host only if its host is already running (never starts it)."""
        proc = self.process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def show(self, text="Listening..."):
        """Displays the pill with the fluid orb."""
        self._is_visible = True
        # if self._is_cs():
        #     self._send("hide")
        #     self._hide_native_quiet()
        #     self._hide_pulse_quiet()
        #     self._hide_wispr_quiet()
        #     self._send_cs("show listening")
        # elif self._is_native():
        #     self._send("hide")
        #     self._hide_cs_quiet()
        #     self._hide_pulse_quiet()
        #     self._hide_wispr_quiet()
        #     self._send_native("show listening")
        # elif self._is_pulse():
        #     self._send("hide")
        #     self._hide_native_quiet()
        #     self._hide_cs_quiet()
        #     self._hide_wispr_quiet()
        #     self._send_pulse("show listening")
        if self._is_wispr():
            self._hide_web_quiet()
            # self._hide_native_quiet()
            # self._hide_cs_quiet()
            # self._hide_pulse_quiet()
            self._send_wispr("show listening")
        else:
            # self._hide_native_quiet()
            # self._hide_pulse_quiet()
            self._hide_wispr_quiet()
            # self._hide_cs_quiet()
            self._send("show listening")

    def set_level(self, level: float):
        """Sends current microphone amplitude (0.0 to 1.0) to modulate the fluid waves."""
        if self._is_visible:
            # if self._is_cs():
            #     self._send_cs(f"level {level:.2f}")
            # elif self._is_native():
            #     self._send_native(f"level {level:.2f}")
            # elif self._is_pulse():
            #     self._send_pulse(f"level {level:.2f}")
            if self._is_wispr():
                self._send_wispr(f"level {level:.2f}")
            else:
                self._send(f"level {level:.2f}")

    def set_style(self, style: str):
        """Switches the pill visual theme (kept: bloom, wispr; commented out: orb, ember, meter, halo, nova, pulse, bloomcs)."""
        # if style in CS_STYLES and not self._cs_available():
        #     print("[OverlayPill] bloomgl_host.py missing, falling back to web bloom.")
        #     style = "bloom"
        if style not in ("bloom", "wispr"):
            style = "bloom"
        self._style = style
        # Persist to settings immediately so choices are remembered across app reopens and restarts
        try:
            import settings_manager
            s = settings_manager.load_settings()
            if s.get("pill_style") != style:
                s["pill_style"] = style
                settings_manager.save_settings(s)
        except Exception:
            pass
        # if style in CS_STYLES:
        #     self._send("hide")
        #     self._hide_native_quiet()
        #     self._hide_pulse_quiet()
        #     self._hide_wispr_quiet()
        #     self._send_cs(f"theme {style}")
        # elif style in NATIVE_STYLES:
        #     self._send("hide")
        #     self._hide_cs_quiet()
        #     self._hide_pulse_quiet()
        #     self._hide_wispr_quiet()
        #     self._send_native(f"theme {style}")
        # elif style in PULSE_STYLES:
        #     self._send("hide")
        #     self._hide_native_quiet()
        #     self._hide_cs_quiet()
        #     self._hide_wispr_quiet()
        #     self._send_pulse(f"theme {style}")
        if style in WISPR_STYLES:
            self._hide_web_quiet()
            # self._hide_native_quiet()
            # self._hide_cs_quiet()
            # self._hide_pulse_quiet()
            self._send_wispr(f"theme {style}")
        else:
            # self._hide_native_quiet()
            # self._hide_pulse_quiet()
            self._hide_wispr_quiet()
            # self._hide_cs_quiet()
            self._send(f"theme {style}")

    def set_processing(self):
        """Switches the fluid orb to transcribing/enhancing mode (purple fluid energy)."""
        # if self._is_cs():
        #     self._send_cs("state transcribing")
        # elif self._is_native():
        #     self._send_native("state transcribing")
        # elif self._is_pulse():
        #     self._send_pulse("state transcribing")
        if self._is_wispr():
            self._send_wispr("state transcribing")
        else:
            self._send("state transcribing")

    def set_done(self):
        """Displays green animated checkmark and Pasted/Done state."""
        # if self._is_cs():
        #     self._send_cs("state done")
        # elif self._is_native():
        #     self._send_native("state done")
        # elif self._is_pulse():
        #     self._send_pulse("state done")
        if self._is_wispr():
            self._send_wispr("state done")
        else:
            self._send("state done")

    def set_error(self):
        """Displays red error state."""
        # if self._is_cs():
        #     self._send_cs("state error")
        # elif self._is_native():
        #     self._send_native("state error")
        # elif self._is_pulse():
        #     self._send_pulse("state error")
        if self._is_wispr():
            self._send_wispr("state error")
        else:
            self._send("state error")

    def hide(self):
        """Smoothly conceals the floating pill."""
        self._is_visible = False
        # if self._is_cs():
        #     self._send_cs("hide")
        # elif self._is_native():
        #     self._send_native("hide")
        # elif self._is_pulse():
        #     self._send_pulse("hide")
        if self._is_wispr():
            self._send_wispr("hide")
        else:
            self._send("hide")

    def close(self):
        """Terminates the host process on app shutdown."""
        if self.process and self.process.poll() is None:
            try:
                if self.process.stdin:
                    self.process.stdin.write("quit\n")
                    self.process.stdin.flush()
                self.process.terminate()
            except Exception:
                pass
        proc = self.native_process
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write("quit\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
        pulse = self.pulse_process
        if pulse is not None and pulse.poll() is None:
            try:
                pulse.stdin.write("quit\n")
                pulse.stdin.flush()
            except Exception:
                pass
            try:
                pulse.terminate()
            except Exception:
                pass
        wsp = self.wispr_process
        if wsp is not None and wsp.poll() is None:
            try:
                wsp.stdin.write("quit\n")
                wsp.stdin.flush()
            except Exception:
                pass
            try:
                wsp.terminate()
            except Exception:
                pass
        csp = self.cs_process
        if csp is not None and csp.poll() is None:
            try:
                csp.stdin.write("quit\n")
                csp.stdin.flush()
            except Exception:
                pass
            try:
                csp.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    pill = OverlayPill()
    time.sleep(1.0)
    print("Testing show...")
    pill.show()
    for l in [0.2, 0.6, 0.9, 0.5, 0.2]:
        pill.set_level(l)
        time.sleep(0.5)
    print("Testing processing...")
    pill.set_processing()
    time.sleep(1.5)
    print("Testing hide...")
    pill.hide()
    time.sleep(0.5)
    pill.close()
