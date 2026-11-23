"""
overlay_pill.py - Floating pill controller hosting the hardware-accelerated WebGL Fluid Orb.
Features the exact fluid shader dynamics, responsive equalizer, and frosted glass design.
"""

import sys
import os
import subprocess
import threading
import time
import settings_manager

PILLS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PILLS_DIR)

HOST_SCRIPT = os.path.join(PILLS_DIR, "bloom", "web_pill_host.py")
NATIVE_HOST_SCRIPT = os.path.join(PILLS_DIR, "bloom", "native_orb_host.py")
PULSE_HOST_SCRIPT = os.path.join(PILLS_DIR, "bloom", "pulse_host.py")
# Capsule runs the author's own PySide6 pill (wispr_pill.py, verbatim) through
# our thin adapter (wispr_qt_bridge.py) so the engine can drive its states.
WISPR_HOST_SCRIPT = os.path.join(PILLS_DIR, "wispr", "wispr_qt_bridge.py")
# Quotal pill: same pattern (quotal_pill.py verbatim + quotal_qt_bridge.py).
QUOTAL_HOST_SCRIPT = os.path.join(PILLS_DIR, "quotal", "quotal_qt_bridge.py")
# Aurora pill: same pattern (aurora_pill.py verbatim + aurora_qt_bridge.py).
AURORA_HOST_SCRIPT = os.path.join(PILLS_DIR, "aurora", "aurora_qt_bridge.py")
# Nothing pill: same pattern (dotmatrix_pill.py verbatim + nothing_qt_bridge.py).
NOTHING_HOST_SCRIPT = os.path.join(PILLS_DIR, "nothing", "nothing_qt_bridge.py")
# Mercury pill: same pattern (mercury_pill.py verbatim + mercury_qt_bridge.py).
MERCURY_HOST_SCRIPT = os.path.join(PILLS_DIR, "mercury", "mercury_qt_bridge.py")
# Orbit pill: same pattern (orbit_pill.py verbatim + orbit_qt_bridge.py).
ORBIT_HOST_SCRIPT = os.path.join(PILLS_DIR, "orbit", "orbit_qt_bridge.py")
# Scribe pill: live caption capsule (scribe_pill.py + scribe_qt_bridge.py).
SCRIBE_HOST_SCRIPT = os.path.join(PILLS_DIR, "scribe", "scribe_qt_bridge.py")
# Opus pill: live caption card with hanging quote marks (opus_pill.py + opus_qt_bridge.py).
OPUS_HOST_SCRIPT = os.path.join(PILLS_DIR, "opus", "opus_qt_bridge.py")
NEON_HOST_SCRIPT = os.path.join(PILLS_DIR, "neon", "neon_qt_bridge.py")
FLIPBOARD_HOST_SCRIPT = os.path.join(PILLS_DIR, "flipboard", "flipboard_qt_bridge.py")
RECEIPT_HOST_SCRIPT = os.path.join(PILLS_DIR, "receipt", "receipt_qt_bridge.py")
FACE_HOST_SCRIPT = os.path.join(PILLS_DIR, "face", "face_qt_bridge.py")
CASSETTE_HOST_SCRIPT = os.path.join(PILLS_DIR, "cassette", "cassette_qt_bridge.py")
CRT_HOST_SCRIPT = os.path.join(PILLS_DIR, "crt", "crt_qt_bridge.py")
# NOTE: this used to be bloomcs/bloomcs_host.exe (C#). Windows Smart App
# Control blocks that fresh unsigned exe on this machine, so the exact same
# GPU shader now runs from bloomgl_host.py inside the trusted interpreter.
CS_HOST_SCRIPT = os.path.join(PILLS_DIR, "bloom", "bloomgl_host.py")

# Styles rendered by the WebGL WebView2 host vs the pure-Python hosts vs the C# GPU host.
# WEB_STYLES = ("orb", "ember", "meter", "halo", "bloom")
# NATIVE_STYLES = ("nova",)
# PULSE_STYLES = ("pulse",)
# CS_STYLES = ("bloomcs",)
WEB_STYLES = ("bloom",)
NATIVE_STYLES = ()
PULSE_STYLES = ()
WISPR_STYLES = ("wispr",)
QUOTAL_STYLES = ("quotal", "quotal-light")
AURORA_STYLES = ("aurora",)
NOTHING_STYLES = ("nothing",)
MERCURY_STYLES = ("mercury",)
ORBIT_STYLES = ("orbit",)
SCRIBE_STYLES = ("scribe",)
OPUS_STYLES = ("opus",)
NEON_STYLES = ("neon",)
FLIPBOARD_STYLES = ("flipboard",)
RECEIPT_STYLES = ("receipt",)
FACE_STYLES = ("face",)
CASSETTE_STYLES = ("cassette",)
CRT_STYLES = ("crt",)
CS_STYLES = ()


