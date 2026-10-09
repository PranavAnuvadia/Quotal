"""
wispr_qt_bridge.py - Engine adapter for the Wispr pill (Quotal side).

`wispr_pill.py` is the author's file, kept VERBATIM (do not edit it).
This bridge is OUR code: it imports Pill/AudioSource untouched and only
translates Quotal's host protocol into the pill's public API:

    show listening   -> window visible + state "recording"
    state transcribing -> state "processing" (spinner, while AI polishes)
    state done/error -> state "idle" (text pasted / failed)
    hide             -> state "idle" + window concealed
    level/theme      -> ignored (the pill reads its own mic for loudness)
    quit             -> exit

Stdin commands run on a background thread; everything touching Qt is
marshalled onto the GUI thread via QTimer.singleShot(0, ...) plus the
pill's own thread-safe `state_request` signal.

Usage:
    python wispr_qt_bridge.py          # real microphone (via wispr_pill)
    python wispr_qt_bridge.py --demo   # fake speech, no mic needed
"""

import sys
import os
import threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    os.chdir(_BASE_DIR)
except Exception:
    pass
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

import settings_manager
from wispr_pill import AudioSource, Pill  # noqa: F401  (author's file, verbatim)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "wispr_bridge.log")


def log(msg):
    try:
        import time as _t
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{_t.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


class Commander(QObject):
    """ stdin thread -> GUI thread via queued signal (never touch Qt
    widgets from the worker thread: QTimer.singleShot created there would
    never fire, since Qt timers live in the calling thread). """
    cmd = Signal(str, str)


def _save_pos(pill):
    try:
        s = settings_manager.load_settings()
        s["pill_x"] = pill.x()
        s["pill_y"] = pill.y()
        settings_manager.save_settings(s)
    except Exception:
        pass


def _work_area(pill):
    """Current screen work area in native pixels (taskbar excluded)."""
    try:
        g = pill.screen().availableGeometry()
        return g.left(), g.top(), g.right() + 1, g.bottom() + 1
    except Exception:
        pass
    try:
        from PySide6.QtGui import QGuiApplication
        g = QGuiApplication.primaryScreen().availableGeometry()
        return g.left(), g.top(), g.right() + 1, g.bottom() + 1
    except Exception:
        return 0, 0, 1920, 1040


def _restore_pos(pill, placed):
    try:
        s = settings_manager.load_settings()
        cx, cy = s.get("pill_x"), s.get("pill_y")
    except Exception:
        cx, cy = None, None
    l, t, r, b = _work_area(pill)
    W, H = pill.width(), pill.height()
    if cx is not None and cy is not None and not placed["done"]:
        # Saved coords may be off-screen now (monitor/DPI/taskbar changed):
        # clamp into the current work area like the other pill hosts do.
        try:
            x = max(l + 4, min(r - W - 4, int(cx)))
            y = max(t + 4, min(b - H - 4, int(cy)))
            pill.move(x, y)
            log(f"restored clamped pos ({x},{y}) from ({cx},{cy})")
        except Exception as exc:
            log(f"restore pos FAILED: {exc!r}")
    elif not placed["done"]:
        try:
            pill.place_default()
        except Exception:
            pass
    placed["done"] = True


def main():
    log("bridge starting...")
    demo = "--demo" in sys.argv[1:]
    qt_args = [a for a in sys.argv[1:] if a != "--demo"]

    try:
        app = QApplication([sys.argv[0]] + qt_args)
    except Exception as exc:
        log(f"QApplication failed: {exc!r}")
        raise
    log("QApplication ready")
    audio = AudioSource(demo=demo)
    app.aboutToQuit.connect(audio.stop)

    # auto_finish=0: the engine decides when polishing is done, not a timer.
    pill = Pill(audio, auto_finish=0)
    placed = {"done": False}
    _restore_pos(pill, placed)
    pill.hide()

    commander = Commander()

    def apply(cmd, arg):
        try:
            _apply(cmd, arg)
        except Exception as exc:
            log(f"apply {cmd} {arg} FAILED: {exc!r}")

    def _apply(cmd, arg):
        log(f"apply: {cmd} {arg}")
        if cmd == "show":
            _restore_pos(pill, placed)
            pill.show()
            pill.raise_()
            pill.set_state("recording")
        elif cmd == "state":
            if arg == "transcribing":
                pill.set_state("processing")
            elif arg in ("done", "error", "listening"):
                pill.set_state("recording" if arg == "listening" else "idle")
        elif cmd == "hide":
            pill.set_state("idle")
            _save_pos(pill)
            pill.hide()
        elif cmd == "quit":
            app.quit()
            return
        # "level" / "theme" intentionally ignored: loudness comes from the
        # pill's own mic via wispr_pill.AudioSource.

    commander.cmd.connect(apply)  # cross-thread emit -> queued on GUI thread
    log("pill hidden, stdin loop starting")

    def stdin_loop():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            log(f"recv: {line}")
            parts = line.split(" ", 1)
            cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
            if cmd in ("show", "state", "hide", "level", "theme", "quit"):
                commander.cmd.emit(cmd, arg)
                if cmd == "quit":
                    break
        log("stdin loop ended")

    threading.Thread(target=stdin_loop, daemon=True).start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
