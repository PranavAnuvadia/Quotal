"""
history_manager.py - Local persistence for Quotal dictations.
Saves history safely to a JSON Lines file (history.jsonl).
"""

import os
import json
import time
from datetime import datetime
from typing import List, Dict

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.jsonl")


def add_entry(raw_text: str, cleaned_text: str, duration_sec: float, latency_ms: float) -> Dict:
    """Append a new dictation entry to history."""
    if not cleaned_text:
        return {}

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
        "duration_sec": round(duration_sec, 2),
        "latency_ms": round(latency_ms, 1),
        "raw": raw_text,
        "cleaned": cleaned_text,
        "word_count": len(cleaned_text.split())
    }

    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[History Error] {e}")

    return entry


def get_recent(limit: int = 50) -> List[Dict]:
    """Retrieve the most recent dictation entries."""
    if not os.path.exists(HISTORY_FILE):
        return []

    entries = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    except Exception as e:
        print(f"[History Error] {e}")

    # Return newest first
    return list(reversed(entries))[:limit]


def clear_history():
    """Clear history file."""
    if os.path.exists(HISTORY_FILE):
        try:
            os.remove(HISTORY_FILE)
        except Exception:
            pass
