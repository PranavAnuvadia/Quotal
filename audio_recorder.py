"""
audio_recorder.py - Low-latency microphone capture with RMS level monitoring.
"""

import sounddevice as sd
import numpy as np
import threading
import queue


class AudioRecorder:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.stream = None
        self.frames = []
        self.is_recording = False
        self._lock = threading.Lock()
        self.current_level = 0.0  # Normalized 0.0 to 1.0 for visual meter

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
        """Start capturing audio."""
        with self._lock:
            self.frames.clear()
            self.is_recording = True
            self.current_level = 0.0

        if self.stream is None:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=self._audio_callback
            )
            self.stream.start()
        elif not self.stream.active:
            self.stream.start()

    def stop(self) -> np.ndarray:
        """Stop capturing and return the recorded audio as a float32 numpy array."""
        self.is_recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        with self._lock:
            if not self.frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self.frames, axis=0)
            self.frames.clear()
            return audio

    def get_level(self) -> float:
        """Get current audio volume level (0.0 to 1.0)."""
        return self.current_level
