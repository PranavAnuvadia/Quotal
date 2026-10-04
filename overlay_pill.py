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


class OverlayPill:
    def __init__(self):
        self.process = None
        self._lock = threading.Lock()
        self._is_visible = False
        self._start_host()

    def _start_host(self):
        """Launches the dedicated WebGL WebView2 host process."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            venv_quotal = os.path.join(base_dir, ".venv", "Scripts", "Quotal.exe")
            if os.path.exists(venv_quotal):
                python_exe = venv_quotal
            else:
                dir_name = os.path.dirname(sys.executable)
                quotal_exe = os.path.join(dir_name, "Quotal.exe")
                if os.path.exists(quotal_exe):
                    python_exe = quotal_exe
                else:
                    python_exe = sys.executable
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.process = subprocess.Popen(
                [python_exe, HOST_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags
            )
        except Exception as e:
            print(f"[OverlayPill] Failed to launch WebGL host: {e}")

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

    def show(self, text="Listening..."):
        """Displays the pill with the fluid orb."""
        self._is_visible = True
        self._send("show listening")

    def set_level(self, level: float):
        """Sends current microphone amplitude (0.0 to 1.0) to modulate the fluid waves."""
        if self._is_visible:
            self._send(f"level {level:.2f}")

    def set_processing(self):
        """Switches the fluid orb to transcribing/enhancing mode (purple fluid energy)."""
        self._send("state transcribing")

    def set_done(self):
        """Displays green animated checkmark and Pasted/Done state."""
        self._send("state done")

    def set_error(self):
        """Displays red error state."""
        self._send("state error")

    def hide(self):
        """Smoothly conceals the floating pill."""
        self._is_visible = False
        self._send("hide")

    def close(self):
        """Terminates the host process on app shutdown."""
        self._send("quit")
        if self.process:
            try:
                self.process.terminate()
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
