"""
audio_recorder.py - Ultra low-latency microphone capture with RMS level monitoring.
Uses persistent WASAPI stream to eliminate stream startup lag (0ms overhead) and driver crashes.
"""

import sounddevice as sd
import numpy as np
import threading
import time


def get_wasapi_input_device():
    """
    Find the best Windows WASAPI input device matching the OS default input.
    Prefers sd.default.device input if WASAPI; otherwise matches default device name
    within WASAPI; falls back to first WASAPI input as last resort.
    Returns (device_index, device_name, native_sample_rate).
    """
    try:
        wasapi_idx = None
        for i, api in enumerate(sd.query_hostapis()):
            if "wasapi" in api.get("name", "").lower():
                wasapi_idx = i
                break
        if wasapi_idx is None:
            return None, "System Default", 16000

        devices = sd.query_devices()
        
        # 1. Check if sd.default.device[0] is already a WASAPI device
        default_in_idx = sd.default.device[0]
        if 0 <= default_in_idx < len(devices):
            def_dev = devices[default_in_idx]
            if def_dev.get("hostapi") == wasapi_idx and def_dev.get("max_input_channels", 0) > 0:
                sr = int(def_dev.get("default_samplerate", 16000))
                return default_in_idx, def_dev.get("name", "Default WASAPI Device"), sr

            # 2. Match the OS default device's name within WASAPI devices
            def_name = def_dev.get("name", "").strip().lower()
            match_prefix = def_name[:24] if len(def_name) >= 24 else def_name
            for idx, dev in enumerate(devices):
                if dev.get("hostapi") == wasapi_idx and dev.get("max_input_channels", 0) > 0:
                    cand_name = dev.get("name", "").strip().lower()
                    if match_prefix and (match_prefix in cand_name or cand_name in def_name):
                        sr = int(dev.get("default_samplerate", 16000))
                        return idx, dev.get("name", "Matched WASAPI Device"), sr

        # 3. Fallback: first available WASAPI input
        for idx, dev in enumerate(devices):
            if dev.get("hostapi") == wasapi_idx and dev.get("max_input_channels", 0) > 0:
                sr = int(dev.get("default_samplerate", 16000))
                return idx, dev.get("name", "Fallback WASAPI Device"), sr

    except Exception:
        pass
    return None, "System Default", 16000


class AudioRecorder:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.stream = None
        self.device, self.device_name, self.stream_sr = get_wasapi_input_device()
        self.frames = []
        self.is_recording = False
        self._lock = threading.Lock()
        self.current_level = 0.0  # Normalized 0.0 to 1.0 for visual meter
        self._init_stream()

    def _init_stream(self):
        """Initializes and keeps an audio stream running persistently in the background."""
        try:
            if self.stream is not None:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

            kwargs = {
                "samplerate": self.stream_sr,
                "channels": 1,
                "dtype": "float32",
                "blocksize": 1024,
                "callback": self._audio_callback
            }
            if self.device is not None:
                kwargs["device"] = self.device

            self.stream = sd.InputStream(**kwargs)
            self.stream.start()
            print(f"[AudioRecorder] Persistent WASAPI mic stream active: {self.device_name} ({self.stream_sr}Hz, device {self.device}).")
        except Exception as e:
            print(f"[AudioRecorder] WASAPI init warning ({e}); trying system default at 16kHz...")
            try:
                self.stream_sr = self.sample_rate
                self.stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=1024,
                    callback=self._audio_callback
                )
                self.stream.start()
                print("[AudioRecorder] Persistent default mic stream active.")
            except Exception as e2:
                print(f"[AudioRecorder Critical] Could not start mic stream: {e2}")
                self.stream = None

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice on every audio chunk."""
        if not self.is_recording:
            return

        audio_chunk = indata[:, 0].copy()
        with self._lock:
            self.frames.append(audio_chunk)

        # Calculate RMS for visual volume meter
        rms = np.sqrt(np.mean(audio_chunk**2)) if len(audio_chunk) > 0 else 0.0
        # Smooth and scale RMS (voice usually ranges from 0.001 to 0.2)
        scaled = min(1.0, float(rms * 12.0))
        self.current_level = self.current_level * 0.4 + scaled * 0.6

    def start(self):
        """Start capturing audio instantly (0ms latency, persistent stream)."""
        with self._lock:
            self.frames.clear()
            self.is_recording = True
            self.current_level = 0.0

        # Auto-recover stream if device was disconnected or suspended
        if self.stream is None or not self.stream.active:
            self._init_stream()

    def stop(self) -> np.ndarray:
        """Stop capturing and return the recorded audio as a float32 numpy array at target sample_rate."""
        self.is_recording = False
        self.current_level = 0.0

        with self._lock:
            if not self.frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self.frames, axis=0)
            self.frames.clear()

        # Resample to target sample_rate (16kHz) if captured at native hardware rate (e.g. 48kHz)
        if self.stream_sr == 48000 and self.sample_rate == 16000:
            n = len(audio) - (len(audio) % 3)
            audio = audio[:n].reshape(-1, 3).mean(axis=1).astype(np.float32)
        elif self.stream_sr != self.sample_rate and len(audio) > 0:
            num_samples = int(len(audio) * self.sample_rate / self.stream_sr)
            audio = np.interp(
                np.linspace(0, len(audio), num_samples, endpoint=False),
                np.arange(len(audio)),
                audio
            ).astype(np.float32)

        return audio

    def get_level(self) -> float:
        """Get current audio volume level (0.0 to 1.0)."""
        return self.current_level

    def close(self):
        """Cleanly shutdown the persistent audio stream."""
        self.is_recording = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
