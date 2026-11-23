"""
settings_manager.py - Persistent user preferences for WinVoice.
Stores model choice, trigger key, and enhancer toggles.
"""

import os
import json

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "model_key": "base",  # "base", "hinglish_swift", "small", "turbo"
    "selected_languages": ["en"],  # list of language codes e.g. ["en", "hi", "mr"]
    "device": "auto",      # "auto" (probe GPU, fall back to CPU) | "cuda" | "cpu"
    "ai_enhance": True,
    "enhancer_mode": "rules",  # "rules", "hybrid", "llm", "off"
    "enhancer_style": "default",
    "strip_hesitations": True,
    "audio_chimes": True,
    "paste_mode": "direct",  # "direct" (paste at cursor) | "clipboard" (copy to clipboard only)
    "pill_x": None,
    "pill_y": None,
    "pill_style": "bloom",  # "bloom", "wispr", "quotal", "quotal-light", "aurora", "nothing", "mercury", "orbit", "scribe", "opus", "neon", "flipboard", "receipt", "face", "cassette", "crt" (commented out: "orb", "ember", "meter", "halo", "nova", "pulse", "bloomcs")
    "orbit_accent": "ion",  # Orbit pill colour: ion | solar | plasma | mint | mono
    "mercury_accent": "orange",  # Mercury pill reflection: preset name or hex #RRGGBB
    "nothing_accent": "white",  # Nothing pill LEDs: preset name or hex #RRGGBB
    "scribe_accent": "orange",  # Scribe pill accent: orange | ice | violet | mint | rose
    "opus_accent": "orange",  # Opus pill accent: orange | ice | violet | mint | rose
    "neon_theme": "cyber",    # Neon pill theme: sunset | cyber | toxic | rose | ice
    "flipboard_style": "amber",  # Flipboard pill style: classic | amber | mint | ice
    "receipt_body": "cream",  # Receipt pill body: cream | graphite
    "cassette_body": "orange",  # Cassette pill body: orange | black | cream
    "crt_case": "walnut",      # CRT pill case: walnut | mint | graphite
    "crt_phosphor": "amber",   # CRT pill phosphor: amber | green | white
    "theme": "dark",        # "dark" or "light"
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
    },
    "turbo": {
        "name": "Whisper Large Turbo (800 MB - Best Multilingual, 99+ Languages)",
        "path": "large-v3-turbo",
        "device": "auto",
        "compute_type": "auto"
    },
    "omnilingual": {
        "name": "Omnilingual ASR (328 MB - 1,600+ Languages, Ultra Fast CPU)",
        "path": "models/omnilingual-asr-ctc",
        "device": "cpu",
        "compute_type": "int8"
    },
    "hima": {
        "name": "Hima ASR (550 MB - 12 Indian Languages & Hinglish)",
        "path": "models/hima-asr-onnx",
        "device": "auto",
        "compute_type": "int8"
    },
    "nemotron": {
        "name": "NVIDIA Nemotron 3.5 Streaming (0.6B - Vulkan GPU & Live Preview)",
        "path": "nemotron",
        "device": "auto",
        "compute_type": "q8_0"
    },
    "parakeet_v3": {
        "name": "NVIDIA Parakeet TDT v3 (0.6B - Ultra Fast Streaming, 4.8% WER)",
        "path": "parakeet_v3",
        "device": "auto",
        "compute_type": "q8_0"
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
