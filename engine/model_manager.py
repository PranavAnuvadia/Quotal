"""
model_manager.py - Speech-model download status & background downloads for Quotal.

- Whisper Base / Small: CTranslate2 models fetched from the Hugging Face Hub
  on demand via faster-whisper's download_model() (same cache the Transcriber
  uses, so a dashboard download means instant switching later).
- Hinglish Swift: bundled in models/hinglish-swift-ct2, always available.

Downloads run in daemon threads; the dashboard polls get_models_state().
"""

import os
import shutil
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_SOURCES = {
    # key: kind + faster-whisper size id (HF download) or bundled folder (local)
    "base": {"kind": "hf", "size": "base"},
    "small": {"kind": "hf", "size": "small"},
    "turbo": {"kind": "hf", "size": "large-v3-turbo"},
    "hinglish_swift": {"kind": "local", "path": os.path.join(BASE_DIR, "models", "hinglish-swift-ct2")},
    "omnilingual": {"kind": "local", "path": os.path.join(BASE_DIR, "models", "omnilingual-asr-ctc")},
    "hima": {"kind": "local", "path": os.path.join(BASE_DIR, "models", "hima-asr-onnx")},
    "nemotron": {"kind": "local", "path": "nemotron"},
    "parakeet_v3": {"kind": "local", "path": "parakeet_v3"},
}

_lock = threading.Lock()
_downloading = {}   # key -> bool
_progress = {}      # key -> status string
_progress_pct = {}  # key -> 0..100 (None when unknown)
_dl_bytes = {}      # key -> exact bytes transferred so far
_dl_total = {}      # key -> exact bytes expected (pre-seeded, topped up per file)


def _refresh_pct(key: str) -> None:
    total = _dl_total.get(key, 0)
    done = _dl_bytes.get(key, 0)
    if total > 0:
        _progress_pct[key] = max(0, min(100, round(done / total * 100)))
        _progress[key] = f"{_progress_pct[key]}%"
    else:
        _progress_pct[key] = None


def _noop(*a, **k):
    return None


class _ByteProgress:
    """tqdm-compatible byte reporter for hf_hub_download (kwargs-only init,
    update(len(chunk)) per chunk). Aggregated per download key across files.

    `total`/`n`/`format_dict` are REAL attributes (not no-ops) because the
    Hub's XET reporter does arithmetic on them.
    """

    def __init__(self, key=None, total=None, initial=0, desc=None, **kw):
        self._key = key
        self.total = total
        try:
            self.n = float(initial or 0)
        except Exception:
            self.n = 0
        self.desc = desc
        self.format_dict = {"rate": None}
        if key is not None:
            with _lock:
                # Total is pre-seeded from the Hub file list; only aggregate
                # per-bar totals when no pre-seed exists (avoids double count).
                if total and not _dl_total.get(key):
                    _dl_total[key] = _dl_total.get(key, 0) + total
                if initial:
                    try:
                        _dl_bytes[key] = _dl_bytes.get(key, 0) + float(initial or 0)
                    except Exception:
                        pass
                _refresh_pct(key)

    def update(self, n=1):
        try:
            n = float(n or 0)
        except Exception:
            return
        try:
            self.n += n
        except Exception:
            pass
        if self._key is None:
            return
        with _lock:
            _dl_bytes[self._key] = _dl_bytes.get(self._key, 0) + n
            _refresh_pct(self._key)

    def update_transfer(self, n=1):
        # Transfer-layer bytes (post-dedup); file bytes are already counted
        # in update(), so this stays a no-op to avoid double counting.
        pass

    def set_postfix_str(self, *a, **k):
        pass

    def set_transfer_postfix_str(self, *a, **k):
        pass

    def set_description(self, *a, **k):
        pass

    def set_postfix(self, *a, **k):
        pass

    def refresh(self, *a, **k):
        pass

    def clear(self, *a, **k):
        pass

    def write(self, *a, **k):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


_WANTED_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json")


def _wanted_file(name: str) -> bool:
    return name in _WANTED_FILES or name.startswith("vocabulary")


