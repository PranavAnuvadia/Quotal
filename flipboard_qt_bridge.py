"""
flipboard_qt_bridge.py - Engine adapter for the FLIPBOARD live caption pill (Quotal side).

`flipboard_pill.py` provides the split-flap board Pill widget with mechanical flaps, lamps, and uppercase live transcript.
This bridge translates Quotal's overlay commands into the pill's public API:

    show listening       -> window visible + state "recording" + blank text
    text <string>        -> live transcript preview update
    state transcribing   -> state "processing" (enhancing wave)
    state done           -> state "idle"
    state error/idle     -> state "idle"
    hide                 -> state "idle" + window hidden
    style <name>         -> sets style ("classic", "amber", "mint", "ice")
    accent <name>        -> sets style
    theme <name>         -> sets style
    quit                 -> exit

Stdin commands run on a background thread; everything touching Qt is
marshalled onto the GUI thread via queued Commander signal.
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

from PySide6.QtCore import QObject, Qt, Signal, QTimer
from PySide6.QtWidgets import QApplication

import settings_manager
from flipboard_pill import AudioSource, Pill

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flipboard_bridge.log")


def log(msg):
    try:
        import time as _t
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{_t.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


class Commander(QObject):
    """stdin thread -> GUI thread via queued signal."""
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
        try:
            x = max(l + 4, min(r - W - 4, int(cx)))
            y = max(t + 4, min(b - H - 4, int(cy)))
            pill.move(x, y)
        except Exception as exc:
            log(f"restore pos failed: {exc!r}")
    elif not placed["done"]:
        try:
            pill.place_default()
        except Exception:
            pass
    placed["done"] = True


def main():
    log("flipboard bridge starting...")
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    demo = "--demo" in sys.argv[1:]
    qt_args = [a for a in sys.argv[1:] if a != "--demo"]

    try:
        app = QApplication([sys.argv[0]] + qt_args)
    except Exception as exc:
        log(f"QApplication failed: {exc!r}")
        raise

    try:
        s = settings_manager.load_settings()
        style = s.get("flipboard_style", "amber")
    except Exception:
        style = "amber"

    audio = AudioSource(demo=demo)
    app.aboutToQuit.connect(audio.stop)

    pill = Pill(audio, auto_finish=0, style=style)
    placed = {"done": False}
    _restore_pos(pill, placed)
    pill.hide()

    commander = Commander()

    def _apply(cmd, arg):
        if cmd == "show":
            _restore_pos(pill, placed)
            pill.text = ""
            pill.lines = []
            pill.show()
            pill.raise_()
            pill.set_state("recording")
        elif cmd == "text":
            pill.text_request.emit(arg)
        elif cmd == "state":
            if arg == "transcribing":
                pill.set_state("processing")
            elif arg in ("done", "error", "idle"):
                pill.set_state("idle")
            elif arg == "listening":
                pill.set_state("recording")
        elif cmd in ("style", "accent", "theme"):
            pill.set_style(arg)
        elif cmd == "hide":
            pill.set_state("idle")
            _save_pos(pill)
            QTimer.singleShot(260, lambda: pill.hide() if pill.state == "idle" else None)
        elif cmd == "quit":
            app.quit()

    commander.cmd.connect(_apply)

    def stdin_loop():
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            commander.cmd.emit(cmd, arg)

    t = threading.Thread(target=stdin_loop, daemon=True)
    t.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
