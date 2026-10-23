"""Validates and buffers incoming audio. Live audio is 16 kHz mono 16-bit little-endian PCM."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
MAX_CHUNK_S = 5.0
MIN_ANALYSIS_S = 1.0
SILENCE_RMS = 0.004


class AudioError(ValueError):
    """Invalid audio input. `code` is machine-readable and is sent to the client."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class AudioWindow:
    samples: np.ndarray  # float32 in [-1, 1]
    sample_rate: int
    t_start: float

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0

    @property
    def t_end(self) -> float:
        return self.t_start + self.duration


def decode_pcm16(data: bytes | bytearray | None, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    if data is None or len(data) == 0:
        raise AudioError("empty_audio", "Audio chunk is empty.")
    if sample_rate != SAMPLE_RATE:
        raise AudioError("bad_sample_rate", f"Expected {SAMPLE_RATE} Hz audio, got {sample_rate} Hz.")
    if len(data) % 2:
        raise AudioError("bad_length", "PCM16 audio must have an even number of bytes.")
    samples = np.frombuffer(bytes(data), dtype="<i2").astype(np.float32) / 32768.0
    if len(samples) / sample_rate > MAX_CHUNK_S:
        raise AudioError("chunk_too_long", f"Chunk longer than {MAX_CHUNK_S:.0f}s.")
    return samples


def metrics(samples: np.ndarray) -> dict[str, float]:
    """Real, simple signal metrics. These are measurements, not detections."""
    if len(samples) == 0:
        return {"rms": 0.0, "peak": 0.0, "clipping": 0.0}
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    peak = float(np.max(np.abs(samples)))
    clipping = float(np.mean(np.abs(samples) > 0.99))
    return {"rms": round(rms, 5), "peak": round(peak, 4), "clipping": round(clipping, 5)}


def assess_quality(window: AudioWindow) -> str | None:
    """Return a reason string if the window cannot be analysed meaningfully, else None."""
    if window.duration < MIN_ANALYSIS_S:
        return f"Audio window shorter than {MIN_ANALYSIS_S:.0f}s."
    if not np.all(np.isfinite(window.samples)):
        return "Audio contains invalid values."
    if metrics(window.samples)["rms"] < SILENCE_RMS:
        return "Window is silent or has no speech."
    return None


class RollingBuffer:
    """Collects PCM and emits overlapping analysis windows (default 3 s window, 1 s hop)."""

    def __init__(self, window_s: float = 3.0, hop_s: float = 1.0, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.win = int(window_s * sample_rate)
        self.hop = int(hop_s * sample_rate)
        self._buf = np.zeros(0, dtype=np.float32)
        self._consumed = 0  # samples dropped from the front
        self._next_end = self.win

    @property
    def total_s(self) -> float:
        return (self._consumed + len(self._buf)) / self.sr

    def push(self, samples: np.ndarray) -> list[AudioWindow]:
        self._buf = np.concatenate([self._buf, samples])
        out: list[AudioWindow] = []
        while self._consumed + len(self._buf) >= self._next_end:
            end = self._next_end - self._consumed
            out.append(AudioWindow(self._buf[end - self.win : end].copy(), self.sr, (self._next_end - self.win) / self.sr))
            self._next_end += self.hop
        keep_from = max(0, self._next_end - self.win - self._consumed)
        if keep_from > 0:
            self._buf = self._buf[keep_from:]
            self._consumed += keep_from
        return out
