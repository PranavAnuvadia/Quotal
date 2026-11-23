"""
nemotron_streaming.py - High-performance Nemotron 3.5 ASR Streaming via transcribe.dll (Vulkan GPU / CPU).

Provides real-time incremental speech recognition with instant partial transcripts for live captioning.
"""

import os
import sys
import time
import ctypes
import threading
from typing import Optional
import numpy as np

# Discover Handy's native transcribe.dll runtime
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_HANDY = os.path.join(_BASE_DIR, "handy_runtime")
if os.path.exists(os.path.join(_LOCAL_HANDY, "transcribe.dll")):
    HANDY_DIR = _LOCAL_HANDY
else:
    HANDY_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Handy")
DLL_PATH = os.path.join(HANDY_DIR, "transcribe.dll")

import glob

def resolve_model(key_or_path: str = None) -> str:
    """Finds GGUF weights for Nemotron or Parakeet v3 in the local cache or workspace."""
    if not key_or_path:
        key_or_path = "nemotron"
    if str(key_or_path).lower() in ("parakeet", "parakeet_v3", "parakeet-v3"):
        base = os.path.expandvars(r"%USERPROFILE%\.cache\huggingface\hub\models--handy-computer--parakeet-tdt-0.6b-v3-gguf")
        matches = glob.glob(os.path.join(base, "snapshots", "*", "parakeet-tdt-0.6b-v3-Q8_0.gguf"))
        if matches:
            return matches[0]
        matches_q4 = glob.glob(os.path.join(base, "snapshots", "*", "parakeet-tdt-0.6b-v3-*.gguf"))
        if matches_q4:
            return matches_q4[0]
    elif str(key_or_path).lower() in ("nemotron", "nemotron_3.5"):
        base = os.path.expandvars(r"%USERPROFILE%\.cache\huggingface\hub\models--handy-computer--nemotron-3.5-asr-streaming-0.6b-gguf")
        matches = glob.glob(os.path.join(base, "snapshots", "*", "nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf"))
        if matches:
            return matches[0]
    if os.path.exists(str(key_or_path)):
        return key_or_path
    return None

# Default model location from Handy / Hugging Face cache
DEFAULT_MODEL_PATH = resolve_model("nemotron") or os.path.expandvars(
    r"%USERPROFILE%\.cache\huggingface\hub\models--handy-computer--nemotron-3.5-asr-streaming-0.6b-gguf\snapshots\6d44e540bc31b0de1dbe174a3cea87f53a7f22fb\nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf"
)


def trim_silence(audio: np.ndarray, sample_rate: int = 16000, pad_ms: int = 250) -> np.ndarray:
    """Trim leading and trailing low-energy silence to minimize compute and latency."""
    if len(audio) == 0:
        return audio
    win_size = int(sample_rate * 0.05)  # 50ms windows
    n_win = len(audio) // win_size
    if n_win < 4:
        return audio

    windows = audio[:n_win * win_size].reshape(n_win, win_size)
    rms = np.sqrt(np.mean(windows**2, axis=1))

    p10 = float(np.percentile(rms, 10))
    p90 = float(np.percentile(rms, 90))

    if (p90 - p10) < 0.003 or p90 < 0.005:
        return audio

    threshold = max(0.004, p10 + 0.12 * (p90 - p10))
    active = np.where(rms >= threshold)[0]
    if len(active) == 0:
        return audio

    pad_wins = max(2, int(pad_ms / 50))
    start_win = max(0, active[0] - pad_wins)
    end_win = min(n_win, active[-1] + pad_wins + 1)

    start_sample = start_win * win_size
    end_sample = min(len(audio), end_win * win_size)
    trimmed = audio[start_sample:end_sample]
    return trimmed if len(trimmed) > 0 else audio


