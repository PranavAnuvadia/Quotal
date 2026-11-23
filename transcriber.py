"""
transcriber.py - Model loader and transcription engine using faster-whisper.
Includes automatic CUDA DLL discovery, dynamic CPU threading, zero-latency inference,
and explicit GPU memory management.
"""

import time
import os
import sys
import gc
import threading
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


def cuda_libs_present() -> bool:
    """True when the cuBLAS runtime DLLs are actually on disk.

    ctranslate2 reports CUDA devices as available (driver present) even when
    the cuBLAS/cuDNN wheels are missing -- the failure only surfaces at first
    inference. Checking here stops us claiming a GPU we cannot use.
    """
    roots = []
    if sys.prefix:
        roots.append(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia"))
    try:
        import site
        for s in site.getsitepackages():
            roots.append(os.path.join(s, "nvidia"))
    except Exception:
        pass
    roots.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              ".venv", "Lib", "site-packages", "nvidia"))
    for root in set(roots):
        try:
            if not os.path.isdir(root):
                continue
            for sub in os.listdir(root):
                d = os.path.join(root, sub, "bin")
                if not os.path.isdir(d):
                    continue
                for n in os.listdir(d):
                    low = n.lower()
                    if low.startswith("cublas64_") and low.endswith(".dll"):
                        return True
        except OSError:
            continue
    return False


