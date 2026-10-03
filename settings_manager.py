"""
settings_manager.py - Persistent user preferences for WinVoice.
Stores model choice, trigger key, and enhancer toggles.
"""

import os
import json

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "model_key": "base",  # "base", "hinglish_swift", "small"
    "device": "cpu",       # "cpu" or "cuda"
    "ai_enhance": True,
    "enhancer_mode": "rules",  # "rules" (instant), "llm" (qwen 0.5b), "off"
    "strip_hesitations": True,
    "audio_chimes": True,
    "pill_x": None,
    "pill_y": None,
}

MODEL_CONFIGS = {
    "base": {
        "name": "Whisper Base (75 MB - Fast, Everyday English)",
        "path": "base",
        "device": "auto",
        "compute_type": "auto"
    },
    "hinglish_swift": {
        "name": "Hinglish Swift (145 MB - Romanized Hindi)",
        "path": "models/hinglish-swift-ct2",
        "device": "auto",
        "compute_type": "auto"
    },
    "small": {
        "name": "Whisper Small (240 MB - High Accuracy)",
        "path": "small",
        "device": "auto",
        "compute_type": "auto"
    }
}


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = DEFAULT_SETTINGS.copy()
                merged.update(data)
                return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"[Settings Error] {e}")