class StreamText(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint64),
        ("full_text", ctypes.c_char_p),
        ("full_text_bytes", ctypes.c_uint64),
        ("committed_text", ctypes.c_char_p),
        ("committed_text_bytes", ctypes.c_uint64),
        ("tentative_text", ctypes.c_char_p),
        ("tentative_text_bytes", ctypes.c_uint64),
        ("raw_tentative_start_bytes", ctypes.c_uint64),
    ]


class NemotronStreaming:
    _instance = None
    _lock = threading.Lock()

    def __init__(self, model_path: str = None, dll_dir: str = None):
        self.dll_dir = dll_dir or HANDY_DIR
        resolved = resolve_model(model_path)
        self.model_path = resolved or model_path or DEFAULT_MODEL_PATH
        self.is_offline = "parakeet" in str(self.model_path).lower()
        self.lib = None
        self.model_p = ctypes.c_void_p()
        self.sess_p = ctypes.c_void_p()
        self._streaming = False
        self._stream_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self._last_text = ""
        self._on_partial = None
        self._buffered_chunks = []
        self._last_partial_time = 0.0

        self._load_lib()
        self._load_model()

    def _load_lib(self):
        if not os.path.exists(self.dll_dir):
            raise FileNotFoundError(f"Handy runtime directory not found at: {self.dll_dir}")
        dll_file = os.path.join(self.dll_dir, "transcribe.dll")
        if not os.path.exists(dll_file):
            raise FileNotFoundError(f"transcribe.dll not found at: {dll_file}")

        try:
            os.add_dll_directory(self.dll_dir)
        except Exception:
            pass

        self.lib = ctypes.CDLL(dll_file)
        self.lib.transcribe_init_backends_default.restype = ctypes.c_int
        self.lib.transcribe_init_backends_default()

        # Suppress internal transcribe.dll / ggml decoder stderr spam
        try:
            self.lib.transcribe_log_set.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.lib.transcribe_log_set.restype = None
            self.lib.transcribe_log_set(None, None)
        except Exception:
            pass

        # Setup FFI signatures
        self.lib.transcribe_model_load_file.argtypes = [
            ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        self.lib.transcribe_model_load_file.restype = ctypes.c_int

        self.lib.transcribe_session_init.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        ]
        self.lib.transcribe_session_init.restype = ctypes.c_int

        self.lib.transcribe_stream_begin.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        ]
        self.lib.transcribe_stream_begin.restype = ctypes.c_int

        self.lib.transcribe_stream_feed.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_void_p
        ]
        self.lib.transcribe_stream_feed.restype = ctypes.c_int

        self.lib.transcribe_stream_get_text.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(StreamText)
        ]
        self.lib.transcribe_stream_get_text.restype = ctypes.c_int

        self.lib.transcribe_stream_finalize.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p
        ]
        self.lib.transcribe_stream_finalize.restype = ctypes.c_int

        self.lib.transcribe_stream_reset.argtypes = [ctypes.c_void_p]
        self.lib.transcribe_stream_reset.restype = None

        self.lib.transcribe_run.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        self.lib.transcribe_run.restype = ctypes.c_int

        self.lib.transcribe_full_text.argtypes = [ctypes.c_void_p]
        self.lib.transcribe_full_text.restype = ctypes.c_char_p

        self.lib.transcribe_session_free.argtypes = [ctypes.c_void_p]
        self.lib.transcribe_model_free.argtypes = [ctypes.c_void_p]

    def _load_model(self):
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model GGUF not found at: {self.model_path}")

        load_params = ctypes.create_string_buffer(512)
        self.lib.transcribe_model_load_params_init(load_params)

        if self.is_offline:
            # For Parakeet TDT v3 (offline FastConformer), CPU (AVX2 / Alder Lake) is 20x-30x faster
            # than Vulkan which suffers severe GPU memory-split and buffer thrashing.
            # offset 8 in transcribe_model_load_params is backend: 1 = TRANSCRIBE_BACKEND_CPU
            ctypes.c_int32.from_buffer(load_params, 8).value = 1

        rc = self.lib.transcribe_model_load_file(
            self.model_path.encode("utf-8"),
            load_params,
            ctypes.byref(self.model_p)
        )
        if rc != 0 or not self.model_p.value:
            raise RuntimeError(f"Failed to load model, rc={rc}")

        sess_params = ctypes.create_string_buffer(512)
        self.lib.transcribe_session_params_init(sess_params)

        rc = self.lib.transcribe_session_init(
            self.model_p,
            sess_params,
            ctypes.byref(self.sess_p)
        )
        if rc != 0 or not self.sess_p.value:
            raise RuntimeError(f"Failed to initialize session, rc={rc}")

        model_name = "Parakeet TDT v3 (CPU AVX2)" if self.is_offline else "Nemotron Streaming 3.5 (Vulkan GPU)"
        print(f"[{model_name}] Model initialized successfully!")

    def _transcribe_offline_audio(self, pcm: np.ndarray) -> str:
        """Run one-shot inference using transcribe_run on CPU."""
        if len(pcm) == 0:
            return ""
        if not pcm.flags["C_CONTIGUOUS"]:
            pcm = np.ascontiguousarray(pcm, dtype=np.float32)
        elif pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32)

        run_params = ctypes.create_string_buffer(512)
        self.lib.transcribe_run_params_init(run_params)
        # Disable timestamps to maximize decode speed
        ctypes.c_int32.from_buffer(run_params, 12).value = 0  # TRANSCRIBE_TIMESTAMPS_NONE

        ptr = pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        rc = self.lib.transcribe_run(self.sess_p, ptr, len(pcm), run_params)
        if rc != 0:
            return ""

        ptr_text = self.lib.transcribe_full_text(self.sess_p)
        if ptr_text:
            raw = ctypes.cast(ptr_text, ctypes.c_char_p).value
            return raw.decode("utf-8", errors="ignore").strip() if raw else ""
        return ""

    def start_stream(self, on_partial=None, language=None):
        """Start a new live streaming recognition session."""
        with self._stream_lock:
            self._on_partial = on_partial
            self._last_text = ""
            self._buffered_chunks = []
            self._last_partial_time = time.time()
            self._streaming = True

            if self.is_offline:
                return True

            # Always reset session state back to IDLE so multiple consecutive dictations work reliably
            try:
                self.lib.transcribe_stream_reset(self.sess_p)
            except Exception:
                pass
            run_params = ctypes.create_string_buffer(512)
            self.lib.transcribe_run_params_init(run_params)
            stream_params = ctypes.create_string_buffer(512)
            self.lib.transcribe_stream_params_init(stream_params)

            # If language is English (or default English), lock Nemotron stream to en-US (locale 0)
            target = (language or "en").strip().lower()
            if target in ("en", "en-us", "en-gb"):
                try:
                    pk_ext = ctypes.create_string_buffer(64)
                    self.lib.transcribe_parakeet_stream_ext_init(pk_ext)
                    ctypes.c_int32.from_buffer(pk_ext, 16).value = 0  # 0 = en-US locale index
                    ptr = ctypes.cast(pk_ext, ctypes.c_void_p).value
                    ctypes.c_uint64.from_buffer(stream_params, 8).value = ptr
                    self._active_pk_ext = pk_ext  # keep reference alive
                except Exception:
                    pass

            rc = self.lib.transcribe_stream_begin(self.sess_p, run_params, stream_params)
            if rc != 0:
                self.is_offline = True
                return True

            return True

    def feed_audio(self, pcm_chunk: np.ndarray) -> str:
        """Feed a float32 16kHz audio chunk and get current full text."""
        if not self._streaming or len(pcm_chunk) == 0:
            return self._last_text

        with self._stream_lock:
            if not pcm_chunk.flags["C_CONTIGUOUS"]:
                pcm_chunk = np.ascontiguousarray(pcm_chunk, dtype=np.float32)
            elif pcm_chunk.dtype != np.float32:
                pcm_chunk = pcm_chunk.astype(np.float32)

            if self.is_offline:
                self._buffered_chunks.append(pcm_chunk)
                return self._last_text

            update_buf = ctypes.create_string_buffer(512)
            self.lib.transcribe_stream_update_init(update_buf)

            ptr = pcm_chunk.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            self.lib.transcribe_stream_feed(self.sess_p, ptr, len(pcm_chunk), update_buf)

            st = StreamText()
            self.lib.transcribe_stream_text_init(ctypes.byref(st))
            self.lib.transcribe_stream_get_text(self.sess_p, ctypes.byref(st))

            txt = (st.full_text or b"").decode("utf-8", errors="ignore").strip()
            self._last_text = txt
            if self._on_partial and txt:
                try:
                    self._on_partial(txt)
                except Exception:
                    pass
            return txt

    def finalize(self, full_audio: Optional[np.ndarray] = None) -> str:
        """Finish live streaming and return the final text."""
        with self._stream_lock:
            if not self._streaming and full_audio is None:
                return self._last_text

            if self.is_offline:
                self._streaming = False
                with self._infer_lock:
                    target_audio = full_audio
                    if target_audio is None or len(target_audio) == 0:
                        if self._buffered_chunks:
                            target_audio = np.concatenate(self._buffered_chunks)
                    self._buffered_chunks = []
                    if target_audio is not None and len(target_audio) > 0:
                        trimmed = trim_silence(target_audio, sample_rate=16000, pad_ms=250)
                        final_text = self._transcribe_offline_audio(trimmed)
                    else:
                        final_text = self._last_text
                    self._last_text = final_text
                    return final_text

            update_buf = ctypes.create_string_buffer(512)
            self.lib.transcribe_stream_update_init(update_buf)
            self.lib.transcribe_stream_finalize(self.sess_p, update_buf)

            st = StreamText()
            self.lib.transcribe_stream_text_init(ctypes.byref(st))
            self.lib.transcribe_stream_get_text(self.sess_p, ctypes.byref(st))

            final_text = (st.full_text or b"").decode("utf-8", errors="ignore").strip()
            self._streaming = False
            self._last_text = final_text
            try:
                self.lib.transcribe_stream_reset(self.sess_p)
            except Exception:
                pass
            return final_text

    def transcribe(self, audio: np.ndarray, on_partial=None) -> str:
        """Batch transcribe full audio array."""
        if self.is_offline:
            with self._infer_lock:
                trimmed = trim_silence(audio, sample_rate=16000, pad_ms=250)
                return self._transcribe_offline_audio(trimmed)
        self.start_stream(on_partial=on_partial)
        chunk_size = 2560  # 160ms chunks
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i : i + chunk_size]
            self.feed_audio(chunk)
        return self.finalize()
        self.start_stream(on_partial=on_partial)
        chunk_size = 2560  # 160ms chunks
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i : i + chunk_size]
            self.feed_audio(chunk)
        return self.finalize()

    def transcribe_full(self, audio: np.ndarray, on_partial=None) -> str:
        return self.transcribe(audio, on_partial=on_partial)

    def close(self):
        with self._stream_lock:
            if self.sess_p.value:
                try:
                    self.lib.transcribe_session_free(self.sess_p)
                except Exception:
                    pass
                self.sess_p = ctypes.c_void_p()
            if self.model_p.value:
                try:
                    self.lib.transcribe_model_free(self.model_p)
                except Exception:
                    pass
                self.model_p = ctypes.c_void_p()


def is_available(key="nemotron") -> bool:
    """Returns True if both transcribe.dll and the requested model file exist."""
    dll_ok = os.path.exists(DLL_PATH)
    m = resolve_model(key)
    return bool(dll_ok and m and os.path.exists(m))