def _resolve_python_exe():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    venv_pyw = os.path.join(base_dir, ".venv", "Scripts", "pythonw.exe")
    if os.path.exists(venv_pyw):
        return venv_pyw
    venv_py = os.path.join(base_dir, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        return venv_py
    return sys.executable


class OverlayPill:
    def __init__(self):
        self.process = None
        self.native_process = None
        self.pulse_process = None
        self.wispr_process = None
        self.quotal_process = None
        self.aurora_process = None
        self.nothing_process = None
        self.mercury_process = None
        self.orbit_process = None
        self.scribe_process = None
        self.opus_process = None
        self.neon_process = None
        self.flipboard_process = None
        self.receipt_process = None
        self.face_process = None
        self.cassette_process = None
        self.crt_process = None
        self.cs_process = None
        self._lock = threading.Lock()
        self._is_visible = False
        # Load user's saved pill choice so it is remembered across app reopens and computer restarts
        try:
            import settings_manager
            s = settings_manager.load_settings()
            saved_style = s.get("pill_style", "bloom")
            if saved_style not in ("bloom", "wispr", "quotal", "quotal-light", "aurora", "nothing", "mercury", "orbit", "scribe", "opus", "neon", "flipboard", "receipt", "face", "cassette", "crt"):
                saved_style = "bloom"
            self._style = saved_style
        except Exception:
            self._style = "bloom"

        if self._style == "wispr":
            self._start_wispr_host()
        elif self._style in ("quotal", "quotal-light"):
            self._start_quotal_host()
        elif self._style == "aurora":
            self._start_aurora_host()
        elif self._style == "nothing":
            self._start_nothing_host()
        elif self._style == "mercury":
            self._start_mercury_host()
        elif self._style == "orbit":
            self._start_orbit_host()
        elif self._style == "scribe":
            self._start_scribe_host()
        elif self._style == "opus":
            self._start_opus_host()
        elif self._style == "neon":
            self._start_neon_host()
        elif self._style == "flipboard":
            self._start_flipboard_host()
        elif self._style == "receipt":
            self._start_receipt_host()
        elif self._style == "face":
            self._start_face_host()
        elif self._style == "cassette":
            self._start_cassette_host()
        elif self._style == "crt":
            self._start_crt_host()
        else:
            self._start_host()

    def _spawn(self, script):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        core_dir = os.path.join(BASE_DIR, "core")
        env["PYTHONPATH"] = BASE_DIR + os.pathsep + core_dir + (os.pathsep + python_path if python_path else "")
        return subprocess.Popen(
            [_resolve_python_exe(), script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=os.path.dirname(script),
            env=env,
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
        """Launches the pure-Python capsule pill host process (lazy, on first Capsule use)."""
        try:
            self.wispr_process = self._spawn(WISPR_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch wispr host: {e}")

    def _start_quotal_host(self):
        """Launches the author's Quotal pill via our Qt bridge (lazy, on first Quotal use)."""
        try:
            self.quotal_process = self._spawn(QUOTAL_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch quotal host: {e}")

    def _start_aurora_host(self):
        """Launches the author's Aurora pill via our Qt bridge (lazy, on first Aurora use)."""
        try:
            self.aurora_process = self._spawn(AURORA_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch aurora host: {e}")

    def _start_nothing_host(self):
        """Launches the author's Nothing pill via our Qt bridge (lazy, on first Nothing use)."""
        try:
            self.nothing_process = self._spawn(NOTHING_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch nothing host: {e}")

    def _start_mercury_host(self):
        """Launches the author's Mercury pill via our Qt bridge (lazy, on first Mercury use)."""
        try:
            self.mercury_process = self._spawn(MERCURY_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch mercury host: {e}")

    def _start_orbit_host(self):
        """Launches the author's Orbit pill via our Qt bridge (lazy, on first Orbit use)."""
        try:
            self.orbit_process = self._spawn(ORBIT_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch orbit host: {e}")


    def _start_opus_host(self):
        """Launches the OPUS live caption pill via our Qt bridge (lazy, on first Opus use)."""
        try:
            self.opus_process = self._spawn(OPUS_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch opus host: {e}")

    def _send_opus(self, cmd: str):
        with self._lock:
            if not self.opus_process or self.opus_process.poll() is not None:
                self._start_opus_host()
                time.sleep(0.8)

            if self.opus_process and self.opus_process.stdin:
                try:
                    self.opus_process.stdin.write(cmd + "\n")
                    self.opus_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Opus stdin write error: {e}. Restarting host...")
                    self._start_opus_host()
                    time.sleep(0.8)
                    try:
                        if self.opus_process and self.opus_process.stdin:
                            self.opus_process.stdin.write(cmd + "\n")
                            self.opus_process.stdin.flush()
                    except Exception:
                        pass

    def _is_opus(self):
        return self._style in OPUS_STYLES

    def _hide_opus_quiet(self):
        """Hides the Opus pill only if its host is already running (never starts it)."""
        proc = self.opus_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass


    def _start_neon_host(self):
        """Launches the NEON live transcript pill via our Qt bridge."""
        try:
            self.neon_process = self._spawn(NEON_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch neon host: {e}")

    def _send_neon(self, cmd: str):
        with self._lock:
            if not self.neon_process or self.neon_process.poll() is not None:
                self._start_neon_host()
                time.sleep(0.8)

            if self.neon_process and self.neon_process.stdin:
                try:
                    self.neon_process.stdin.write(cmd + "\n")
                    self.neon_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Neon stdin write error: {e}. Restarting host...")
                    self._start_neon_host()
                    time.sleep(0.8)
                    try:
                        if self.neon_process and self.neon_process.stdin:
                            self.neon_process.stdin.write(cmd + "\n")
                            self.neon_process.stdin.flush()
                    except Exception:
                        pass

    def _is_neon(self):
        return self._style in NEON_STYLES

    def _hide_neon_quiet(self):
        proc = self.neon_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _start_flipboard_host(self):
        """Launches the FLIPBOARD live transcript pill via our Qt bridge."""
        try:
            self.flipboard_process = self._spawn(FLIPBOARD_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch flipboard host: {e}")

    def _send_flipboard(self, cmd: str):
        with self._lock:
            if not self.flipboard_process or self.flipboard_process.poll() is not None:
                self._start_flipboard_host()
                time.sleep(0.8)

            if self.flipboard_process and self.flipboard_process.stdin:
                try:
                    self.flipboard_process.stdin.write(cmd + "\n")
                    self.flipboard_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Flipboard stdin write error: {e}. Restarting host...")
                    self._start_flipboard_host()
                    time.sleep(0.8)
                    try:
                        if self.flipboard_process and self.flipboard_process.stdin:
                            self.flipboard_process.stdin.write(cmd + "\n")
                            self.flipboard_process.stdin.flush()
                    except Exception:
                        pass

    def _is_flipboard(self):
        return self._style in FLIPBOARD_STYLES

    def _hide_flipboard_quiet(self):
        proc = self.flipboard_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _start_receipt_host(self):
        """Launches the RECEIPT thermal printer pill via our Qt bridge."""
        try:
            self.receipt_process = self._spawn(RECEIPT_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch receipt host: {e}")

    def _send_receipt(self, cmd: str):
        with self._lock:
            if not self.receipt_process or self.receipt_process.poll() is not None:
                self._start_receipt_host()
                time.sleep(0.8)

            if self.receipt_process and self.receipt_process.stdin:
                try:
                    self.receipt_process.stdin.write(cmd + "\n")
                    self.receipt_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Receipt stdin write error: {e}. Restarting host...")
                    self._start_receipt_host()
                    time.sleep(0.8)
                    try:
                        if self.receipt_process and self.receipt_process.stdin:
                            self.receipt_process.stdin.write(cmd + "\n")
                            self.receipt_process.stdin.flush()
                    except Exception:
                        pass

    def _is_receipt(self):
        return self._style in RECEIPT_STYLES

    def _hide_receipt_quiet(self):
        proc = self.receipt_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _start_face_host(self):
        """Launches the FACE live animated pill via our Qt bridge."""
        try:
            self.face_process = self._spawn(FACE_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch face host: {e}")

    def _send_face(self, cmd: str):
        with self._lock:
            if not self.face_process or self.face_process.poll() is not None:
                self._start_face_host()
                time.sleep(0.8)

            if self.face_process and self.face_process.stdin:
                try:
                    self.face_process.stdin.write(cmd + "\n")
                    self.face_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Face stdin write error: {e}. Restarting host...")
                    self._start_face_host()
                    time.sleep(0.8)
                    try:
                        if self.face_process and self.face_process.stdin:
                            self.face_process.stdin.write(cmd + "\n")
                            self.face_process.stdin.flush()
                    except Exception:
                        pass

    def _is_face(self):
        return self._style in FACE_STYLES

    def _hide_face_quiet(self):
        proc = self.face_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _start_cassette_host(self):
        """Launches the CASSETTE live tape deck pill via our Qt bridge."""
        try:
            self.cassette_process = self._spawn(CASSETTE_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch cassette host: {e}")

    def _send_cassette(self, cmd: str):
        with self._lock:
            if not self.cassette_process or self.cassette_process.poll() is not None:
                self._start_cassette_host()
                time.sleep(0.8)

            if self.cassette_process and self.cassette_process.stdin:
                try:
                    self.cassette_process.stdin.write(cmd + "\n")
                    self.cassette_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Cassette stdin write error: {e}. Restarting host...")
                    self._start_cassette_host()
                    time.sleep(0.8)
                    try:
                        if self.cassette_process and self.cassette_process.stdin:
                            self.cassette_process.stdin.write(cmd + "\n")
                            self.cassette_process.stdin.flush()
                    except Exception:
                        pass

    def _is_cassette(self):
        return self._style in CASSETTE_STYLES

    def _hide_cassette_quiet(self):
        proc = self.cassette_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _start_crt_host(self):
        """Launches the CRT 1970s TV pill via our Qt bridge."""
        try:
            self.crt_process = self._spawn(CRT_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch crt host: {e}")

    def _send_crt(self, cmd: str):
        with self._lock:
            if not self.crt_process or self.crt_process.poll() is not None:
                self._start_crt_host()
                time.sleep(0.8)

            if self.crt_process and self.crt_process.stdin:
                try:
                    self.crt_process.stdin.write(cmd + "\n")
                    self.crt_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] CRT stdin write error: {e}. Restarting host...")
                    self._start_crt_host()
                    time.sleep(0.8)
                    try:
                        if self.crt_process and self.crt_process.stdin:
                            self.crt_process.stdin.write(cmd + "\n")
                            self.crt_process.stdin.flush()
                    except Exception:
                        pass

    def _is_crt(self):
        return self._style in CRT_STYLES

    def _hide_crt_quiet(self):
        proc = self.crt_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _start_scribe_host(self):
        """Launches the Scribe live caption pill via our Qt bridge (lazy, on first Scribe use)."""
        try:
            self.scribe_process = self._spawn(SCRIBE_HOST_SCRIPT)
        except Exception as e:
            print(f"[OverlayPill] Failed to launch scribe host: {e}")

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
                    print(f"[OverlayPill] Capsule stdin write error: {e}. Restarting host...")
                    self._start_wispr_host()
                    time.sleep(0.8)
                    try:
                        if self.wispr_process and self.wispr_process.stdin:
                            self.wispr_process.stdin.write(cmd + "\n")
                            self.wispr_process.stdin.flush()
                    except Exception:
                        pass

    def _send_quotal(self, cmd: str):
        with self._lock:
            if not self.quotal_process or self.quotal_process.poll() is not None:
                self._start_quotal_host()
                time.sleep(0.8)

            if self.quotal_process and self.quotal_process.stdin:
                try:
                    self.quotal_process.stdin.write(cmd + "\n")
                    self.quotal_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Quotal stdin write error: {e}. Restarting host...")
                    self._start_quotal_host()
                    time.sleep(0.8)
                    try:
                        if self.quotal_process and self.quotal_process.stdin:
                            self.quotal_process.stdin.write(cmd + "\n")
                            self.quotal_process.stdin.flush()
                    except Exception:
                        pass

    def _send_aurora(self, cmd: str):
        with self._lock:
            if not self.aurora_process or self.aurora_process.poll() is not None:
                self._start_aurora_host()
                time.sleep(0.8)

            if self.aurora_process and self.aurora_process.stdin:
                try:
                    self.aurora_process.stdin.write(cmd + "\n")
                    self.aurora_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Aurora stdin write error: {e}. Restarting host...")
                    self._start_aurora_host()
                    time.sleep(0.8)
                    try:
                        if self.aurora_process and self.aurora_process.stdin:
                            self.aurora_process.stdin.write(cmd + "\n")
                            self.aurora_process.stdin.flush()
                    except Exception:
                        pass

    def _send_nothing(self, cmd: str):
        with self._lock:
            if not self.nothing_process or self.nothing_process.poll() is not None:
                self._start_nothing_host()
                time.sleep(0.8)

            if self.nothing_process and self.nothing_process.stdin:
                try:
                    self.nothing_process.stdin.write(cmd + "\n")
                    self.nothing_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Nothing stdin write error: {e}. Restarting host...")
                    self._start_nothing_host()
                    time.sleep(0.8)
                    try:
                        if self.nothing_process and self.nothing_process.stdin:
                            self.nothing_process.stdin.write(cmd + "\n")
                            self.nothing_process.stdin.flush()
                    except Exception:
                        pass

    def set_nothing_accent(self, value: str):
        """Pushes a Nothing LED color live (preset name or #RRGGBB)."""
        if not self._is_nothing():
            return  # setting is saved by the caller; applied on next spawn
        self._send_nothing(f"accent {value}")

    def set_mercury_accent(self, value: str):
        """Pushes a Mercury reflection color live (preset name or #RRGGBB)."""
        if not self._is_mercury():
            return  # setting is saved by the caller; applied on next spawn
        self._send_mercury(f"accent {value}")

    def set_orbit_accent(self, value: str):
        """Pushes an Orbit colour live (preset name only)."""
        if value not in ("ion", "solar", "plasma", "mint", "mono"):
            return
        if not self._is_orbit():
            return  # setting is saved by the caller; applied on next spawn
        self._send_orbit(f"accent {value}")

    def _send_mercury(self, cmd: str):
        with self._lock:
            if not self.mercury_process or self.mercury_process.poll() is not None:
                self._start_mercury_host()
                time.sleep(0.8)

            if self.mercury_process and self.mercury_process.stdin:
                try:
                    self.mercury_process.stdin.write(cmd + "\n")
                    self.mercury_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Mercury stdin write error: {e}. Restarting host...")
                    self._start_mercury_host()
                    time.sleep(0.8)
                    try:
                        if self.mercury_process and self.mercury_process.stdin:
                            self.mercury_process.stdin.write(cmd + "\n")
                            self.mercury_process.stdin.flush()
                    except Exception:
                        pass

    def _send_orbit(self, cmd: str):
        with self._lock:
            if not self.orbit_process or self.orbit_process.poll() is not None:
                self._start_orbit_host()
                time.sleep(0.8)

            if self.orbit_process and self.orbit_process.stdin:
                try:
                    self.orbit_process.stdin.write(cmd + "\n")
                    self.orbit_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Orbit stdin write error: {e}. Restarting host...")
                    self._start_orbit_host()
                    time.sleep(0.8)
                    try:
                        if self.orbit_process and self.orbit_process.stdin:
                            self.orbit_process.stdin.write(cmd + "\n")
                            self.orbit_process.stdin.flush()
                    except Exception:
                        pass

    def _send_scribe(self, cmd: str):
        with self._lock:
            if not self.scribe_process or self.scribe_process.poll() is not None:
                self._start_scribe_host()
                time.sleep(0.8)

            if self.scribe_process and self.scribe_process.stdin:
                try:
                    self.scribe_process.stdin.write(cmd + "\n")
                    self.scribe_process.stdin.flush()
                except Exception as e:
                    print(f"[OverlayPill] Scribe stdin write error: {e}. Restarting host...")
                    self._start_scribe_host()
                    time.sleep(0.8)
                    try:
                        if self.scribe_process and self.scribe_process.stdin:
                            self.scribe_process.stdin.write(cmd + "\n")
                            self.scribe_process.stdin.flush()
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

    def _is_quotal(self):
        return self._style in QUOTAL_STYLES

    def _is_aurora(self):
        return self._style in AURORA_STYLES

    def _is_nothing(self):
        return self._style in NOTHING_STYLES

    def _is_mercury(self):
        return self._style in MERCURY_STYLES

    def _is_orbit(self):
        return self._style in ORBIT_STYLES

    def _is_scribe(self):
        return self._style in SCRIBE_STYLES

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
        """Hides the capsule pill only if its host is already running (never starts it)."""
        proc = self.wispr_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_quotal_quiet(self):
        """Hides the Quotal pill only if its host is already running (never starts it)."""
        proc = self.quotal_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_aurora_quiet(self):
        """Hides the Aurora pill only if its host is already running (never starts it)."""
        proc = self.aurora_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_nothing_quiet(self):
        """Hides the Nothing pill only if its host is already running (never starts it)."""
        proc = self.nothing_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_mercury_quiet(self):
        """Hides the Mercury pill only if its host is already running (never starts it)."""
        proc = self.mercury_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_orbit_quiet(self):
        """Hides the Orbit pill only if its host is already running (never starts it)."""
        proc = self.orbit_process
        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                with self._lock:
                    proc.stdin.write("hide\n")
                    proc.stdin.flush()
            except Exception:
                pass

    def _hide_scribe_quiet(self):
        """Hides the Scribe pill only if its host is already running (never starts it)."""
        proc = self.scribe_process
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
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            # self._hide_native_quiet()
            # self._hide_cs_quiet()
            # self._hide_pulse_quiet()
            self._send_wispr("show listening")
        elif self._is_quotal():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_quotal("show listening")
        elif self._is_aurora():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_aurora("show listening")
        elif self._is_nothing():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_nothing("show listening")
        elif self._is_mercury():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_mercury("show listening")
        elif self._is_orbit():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_orbit("show listening")
        elif self._is_opus():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_opus("show listening")
        elif self._is_scribe():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_scribe("show listening")
        elif self._is_neon():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_neon("show listening")
        elif self._is_flipboard():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_flipboard("show listening")
        elif self._is_receipt():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_receipt("show listening")
        elif self._is_face():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_cassette_quiet()
            self._send_face("show listening")
        elif self._is_cassette():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_crt_quiet()
            self._send_cassette("show listening")
        elif self._is_crt():
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_crt("show listening")
        else:
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._hide_crt_quiet()
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
            elif self._is_quotal():
                self._send_quotal(f"level {level:.2f}")
            elif self._is_aurora():
                self._send_aurora(f"level {level:.2f}")
            elif self._is_nothing():
                self._send_nothing(f"level {level:.2f}")
            elif self._is_mercury():
                self._send_mercury(f"level {level:.2f}")
            elif self._is_orbit():
                self._send_orbit(f"level {level:.2f}")
            elif self._is_scribe() or self._is_opus() or self._is_neon() or self._is_flipboard() or self._is_receipt() or self._is_face() or self._is_cassette() or self._is_crt():
                pass  # Scribe, Opus, Neon, Flipboard, Receipt, Face, Cassette, CRT read audio levels locally
            else:
                self._send(f"level {level:.2f}")

    def set_text(self, text: str):
        """Streams live partial or final transcript text to caption-capable pills."""
        clean = (text or "").replace("\n", " ").replace("\r", " ").strip()
        if self._is_scribe():
            self._send_scribe(f"text {clean}")
        elif self._is_opus():
            self._send_opus(f"text {clean}")
        elif self._is_neon():
            self._send_neon(f"text {clean}")
        elif self._is_flipboard():
            self._send_flipboard(f"text {clean}")
        elif self._is_receipt():
            self._send_receipt(f"text {clean}")
        elif self._is_face():
            self._send_face(f"text {clean}")
        elif self._is_cassette():
            self._send_cassette(f"text {clean}")
        elif self._is_crt():
            self._send_crt(f"text {clean}")

    def set_crt_case(self, value: str):
        """Saves the CRT pill case and pushes it live (no restart)."""
        v = (value or "walnut").strip().lower()
        if v not in ("walnut", "mint", "graphite"):
            return
        s = settings_manager.load_settings()
        s["crt_case"] = v
        settings_manager.save_settings(s)
        if not self._is_crt():
            return
        self._send_crt(f"case {v}")

    def set_crt_phosphor(self, value: str):
        """Saves the CRT pill phosphor and pushes it live (no restart)."""
        v = (value or "amber").strip().lower()
        if v not in ("amber", "green", "white"):
            return
        s = settings_manager.load_settings()
        s["crt_phosphor"] = v
        settings_manager.save_settings(s)
        if not self._is_crt():
            return
        self._send_crt(f"phosphor {v}")

    def set_cassette_body(self, value: str):
        """Saves the Cassette pill body and pushes it live (no restart)."""
        v = (value or "orange").strip().lower()
        if v not in ("orange", "black", "cream"):
            return
        s = settings_manager.load_settings()
        s["cassette_body"] = v
        settings_manager.save_settings(s)
        if not self._is_cassette():
            return
        self._send_cassette(f"body {v}")

    def set_receipt_body(self, value: str):
        """Saves the Receipt pill body and pushes it live (no restart)."""
        v = (value or "cream").strip().lower()
        if v not in ("cream", "graphite"):
            return
        s = settings_manager.load_settings()
        s["receipt_body"] = v
        settings_manager.save_settings(s)
        if not self._is_receipt():
            return
        self._send_receipt(f"body {v}")



    def set_neon_theme(self, value: str):
        """Saves the Neon pill theme and pushes it live (no restart)."""
        v = (value or "cyber").strip().lower()
        if v not in ("sunset", "cyber", "toxic", "rose", "ice"):
            return
        s = settings_manager.load_settings()
        s["neon_theme"] = v
        settings_manager.save_settings(s)
        if not self._is_neon():
            return
        self._send_neon(f"theme {v}")

    def set_flipboard_style(self, value: str):
        """Saves the Flipboard pill style and pushes it live (no restart)."""
        v = (value or "amber").strip().lower()
        if v not in ("classic", "amber", "mint", "ice"):
            return
        s = settings_manager.load_settings()
        s["flipboard_style"] = v
        settings_manager.save_settings(s)
        if not self._is_flipboard():
            return
        self._send_flipboard(f"style {v}")

    def set_opus_accent(self, value: str):
        """Saves the Opus pill accent color and pushes it live (no restart)."""
        v = (value or "orange").strip().lower()
        if v not in ("orange", "ice", "violet", "mint", "rose"):
            return
        s = settings_manager.load_settings()
        s["opus_accent"] = v
        settings_manager.save_settings(s)
        if not self._is_opus():
            return
        self._send_opus(f"accent {v}")

    def set_scribe_accent(self, value: str):
        """Saves the Scribe pill accent color and pushes it live (no restart)."""
        v = str(value or "").strip().lower()
        if v not in ("orange", "ice", "violet", "mint", "rose"):
            v = "orange"
        try:
            import settings_manager
            s = settings_manager.load_settings()
            s["scribe_accent"] = v
            settings_manager.save_settings(s)
        except Exception:
            pass
        if not self._is_scribe():
            return
        self._send_scribe(f"accent {v}")

    def set_style(self, style: str):
        """Switches the pill visual theme (kept: bloom, wispr, quotal, quotal-light, aurora, nothing, mercury, orbit, scribe; commented out: orb, ember, meter, halo, nova, pulse, bloomcs)."""
        # if style in CS_STYLES and not self._cs_available():
        #     print("[OverlayPill] bloomgl_host.py missing, falling back to web bloom.")
        #     style = "bloom"
        if style not in ("bloom", "wispr", "quotal", "quotal-light", "aurora", "nothing", "mercury", "orbit", "scribe", "opus", "neon", "flipboard", "receipt", "face", "cassette", "crt"):
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
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            # self._hide_native_quiet()
            # self._hide_cs_quiet()
            # self._hide_pulse_quiet()
            self._send_wispr(f"theme {style}")
        elif style in QUOTAL_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_quotal(f"theme {style}")
        elif style in AURORA_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_aurora(f"theme {style}")
        elif style in NOTHING_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_nothing(f"theme {style}")
        elif style in MERCURY_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_mercury(f"theme {style}")
        elif style in ORBIT_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_orbit(f"theme {style}")
        elif style in OPUS_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_opus(f"theme {style}")
        elif style in SCRIBE_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._send_scribe(f"theme {style}")
        elif style in NEON_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            try:
                s = settings_manager.load_settings()
                nth = s.get("neon_theme", "cyber")
            except Exception:
                nth = "cyber"
            self._send_neon(f"theme {nth}")
        elif style in FLIPBOARD_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            try:
                s = settings_manager.load_settings()
                fst = s.get("flipboard_style", "amber")
            except Exception:
                fst = "amber"
            self._send_flipboard(f"style {fst}")
        elif style in RECEIPT_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            try:
                s = settings_manager.load_settings()
                rb = s.get("receipt_body", "cream")
            except Exception:
                rb = "cream"
            self._send_receipt(f"body {rb}")
        elif style in FACE_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_cassette_quiet()
        elif style in CASSETTE_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_crt_quiet()
            try:
                s = settings_manager.load_settings()
                cb = s.get("cassette_body", "orange")
            except Exception:
                cb = "orange"
            self._send_cassette(f"body {cb}")
        elif style in CRT_STYLES:
            self._hide_web_quiet()
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            try:
                s = settings_manager.load_settings()
                cc = s.get("crt_case", "walnut")
                cp = s.get("crt_phosphor", "amber")
            except Exception:
                cc, cp = "walnut", "amber"
            self._send_crt(f"case {cc}")
            self._send_crt(f"phosphor {cp}")
        else:
            self._hide_wispr_quiet()
            self._hide_quotal_quiet()
            self._hide_aurora_quiet()
            self._hide_nothing_quiet()
            self._hide_mercury_quiet()
            self._hide_orbit_quiet()
            self._hide_scribe_quiet()
            self._hide_opus_quiet()
            self._hide_neon_quiet()
            self._hide_flipboard_quiet()
            self._hide_receipt_quiet()
            self._hide_face_quiet()
            self._hide_cassette_quiet()
            self._hide_crt_quiet()
            self._send(f"theme {style}")

    def set_processing(self):
        """Switches the fluid orb to transcribing/enhancing mode (purple fluid energy)."""
        if self._is_wispr():
            self._send_wispr("state transcribing")
        elif self._is_quotal():
            self._send_quotal("state transcribing")
        elif self._is_aurora():
            self._send_aurora("state transcribing")
        elif self._is_nothing():
            self._send_nothing("state transcribing")
        elif self._is_mercury():
            self._send_mercury("state transcribing")
        elif self._is_orbit():
            self._send_orbit("state transcribing")
        elif self._is_scribe():
            self._send_scribe("state transcribing")
        elif self._is_opus():
            self._send_opus("state transcribing")
        elif self._is_neon():
            self._send_neon("state transcribing")
        elif self._is_flipboard():
            self._send_flipboard("state transcribing")
        elif self._is_receipt():
            self._send_receipt("state transcribing")
        elif self._is_face():
            self._send_face("state transcribing")
        elif self._is_cassette():
            self._send_cassette("state transcribing")
        elif self._is_crt():
            self._send_crt("state transcribing")
        else:
            self._send("state transcribing")

    def set_done(self):
        """Displays green animated checkmark and Pasted/Done state."""
        if self._is_wispr():
            self._send_wispr("state done")
        elif self._is_quotal():
            self._send_quotal("state done")
        elif self._is_aurora():
            self._send_aurora("state done")
        elif self._is_nothing():
            self._send_nothing("state done")
        elif self._is_mercury():
            self._send_mercury("state done")
        elif self._is_orbit():
            self._send_orbit("state done")
        elif self._is_scribe():
            self._send_scribe("state done")
        elif self._is_opus():
            self._send_opus("state done")
        elif self._is_neon():
            self._send_neon("state done")
        elif self._is_flipboard():
            self._send_flipboard("state done")
        elif self._is_receipt():
            self._send_receipt("state done")
        elif self._is_face():
            self._send_face("state done")
        elif self._is_cassette():
            self._send_cassette("state done")
        elif self._is_crt():
            self._send_crt("state done")
        else:
            self._send("state done")

    def set_error(self):
        """Displays red error state."""
        if self._is_wispr():
            self._send_wispr("state error")
        elif self._is_quotal():
            self._send_quotal("state error")
        elif self._is_aurora():
            self._send_aurora("state error")
        elif self._is_nothing():
            self._send_nothing("state error")
        elif self._is_mercury():
            self._send_mercury("state error")
        elif self._is_orbit():
            self._send_orbit("state error")
        elif self._is_scribe():
            self._send_scribe("state error")
        elif self._is_opus():
            self._send_opus("state error")
        elif self._is_neon():
            self._send_neon("state error")
        elif self._is_flipboard():
            self._send_flipboard("state error")
        elif self._is_receipt():
            self._send_receipt("state error")
        elif self._is_face():
            self._send_face("state error")
        elif self._is_cassette():
            self._send_cassette("state error")
        elif self._is_crt():
            self._send_crt("state error")
        else:
            self._send("state error")

    def hide(self):
        """Smoothly conceals the floating pill."""
        self._is_visible = False
        if self._is_wispr():
            self._send_wispr("hide")
        elif self._is_quotal():
            self._send_quotal("hide")
        elif self._is_aurora():
            self._send_aurora("hide")
        elif self._is_nothing():
            self._send_nothing("hide")
        elif self._is_mercury():
            self._send_mercury("hide")
        elif self._is_orbit():
            self._send_orbit("hide")
        elif self._is_scribe():
            self._send_scribe("hide")
        elif self._is_opus():
            self._send_opus("hide")
        elif self._is_neon():
            self._send_neon("hide")
        elif self._is_flipboard():
            self._send_flipboard("hide")
        elif self._is_receipt():
            self._send_receipt("hide")
        elif self._is_face():
            self._send_face("hide")
        elif self._is_cassette():
            self._send_cassette("hide")
        elif self._is_crt():
            self._send_crt("hide")
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
        qtp = self.quotal_process
        if qtp is not None and qtp.poll() is None:
            try:
                qtp.stdin.write("quit\n")
                qtp.stdin.flush()
            except Exception:
                pass
            try:
                qtp.terminate()
            except Exception:
                pass
        aup = self.aurora_process
        if aup is not None and aup.poll() is None:
            try:
                aup.stdin.write("quit\n")
                aup.stdin.flush()
            except Exception:
                pass
            try:
                aup.terminate()
            except Exception:
                pass
        nop = self.nothing_process
        if nop is not None and nop.poll() is None:
            try:
                nop.stdin.write("quit\n")
                nop.stdin.flush()
            except Exception:
                pass
            try:
                nop.terminate()
            except Exception:
                pass
        mep = self.mercury_process
        if mep is not None and mep.poll() is None:
            try:
                mep.stdin.write("quit\n")
                mep.stdin.flush()
            except Exception:
                pass
            try:
                mep.terminate()
            except Exception:
                pass
        orp = self.orbit_process
        if orp is not None and orp.poll() is None:
            try:
                orp.stdin.write("quit\n")
                orp.stdin.flush()
            except Exception:
                pass
            try:
                orp.terminate()
            except Exception:
                pass
        opp = self.opus_process
        if opp:
            try:
                opp.terminate()
            except Exception:
                pass
        scp = self.scribe_process
        if scp is not None and scp.poll() is None:
            try:
                scp.stdin.write("quit\n")
                scp.stdin.flush()
            except Exception:
                pass
            try:
                scp.terminate()
            except Exception:
                pass
        nep = self.neon_process
        if nep is not None and nep.poll() is None:
            try:
                nep.stdin.write("quit\n")
                nep.stdin.flush()
            except Exception:
                pass
            try:
                nep.terminate()
            except Exception:
                pass
        flp = self.flipboard_process
        if flp is not None and flp.poll() is None:
            try:
                flp.stdin.write("quit\n")
                flp.stdin.flush()
            except Exception:
                pass
            try:
                flp.terminate()
            except Exception:
                pass
        rcp = self.receipt_process
        if rcp is not None and rcp.poll() is None:
            try:
                rcp.stdin.write("quit\n")
                rcp.stdin.flush()
            except Exception:
                pass
            try:
                rcp.terminate()
            except Exception:
                pass
        fcp = self.face_process
        if fcp is not None and fcp.poll() is None:
            try:
                fcp.stdin.write("quit\n")
                fcp.stdin.flush()
            except Exception:
                pass
            try:
                fcp.terminate()
            except Exception:
                pass
        csp_proc = self.cassette_process
        if csp_proc is not None and csp_proc.poll() is None:
            try:
                csp_proc.stdin.write("quit\n")
                csp_proc.stdin.flush()
            except Exception:
                pass
            try:
                csp_proc.terminate()
            except Exception:
                pass
            except Exception:
                pass
        crtp = self.crt_process
        if crtp is not None and crtp.poll() is None:
            try:
                crtp.stdin.write("quit\n")
                crtp.stdin.flush()
            except Exception:
                pass
            try:
                crtp.terminate()
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
