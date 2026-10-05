"""
transcriber.py - Model loader and transcription engine using faster-whisper.
Includes automatic CUDA DLL discovery, dynamic CPU threading, zero-latency inference,
and explicit GPU memory management.
"""

import time
import os
import sys
import gc
import numpy as np
from faster_whisper import WhisperModel
import ctranslate2

# Add nvidia site-packages bin/lib directories to Windows DLL search path
def setup_cuda_dlls():
    search_dirs = []
    if sys.prefix:
        search_dirs.append(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia"))
    try:
        import site
        for s in site.getsitepackages():
            search_dirs.append(os.path.join(s, "nvidia"))
    except Exception:
        pass
    for nvidia_dir in set(search_dirs):
        if os.path.isdir(nvidia_dir):
            for sub in os.listdir(nvidia_dir):
                for folder in ["bin", "lib"]:
                    d = os.path.join(nvidia_dir, sub, folder)
                    if os.path.isdir(d):
                        try:
                            os.add_dll_directory(d)
                            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
                        except Exception:
                            pass

setup_cuda_dlls()


def get_optimal_cpu_threads() -> int:
    """Calculates optimal CPU thread count based on physical/logical cores."""
    cores = os.cpu_count() or 4
    return max(1, min(8, cores))


class Transcriber:
    def __init__(self, model_path: str = "models/hinglish-swift-ct2", device: str = "auto"):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        local_cand = os.path.join(base_dir, model_path)
        if os.path.exists(local_cand):
            self.model_path = local_cand
        else:
            self.model_path = model_path
        setup_cuda_dlls()
        
        self.cpu_threads = get_optimal_cpu_threads()

        # Determine device
        if device == "auto":
            has_cuda = ctranslate2.get_cuda_device_count() > 0
            self.device = "cuda" if has_cuda else "cpu"
        else:
            self.device = device
            
        t0 = time.time()
        if self.device == "cuda":
            print(f"[Transcriber] Initializing '{model_path}' on CUDA (float16)...")
            try:
                self.compute_type = "float16"
                self.model = WhisperModel(
                    self.model_path,
                    device="cuda",
                    compute_type="float16"
                )
                print(f"[Transcriber] CUDA initialized & ready in {time.time() - t0:.2f}s!")
            except Exception as e:
                print(f"[Transcriber] CUDA unavailable ({e}). Falling back to CPU (int8)...")
                self.device = "cpu"
                self.compute_type = "int8"
                self.model = WhisperModel(
                    self.model_path,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self.cpu_threads
                )
                print(f"[Transcriber] CPU (int8) ready in {time.time() - t0:.2f}s!")
        else:
            self.compute_type = "int8"
            print(f"[Transcriber] Initializing '{model_path}' on CPU (int8)...")
            self.model = WhisperModel(
                self.model_path,
                device="cpu",
                compute_type="int8",
                cpu_threads=self.cpu_threads
            )
            print(f"[Transcriber] CPU ready in {time.time() - t0:.2f}s!")

    def unload(self):
        """Explicitly release model resources and free GPU memory immediately."""
        if hasattr(self, "model") and self.model is not None:
            try:
                del self.model
                self.model = None
            except Exception:
                pass
        gc.collect()

    def transcribe(self, audio: np.ndarray, language: str = None) -> tuple[str, float]:
        """
        Transcribes the float32 audio array.
        Returns: (transcribed_text, elapsed_seconds)
        """
        if self.model is None or len(audio) == 0:
            return "", 0.0

        duration = len(audio) / 16000.0
        if duration < 0.25:
            # Noise click or accidental key tap
            return "", 0.0

        # Windowed energy check (100ms windows): preserves quiet speech inside longer holds
        win_size = 1600
        n_win = len(audio) // win_size
        if n_win > 0:
            windows = audio[:n_win * win_size].reshape(n_win, win_size)
            max_rms = float(np.sqrt(np.mean(windows**2, axis=1)).max())
            if max_rms < 0.0018:
                return "", 0.0
        elif np.sqrt(np.mean(audio**2)) < 0.0018:
            return "", 0.0

        t0 = time.time()
        try:
            # For push-to-talk, audio is already strictly user-bounded.
            # Disabling vad_filter bypasses CPU ONNX Silero VAD latency and runs direct GPU inference.
            segments, info = self.model.transcribe(
                audio,
                beam_size=1,  # greedy / ultra-fast
                language=language or "en",
                task="transcribe",
                vad_filter=False
            )
            text_parts = [segment.text.strip() for segment in segments]
            result_text = " ".join(text_parts).strip()
            elapsed = time.time() - t0
            return result_text, elapsed
        except Exception as e:
            print(f"[Transcriber Error] {e}")
            return "", 0.0
