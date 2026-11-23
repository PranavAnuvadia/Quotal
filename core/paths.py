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

# Auto-inject project root and all component directories into sys.path
_CORE_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_CORE_DIR)

for _sub in [_BASE_DIR, _CORE_DIR,
             os.path.join(_BASE_DIR, "engine"),
             os.path.join(_BASE_DIR, "processing"),
             os.path.join(_BASE_DIR, "ui"),
             os.path.join(_BASE_DIR, "ui", "dashboard"),
             os.path.join(_BASE_DIR, "pills")]:
    if _sub not in sys.path:
        sys.path.insert(0, _sub)


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> str:
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _BASE_DIR


def asset(name: str) -> str:
    if is_frozen():
        cand = os.path.join(app_dir(), "_internal", name)
        if os.path.exists(cand):
            return cand
        cand = os.path.join(app_dir(), name)
        if os.path.exists(cand):
            return cand
    # Search specialized folders first
    for sub in ["assets", os.path.join("ui", "dashboard"), "pills"]:
        cand = os.path.join(app_dir(), sub, name)
        if os.path.exists(cand):
            return cand
    return os.path.join(app_dir(), name)