def _repo_id(size: str) -> str | None:
    try:
        from faster_whisper.utils import _MODELS
        repo = _MODELS.get(size)
        if repo:
            return repo
    except Exception:
        pass
    return {"base": "Systran/faster-whisper-base",
            "small": "Systran/faster-whisper-small",
            "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}.get(size)


def is_downloaded(key: str) -> bool:
    """True if the model can be loaded right now without network.

    Verifies every expected file exists (snapshot resolution alone can
    succeed while a download is still in flight).
    """
    import fnmatch

    src = MODEL_SOURCES.get(key)
    if not src:
        return False
    if key in ("nemotron", "parakeet_v3"):
        try:
            import nemotron_streaming
            return nemotron_streaming.is_available(key)
        except Exception:
            return False
    if src["kind"] == "local":
        p = src["path"]
        if not os.path.isdir(p):
            return False
        if os.path.exists(os.path.join(p, "model.bin")):
            return True
        for root, _, files in os.walk(p):
            if any(f.endswith(".onnx") for f in files):
                return True
        return False
    try:
        from faster_whisper.utils import download_model
        path = download_model(src["size"], local_files_only=True)
    except Exception:
        return False
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for want in ("config.json", "model.bin", "tokenizer.json"):
        if want not in names:
            return False
    if not any(fnmatch.fnmatch(n, "vocabulary.*") for n in names):
        return False
    try:
        if os.path.getsize(os.path.join(path, "model.bin")) < 1024 * 1024:
            return False
    except OSError:
        return False
    return True


def get_model_state(key: str) -> dict:
    with _lock:
        busy = _downloading.get(key, False)
        prog = _progress.get(key, "")
        pct = _progress_pct.get(key)
    return {
        "key": key,
        "downloaded": is_downloaded(key),
        "downloading": busy,
        "progress": prog,
        "pct": pct,
    }


def get_models_state() -> list:
    return [get_model_state(k) for k in MODEL_SOURCES]


def start_download(key: str) -> bool:
    """Start background download. Returns False for unknown/bundled keys."""
    src = MODEL_SOURCES.get(key)
    if not src or src["kind"] != "hf":
        return False
    with _lock:
        if _downloading.get(key) or is_downloaded(key):
            return True
        _downloading[key] = True
        _progress[key] = "Preparing..."
        _progress_pct[key] = None

    def _worker():
        import functools
        try:
            from huggingface_hub import HfApi, hf_hub_download
            repo = _repo_id(src["size"])
            if not repo:
                raise RuntimeError(f"unknown model size '{src['size']}'")
            # Same files faster-whisper fetches, same shared HF cache layout —
            # but per-file so the byte hook reports exact progress.
            files = [f for f in HfApi().list_repo_files(repo) if _wanted_file(f)]
            if not files:
                raise RuntimeError("file list empty")
            files.sort(key=lambda f: 0 if f == "model.bin" else 1)
            with _lock:
                _dl_bytes[key] = 0
                _dl_total[key] = _expected_total_bytes(src["size"]) or 0
                _progress[key] = "Starting..."
                _progress_pct[key] = 0
            hook = functools.partial(_ByteProgress, key=key)
            for fn in files:
                hf_hub_download(repo, fn, tqdm_class=hook)
            with _lock:
                _progress_pct[key] = 100
                _progress[key] = "Download complete!"
        except Exception as e:
            with _lock:
                _progress_pct[key] = None
                _progress[key] = f"Download failed: {e}"
        finally:
            with _lock:
                _downloading[key] = False

    threading.Thread(target=_worker, daemon=True).start()
    return True


def _expected_total_bytes(size: str) -> int | None:
    """Expected download bytes for a faster-whisper size id (None if unknown)."""
    repo = _repo_id(size)
    if not repo:
        return None
    try:
        from huggingface_hub import HfApi
        info = HfApi().model_info(repo, files_metadata=True)
        total = 0
        for s in info.siblings:
            n = s.rfilename
            if n in ("config.json", "model.bin", "tokenizer.json") or n.startswith("vocabulary"):
                total += s.size or 0
        return total or None
    except Exception:
        return None


def _hf_cache_repo_dir(size: str) -> str | None:
    """Local HF cache folder for a faster-whisper size id (None if unknown)."""
    repo = None
    try:
        from faster_whisper.utils import _MODELS
        repo = _MODELS.get(size)
    except Exception:
        repo = None
    if not repo:
        repo = {"base": "Systran/faster-whisper-base",
                "small": "Systran/faster-whisper-small",
                "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}.get(size)
    if not repo:
        return None
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except Exception:
        HF_HUB_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    return os.path.join(HF_HUB_CACHE, "models--" + repo.replace("/", "--"))


def delete_model(key: str) -> dict:
    """Remove a downloaded HF model from the cache. Bundled models refuse."""
    src = MODEL_SOURCES.get(key)
    if not src or src["kind"] != "hf":
        return {"ok": False, "reason": "bundled"}
    with _lock:
        if _downloading.get(key):
            return {"ok": False, "reason": "downloading"}
    target = _hf_cache_repo_dir(src["size"])
    try:
        if target and os.path.isdir(target):
            shutil.rmtree(target)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True}
