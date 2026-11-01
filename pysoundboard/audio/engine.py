"""
Audio engine: Mixer and Engine classes for PySoundboard.

Audio flow (per Mixer):
  sound voices  ──┐
                  ├─► sum ──► volume ──► output stream (virtual cable / headphones)
  optional mic  ──┘

Engine owns two Mixers:
  cable   → virtual cable input  (apps hear as "microphone")
  monitor → headphones           (you hear it too)
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

from pysoundboard.audio.resampler import resample_audio
from pysoundboard.audio.ring_buffer import AudioRingBuffer

logger = logging.getLogger(__name__)

SR = 48_000
BLOCK = 512


def load_sound(path: str) -> np.ndarray:
    """Load and normalise an audio file to float32 stereo at SR."""
    import soundfile as sf  # lazy import – keeps module importable without soundfile at test time

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return resample_audio(data, in_rate=sr, target_rate=SR)


class Mixer:
    """One output stream that sums all active voices plus optional mic pass-through."""

    def __init__(self) -> None:
        self._voices: list[list] = []   # each entry: [data, position, gain]
        self._lock = threading.Lock()
        self.volume: float = 1.0
        self.mic_volume: float = 1.0
        self._mic_rb: Optional[AudioRingBuffer] = None
        self._mic_tmp: Optional[np.ndarray] = None   # pre-alloc read buffer
        self.stream: Optional[sd.OutputStream] = None
        self._mic_stream: Optional[sd.InputStream] = None

    # ---------------------------------------------------------------- public --

    def add(self, data: np.ndarray, gain: float = 1.0) -> None:
        """Enqueue a sound for playback (can be called from any thread)."""
        with self._lock:
            self._voices.append([data, 0, gain])

    def stop_all(self) -> None:
        """Immediately silence all playing voices."""
        with self._lock:
            self._voices.clear()

    def start(self, out_dev: str, mic_dev: Optional[str] = None) -> None:
        """Open output (and optionally mic input) streams."""
        self.stop()
        self.stream = sd.OutputStream(
            samplerate=SR, blocksize=BLOCK, channels=2, dtype="float32",
            device=out_dev, callback=self._out_callback,
            latency="low",
        )
        if mic_dev:
            self._mic_rb = AudioRingBuffer(capacity_frames=SR // 4, channels=2)   # 250 ms
            self._mic_tmp = np.zeros((BLOCK, 2), dtype=np.float32)
            self._mic_stream = sd.InputStream(
                samplerate=SR, blocksize=BLOCK, channels=1, dtype="float32",
                device=mic_dev, callback=self._mic_callback,
                latency="low",
            )
            self._mic_stream.start()
        self.stream.start()
        logger.info("Mixer started: out_dev=%r mic_dev=%r", out_dev, mic_dev)

    def stop(self) -> None:
        """Stop and close all streams."""
        for s in (self.stream, self._mic_stream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception as exc:
                    logger.warning("Error closing stream: %s", exc)
        self.stream = self._mic_stream = None
        self._mic_rb = None
        self._mic_tmp = None

    # --------------------------------------------------------------- callbacks --

    def _out_callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        """Real-time output callback – zero allocations after start."""
        out = np.zeros((frames, 2), dtype=np.float32)

        with self._lock:
            alive: list = []
            for v in self._voices:
                data, pos, gain = v
                chunk = data[pos:pos + frames]
                out[:len(chunk)] += chunk * gain
                v[1] = pos + frames
                if v[1] < len(data):
                    alive.append(v)
            self._voices = alive

        out *= self.volume

        # Mix mic ring-buffer into the output
        if self._mic_rb is not None and self._mic_tmp is not None:
            n = self._mic_rb.read(self._mic_tmp, frames)
            if n:
                out[:n] += self._mic_tmp[:n] * self.mic_volume

        np.clip(out, -1.0, 1.0, out=out)
        outdata[:] = out

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """Real-time mic input callback – converts mono to stereo then writes ring-buffer."""
        stereo = np.repeat(indata[:, :1], 2, axis=1)
        if self._mic_rb is not None:
            self._mic_rb.write(stereo)


class Engine:
    """Owns two Mixers (cable and monitor) and a sound file cache."""

    def __init__(self) -> None:
        self.cable = Mixer()      # → virtual cable input
        self.monitor = Mixer()    # → headphones
        self._cache: dict[str, np.ndarray] = {}
        self._cache_lock = threading.Lock()

    # ---------------------------------------------------------------- public --

    def start(
        self,
        cable_dev: str,
        monitor_dev: Optional[str] = None,
        mic_dev: Optional[str] = None,
    ) -> None:
        """(Re-)start both mixers."""
        self.cable.start(cable_dev, mic_dev)
        if monitor_dev:
            self.monitor.start(monitor_dev)
        else:
            self.monitor.stop()

    def play(self, path: str, gain: float = 1.0) -> None:
        """Play a sound file on both mixers (non-blocking)."""
        with self._cache_lock:
            if path not in self._cache:
                self._cache[path] = load_sound(path)
            data = self._cache[path]

        if self.cable.stream:
            self.cable.add(data, gain)
        if self.monitor.stream:
            self.monitor.add(data, gain)

    def stop_all(self) -> None:
        """Stop all currently-playing sounds on both mixers."""
        self.cable.stop_all()
        self.monitor.stop_all()

    def set_volume(self, volume: float, mic_volume: float) -> None:
        """Update master and mic volumes on both mixers."""
        for m in (self.cable, self.monitor):
            m.volume = volume
        self.cable.mic_volume = mic_volume

    def clear_cache(self) -> None:
        """Evict all cached audio (forces re-load on next play)."""
        with self._cache_lock:
            self._cache.clear()

    def shutdown(self) -> None:
        """Gracefully stop all streams."""
        self.cable.stop()
        self.monitor.stop()
        logger.info("Engine shut down.")