def get_optimal_cpu_threads() -> int:
    """Calculates optimal CPU thread count based on physical cores without thrashing."""
    try:
        import psutil
        # Intel hybrid architectures (Alder Lake / Raptor Lake) have P-cores and E-cores.
        # Restricting to 4 threads targets the physical P-cores and prevents OpenMP sync stalls on E-cores.
        physical = psutil.cpu_count(logical=False)
        if physical:
            return max(1, min(4, physical))
    except Exception:
        pass
    cores = os.cpu_count() or 4
    return max(1, min(4, cores // 2 if cores > 4 else cores))


def trim_silence(audio: np.ndarray, sample_rate: int = 16000, pad_ms: int = 300) -> np.ndarray:
    """
    Trim leading and trailing low-energy silence to minimize compute and latency.
    Uses adaptive energy thresholding based on the dynamic range of the audio,
    with a generous 300ms safety pad so speech is never clipped.
    """
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
    
    # If dynamic range is very low (e.g. constant background noise or total silence), don't trim
    if (p90 - p10) < 0.003 or p90 < 0.005:
        return audio
        
    threshold = max(0.004, p10 + 0.12 * (p90 - p10))
    active = np.where(rms >= threshold)[0]
    if len(active) == 0:
        return audio
        
    pad_wins = max(2, int(pad_ms / 50))  # 300ms = 6 windows
    start_win = max(0, active[0] - pad_wins)
    end_win = min(n_win, active[-1] + pad_wins + 1)
    
    start_sample = start_win * win_size
    end_sample = min(len(audio), end_win * win_size)
    trimmed = audio[start_sample:end_sample]
    return trimmed if len(trimmed) > 0 else audio


class Transcriber:
    def __init__(self, model_path: str = "models/hinglish-swift-ct2", device: str = "auto"):
        self._infer_lock = threading.Lock()
        base_dir = os.path.dirname(os.path.abspath(__file__))

        def _resolve_model(path_or_size: str) -> str:
            if os.path.exists(path_or_size):
                return path_or_size
            local_cand = os.path.join(base_dir, path_or_size)
            if os.path.exists(local_cand):
                return local_cand
            try:
                from faster_whisper.utils import download_model
                return download_model(path_or_size, local_files_only=True)
            except Exception:
                pass
            return path_or_size

        self.model_path = _resolve_model(model_path)
        setup_cuda_dlls()
        
        self.cpu_threads = get_optimal_cpu_threads()
        self.requested_device = device
        self._cpu_fallback_done = False
        # True when a GPU exists but its runtime libs are missing (GPU pack
        # not installed) -> the dashboard uses this to offer the pack.
        self.gpu_libs_missing = False

        self.is_sherpa = "omnilingual" in str(self.model_path).lower() or "sherpa" in str(self.model_path).lower()
        if self.is_sherpa:
            t0 = time.time()
            import sherpa_onnx
            onnx_path = None
            tokens_path = None
            for root, _, files in os.walk(self.model_path):
                for f in files:
                    if f.endswith(".onnx") and onnx_path is None:
                        onnx_path = os.path.join(root, f)
                    elif f == "tokens.txt" and tokens_path is None:
                        tokens_path = os.path.join(root, f)
            print(f"[Transcriber] Initializing Omnilingual ASR (sherpa-onnx) on CPU ({self.cpu_threads} threads)...")
            self.model = sherpa_onnx.OfflineRecognizer.from_omnilingual_asr_ctc(
                model=onnx_path,
                tokens=tokens_path,
                num_threads=self.cpu_threads,
                provider="cpu",
                decoding_method="greedy_search"
            )
            self.device = "cpu"
            self.compute_type = "int8"
            print(f"[Transcriber] Omnilingual ASR ready in {time.time() - t0:.2f}s!")
            return

        self.is_hima = "hima" in str(self.model_path).lower()
        if self.is_hima:
            t0 = time.time()
            if self.model_path not in sys.path:
                sys.path.insert(0, self.model_path)
            from hima_onnx import HimaONNX
            self.model = HimaONNX(path=self.model_path, precision="int8", threads=self.cpu_threads, device=self.requested_device)
            providers = getattr(self.model.enc, "get_providers", lambda: [])()
            used_gpu = any("Dml" in p or "CUDA" in p for p in providers)
            self.device = "cuda" if used_gpu else "cpu"
            self.compute_type = "int8"
            dev_str = "DirectML GPU (RTX 3050)" if used_gpu else f"CPU ({self.cpu_threads} threads)"
            print(f"[Transcriber] Initializing Hima ASR on {dev_str}...")
            # Warm up GPU sessions to compile DirectML shaders ahead of time
            if used_gpu:
                try:
                    dummy_audio = np.zeros(8000, dtype=np.float32)
                    _ = self.model.listen(dummy_audio)
                except Exception:
                    pass
            # Pre-cache Decision Head language questions (zero latency overhead during dictation)
            try:
                self.warmup_hima()
            except Exception as e:
                print(f"[Transcriber] Hima warmup notice: {e}")
            print(f"[Transcriber] Hima ASR ready in {time.time() - t0:.2f}s!")
            return

        self.is_parakeet = "parakeet" in str(self.model_path).lower()
        self.is_nemotron = "nemotron" in str(self.model_path).lower() and not self.is_parakeet
        self.is_streaming_model = self.is_nemotron
        if self.is_nemotron or self.is_parakeet:
            t0 = time.time()
            from nemotron_streaming import NemotronStreaming
            name = "NVIDIA Parakeet TDT v3 (0.6B)" if self.is_parakeet else "NVIDIA Nemotron Streaming 3.5 (0.6B)"
            print(f"[Transcriber] Initializing {name}...")
            self.model = NemotronStreaming(model_path=self.model_path)
            self.device = "cpu" if self.is_parakeet else "gpu"
            self.compute_type = "q8_0"
            print(f"[Transcriber] {name} ready in {time.time() - t0:.2f}s!")
            return

        # Determine device (any detection error -> CPU, never crash boot)
        libs_ok = cuda_libs_present()
        if device == "auto":
            try:
                has_cuda = ctranslate2.get_cuda_device_count() > 0
            except Exception as e:
                print(f"[Transcriber] CUDA probe failed ({e}). Using CPU.")
                has_cuda = False
            if has_cuda and not libs_ok:
                # Driver present but cuBLAS/cuDNN absent: claiming CUDA here
                # would load "successfully" then fail on the first dictation.
                self.gpu_libs_missing = True
                print("[Transcriber] NVIDIA GPU found but GPU pack not installed "
                      "(cublas64 missing). Using CPU until it is installed.")
                has_cuda = False
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
        """Explicitly release model resources and free memory immediately."""
        with self._infer_lock:
            if hasattr(self, "model") and self.model is not None:
                try:
                    if hasattr(self.model, "close"):
                        self.model.close()
                    del self.model
                    self.model = None
                except Exception:
                    pass
        gc.collect()

    def resolve_language(self, audio: np.ndarray = None, selected_languages: list[str] = None, language: str = None) -> tuple[str | None, str | None]:
        """
        Resolves language code without unconstrained 99-language guessing.
        - If language is explicitly given (e.g. 'es'), uses that.
        - If selected_languages has 1 item, uses that directly (0 detection overhead).
        - If 'hinglish' is used with hinglish-swift model -> uses 'en'.
        - If 'hinglish' is used with standard whisper -> uses 'hi' (prevents Whisper from translating Hindi speech to English).
        - If multiple languages are selected -> constrains auto-detection strictly to the selected candidates.
        """
        if not selected_languages:
            try:
                import settings_manager
                s = settings_manager.load_settings()
                selected_languages = s.get("selected_languages", ["en"])
            except Exception:
                selected_languages = ["en"]

        is_hinglish_model = "hinglish" in str(self.model_path).lower()

        if language and language not in ("auto", "Auto", ""):
            if language == "hinglish":
                return ("en", None) if is_hinglish_model else ("hi", None)
            return language, None

        if selected_languages:
            valid = [l for l in selected_languages if l]
            if len(valid) == 1:
                target = valid[0]
                if target == "hinglish":
                    return ("en", None) if is_hinglish_model else ("hi", None)
                return target, None

            if is_hinglish_model:
                return "en", None

            # Constrain language candidates strictly to the user's selected languages
            candidate_map = {}
            for l in valid:
                if l == "hinglish":
                    candidate_map["hi"] = "hinglish"
                else:
                    candidate_map[l] = l

            hinglish_prompt = (
                "Bhai, maine tujhe phone kiya tha tune uthaya nahi. "
                "Dekh kaam kuch aisa hai ki hume building ko todna hai. "
                "Call karo aur bolo office meeting message check karo."
            )

            if audio is not None and len(audio) > 0 and hasattr(self, "model") and self.model is not None:
                try:
                    _, _, all_probs = self.model.detect_language(audio, vad_filter=True)
                    cand_dict = {code: prob for code, prob in all_probs if code in candidate_map}
                    if cand_dict:
                        # CRITICAL: When Hinglish is among selected languages:
                        # Whisper MUST NEVER run with language="en" on code-mixed speech,
                        # because Whisper's decoder will translate the Hindi words into English!
                        # When speaking Hinglish, Whisper's language detection on audio containing
                        # English loanwords (phone, building, call) often predicts 'en' ~0.60-0.85
                        # and 'hi' ~0.02-0.10.
                        # ONLY pick 'en' if speech is overwhelmingly pure English (en > 0.95 and hi < 0.01).
                        if "hinglish" in valid and "hi" in cand_dict:
                            if cand_dict.get("en", 0) > 0.95 and cand_dict.get("hi", 0) < 0.01:
                                return "en", None
                            else:
                                return "hi", hinglish_prompt

                        best_code = max(cand_dict, key=cand_dict.get)
                        prompt = hinglish_prompt if (best_code == "hi" and "hinglish" in valid) else None
                        return best_code, prompt
                except Exception as e:
                    print(f"[Transcriber] Constrained language detection error: {e}")

            # Fallback to the first selected language
            first = valid[0]
            if first == "hinglish":
                return ("en", None) if is_hinglish_model else ("hi", hinglish_prompt)
            return first, None

        return None, None

    def warmup_hima(self, selected_languages: list[str] = None):
        """Pre-cache Decision Head question vectors so language classification is instant during speech."""
        if not getattr(self, "is_hima", False) or self.model is None:
            return
        try:
            HIMA_LANGUAGES = {
                "as": "Assamese", "bn": "Bengali", "en": "English", "gu": "Gujarati",
                "hi": "Hindi", "kn": "Kannada", "ml": "Malayalam", "mr": "Marathi",
                "or": "Odia", "pa": "Punjabi", "ta": "Tamil", "te": "Telugu"
            }
            if not selected_languages:
                try:
                    import settings_manager
                    cfg = settings_manager.load_settings()
                    selected_languages = cfg.get("selected_languages", [])
                except Exception:
                    selected_languages = []

            to_warm = []
            if selected_languages and len(selected_languages) > 1:
                to_warm.append(selected_languages)
            default_multi = ["en", "hi", "mr", "gu", "hinglish"]
            if default_multi not in to_warm:
                to_warm.append(default_multi)

            for lang_set in to_warm:
                candidate_options = {}
                for code in lang_set:
                    if code == "hinglish":
                        candidate_options["hi"] = "Hindi or Hinglish"
                    elif code in HIMA_LANGUAGES:
                        candidate_options[code] = HIMA_LANGUAGES[code]
                if len(candidate_options) > 1:
                    q = {
                        "q": "Which language is the speaker speaking?",
                        "type": "choice",
                        "options": candidate_options
                    }
                    self.model.head._question(q)
            print(f"[Transcriber] Hima multilingual Decision Head pre-cached and ready!")
        except Exception as e:
            print(f"[Transcriber] Hima warmup notice: {e}")

    def transcribe_hima(self, audio: np.ndarray, selected_languages: list[str] = None, language: str = None) -> str:
        """
        Unified single-pass transcription for Hima ASR on GPU.
        Reuses acoustic encoding layers for both language classification and transcript generation.
        """
        HIMA_LANGUAGES = {
            "as": "Assamese", "bn": "Bengali", "en": "English", "gu": "Gujarati",
            "hi": "Hindi", "kn": "Kannada", "ml": "Malayalam", "mr": "Marathi",
            "or": "Odia", "pa": "Punjabi", "ta": "Tamil", "te": "Telugu"
        }

        # 1. Listen once (acoustic encoding on GPU)
        sound, layers = self.model._listen(audio)

        valid = [l for l in (selected_languages or []) if l in HIMA_LANGUAGES or l == "hinglish"]
        if not valid:
            valid = ["en", "hi"]

        # Determine target language and code-switching style
        if language and language not in ("auto", "Auto", ""):
            best_lang = "hi" if language == "hinglish" else language
            style = "mixed" if language == "hinglish" else "native"
        elif len(valid) == 1:
            best_lang = "hi" if valid[0] == "hinglish" else valid[0]
            style = "mixed" if valid[0] == "hinglish" else "native"
        else:
            # Constrained multilingual decision using pre-computed acoustic states
            candidate_options = {}
            for code in valid:
                if code == "hinglish":
                    candidate_options["hi"] = "Hindi or Hinglish"
                elif code in HIMA_LANGUAGES:
                    candidate_options[code] = HIMA_LANGUAGES[code]

            try:
                x = np.concatenate([self.model.embed[[self.model.row[self.model.bos]]], sound, self.model.sot[None]]).astype(np.float32)
                pk = np.zeros((18, 1, 0, 256), np.float32)
                states = self.model._step(x, 0, pk, pk)[3][:, 1:]
                q = {
                    "q": "Which language is the speaker speaking?",
                    "type": "choice",
                    "options": candidate_options
                }
                ans = self.model.head.decide(self.model.head.sound(layers, states), [q])
                best_lang = valid[0]
                if ans and isinstance(ans, list) and len(ans) > 0:
                    probs = ans[0]
                    best_lang = max(probs, key=probs.get)
            except Exception as e:
                print(f"[Transcriber] Hima decision head language detection error: {e}")
                best_lang = "hi" if "hinglish" in valid else valid[0]

            style = "mixed" if ("hinglish" in valid and best_lang == "hi") else "native"

        # 2. Write transcript directly with already-computed sound
        text, _ = self.model._write(sound, language=best_lang, style=style)
        return text

    def transcribe(self, audio: np.ndarray, language: str = None, selected_languages: list[str] = None) -> tuple[str, float]:
        """
        Transcribes the float32 audio array.
        Returns: (transcribed_text, elapsed_seconds)
        """
        with self._infer_lock:
            if self.model is None or len(audio) == 0:
                return "", 0.0

            # Trim leading/trailing dead air to cut compute and latency
            audio = trim_silence(audio)
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
            if self.is_sherpa:
                try:
                    stream = self.model.create_stream()
                    stream.accept_waveform(16000, audio)
                    self.model.decode_stream(stream)
                    result_text = stream.result.text.strip()
                    elapsed = time.time() - t0
                    return result_text, elapsed
                except Exception as e:
                    print(f"[Transcriber Error] sherpa-onnx failed: {e}")
                    return "", 0.0

            if self.is_hima:
                try:
                    result_text = self.transcribe_hima(audio=audio, selected_languages=selected_languages, language=language)
                    elapsed = time.time() - t0
                    return result_text, elapsed
                except Exception as e:
                    print(f"[Transcriber Error] Hima ASR failed: {e}")
                    return "", 0.0

            if self.is_nemotron or self.is_parakeet:
                try:
                    result_text = self.model.transcribe(audio)
                    elapsed = time.time() - t0
                    return result_text, elapsed
                except Exception as e:
                    print(f"[Transcriber Error] Nemotron/Parakeet failed: {e}")
                    return "", 0.0

            target_lang, init_prompt = self.resolve_language(audio=audio, selected_languages=selected_languages, language=language)
            kw = {
                "beam_size": 1,
                "best_of": 1,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "without_timestamps": True,
                "compression_ratio_threshold": 2.4,
                "repetition_penalty": 1.1,
                "no_repeat_ngram_size": 3,
                "no_speech_threshold": 0.6,
                "task": "transcribe",
                "vad_filter": True,
                "vad_parameters": dict(min_silence_duration_ms=300, speech_pad_ms=150),
            }
            if target_lang:
                kw["language"] = target_lang
            if init_prompt:
                kw["initial_prompt"] = init_prompt

            try:
                segments, info = self.model.transcribe(audio, **kw)
                text_parts = [segment.text.strip() for segment in segments]
                result_text = " ".join(text_parts).strip()
                elapsed = time.time() - t0
                return result_text, elapsed
            except Exception as e:
                # Mid-session GPU failure (e.g. VRAM OOM): degrade to CPU once
                # and retry, so dictation keeps working instead of dying.
                if self.device == "cuda" and not self._cpu_fallback_done:
                    print(f"[Transcriber] GPU inference failed ({e}). Failing over to CPU...")
                    try:
                        del self.model
                    except Exception:
                        pass
                    gc.collect()
                    try:
                        self.model = WhisperModel(
                            self.model_path,
                            device="cpu",
                            compute_type="int8",
                            cpu_threads=self.cpu_threads
                        )
                        self.device = "cpu"
                        self.compute_type = "int8"
                        self._cpu_fallback_done = True
                        print("[Transcriber] CPU fallback ready, retrying...")
                        segments, info = self.model.transcribe(audio, **kw)
                        text_parts = [segment.text.strip() for segment in segments]
                        result_text = " ".join(text_parts).strip()
                        return result_text, time.time() - t0
                    except Exception as e2:
                        print(f"[Transcriber Error] CPU fallback failed: {e2}")
                        return "", 0.0
                print(f"[Transcriber Error] {e}")
                return "", 0.0

    def start_stream(self, on_partial=None, language=None):
        """Start streaming session if supported (Nemotron)."""
        if getattr(self, "is_streaming_model", False) and self.model:
            return self.model.start_stream(on_partial=on_partial, language=language)
        return False

    def feed_stream(self, chunk: np.ndarray) -> str:
        """Feed audio chunk to active stream, returning partial text."""
        if getattr(self, "is_streaming_model", False) and self.model:
            return self.model.feed_audio(chunk)
        return ""

    def finish_stream(self, full_audio=None) -> str:
        """Finalize streaming session and return the complete text."""
        if getattr(self, "is_streaming_model", False) and self.model:
            return self.model.finalize(full_audio=full_audio)
        return ""
