"""
history_manager.py - Local persistence for Quotal dictations.
Saves history safely to a JSON Lines file (history.jsonl) with high-efficiency
tail-reading and automatic size-rotation safeguards.
"""

import os
import json
import time
import threading
from datetime import datetime
from typing import List, Dict, Union, Optional

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.jsonl")
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB maximum before compaction
_history_lock = threading.Lock()


def _read_all_raw() -> List[Dict]:
    """Read all entries from history.jsonl in chronological order."""
    if not os.path.exists(HISTORY_FILE):
        return []
    entries = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    except Exception as e:
        print(f"[History Read Error] {e}")
    return entries


def _write_all_raw(entries: List[Dict]):
    """Safely rewrite history.jsonl using an atomic file replacement."""
    tmp_file = HISTORY_FILE + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            for item in entries:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        if os.path.exists(HISTORY_FILE):
            os.replace(tmp_file, HISTORY_FILE)
        else:
            os.rename(tmp_file, HISTORY_FILE)
    except Exception as e:
        print(f"[History Write Error] {e}")
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass


def _rotate_if_needed():
    """Compacts history file if it exceeds MAX_FILE_BYTES, retaining the newest 2,000 entries."""
    try:
        if os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > MAX_FILE_BYTES:
            entries = get_recent(limit=2000)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                for item in reversed(entries):
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[History Rotation Warning] {e}")


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
        with _history_lock:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _rotate_if_needed()
    except Exception as e:
        print(f"[History Error] {e}")

    return entry


def get_recent(limit: int = 50) -> List[Dict]:
    """Retrieve the most recent dictation entries by reading from the end of the file."""
    if not os.path.exists(HISTORY_FILE):
        return []

    entries = []
    try:
        with _history_lock:
            file_size = os.path.getsize(HISTORY_FILE)
            if file_size == 0:
                return []

            # Seek from end of file so we only read and parse what's needed
            with open(HISTORY_FILE, "rb") as f:
                chunk_size = min(file_size, max(8192, limit * 600))
                f.seek(max(0, file_size - chunk_size))
                data = f.read().decode("utf-8", errors="ignore")
                lines = data.splitlines()

                # If the chunk didn't yield enough lines and more data exists, read full
                if len(lines) < limit and chunk_size < file_size:
                    f.seek(0)
                    lines = f.read().decode("utf-8", errors="ignore").splitlines()

                for line in reversed(lines):
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                            if len(entries) >= limit:
                                break
                        except Exception:
                            pass
    except Exception as e:
        print(f"[History Error] {e}")

    return entries


def delete_entry(epoch: Optional[Union[float, int, str]] = None, timestamp: Optional[str] = None) -> bool:
    """Delete a single dictation entry by epoch or timestamp."""
    if epoch is None and timestamp is None:
        return False

    with _history_lock:
        entries = _read_all_raw()
        target_str = str(epoch).strip() if epoch is not None else None
        new_entries = []
        found = False

        for e in entries:
            e_epoch = e.get("epoch")
            e_epoch_str = str(e_epoch).strip() if e_epoch is not None else ""
            
            # Check epoch match (exact string or float match)
            is_match = False
            if target_str:
                if e_epoch_str == target_str:
                    is_match = True
                elif e_epoch is not None:
                    try:
                        if abs(float(e_epoch) - float(epoch)) < 0.0001:
                            is_match = True
                    except Exception:
                        pass
            elif timestamp and e.get("timestamp") == timestamp:
                is_match = True

            if is_match and not found:
                found = True
                continue
            new_entries.append(e)

        if found:
            _write_all_raw(new_entries)
            return True
        return False


def delete_entries(epochs: List[Union[float, int, str]]) -> int:
    """Delete multiple dictation entries matching any of the specified epochs."""
    if not epochs:
        return 0

    target_set = {str(x).strip() for x in epochs}
    float_targets = []
    for x in epochs:
        try:
            float_targets.append(float(x))
        except Exception:
            pass

    with _history_lock:
        entries = _read_all_raw()
        new_entries = []
        deleted_count = 0

        for e in entries:
            e_epoch = e.get("epoch")
            e_epoch_str = str(e_epoch).strip() if e_epoch is not None else ""
            
            is_match = (e_epoch_str in target_set) or (bool(e.get("timestamp")) and e.get("timestamp", "").strip() in target_set)
            if not is_match and e_epoch is not None and float_targets:
                try:
                    ef = float(e_epoch)
                    if any(abs(ef - ft) < 0.0001 for ft in float_targets):
                        is_match = True
                except Exception:
                    pass

            if is_match:
                deleted_count += 1
                continue
            new_entries.append(e)

        if deleted_count > 0:
            _write_all_raw(new_entries)
        return deleted_count


def delete_day(date_prefix: str) -> int:
    """Delete all dictations from a specific date prefix (e.g. '2026-10-05')."""
    if not date_prefix:
        return 0

    clean_prefix = str(date_prefix).strip()
    with _history_lock:
        entries = _read_all_raw()
        new_entries = []
        deleted_count = 0

        for e in entries:
            ts = e.get("timestamp", "").strip()
            if ts.startswith(clean_prefix):
                deleted_count += 1
                continue
            new_entries.append(e)

        if deleted_count > 0:
            _write_all_raw(new_entries)
        return deleted_count


def clear_history():
    """Clear history file."""
    with _history_lock:
        if os.path.exists(HISTORY_FILE):
            try:
                os.remove(HISTORY_FILE)
            except Exception:
                pass
