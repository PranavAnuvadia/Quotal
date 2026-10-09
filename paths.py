"""Central install-layout paths for Quotal.

Works in both layouts:
  - dev:      plain .py files in the project root (python app.py)
  - frozen:   PyInstaller one-dir bundle (installed Quotal.exe)

Rules:
  - app_dir(): writable directory holding the exe AND user data
    (settings.json, history.jsonl, logs). Dev = project root,
    frozen = folder containing the exe (per-user install, always writable).
  - asset(): read-only files shipped with the app (pill.html,
    dashboard.html, icons). Frozen one-dir keeps them under _internal/.
"""

import os
import sys


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> str:
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def asset(name: str) -> str:
    if is_frozen():
        cand = os.path.join(app_dir(), "_internal", name)
        if os.path.exists(cand):
            return cand
        cand = os.path.join(app_dir(), name)
        if os.path.exists(cand):
            return cand
    return os.path.join(app_dir(), name)
