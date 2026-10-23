"""Voice-authenticity detectors. Two independent interfaces:

    detect_synthetic_speech(window)    -> VoiceResult   (is the voice synthetic?)
    detect_voice_manipulation(window)  -> VoiceResult   (splicing / conversion / editing?)

Honesty rules, enforced here:
* If no real detector is installed the result is status="unavailable" with score=None. No numbers
  are invented. The demo's scripted scenarios inject separately-labelled *simulated* results.
* Heuristics are never presented as trained models. The only built-in heuristic (splice check) is
  marked experimental, has capped confidence, and is OFF unless TRINETRA_EXPERIMENTAL_SPLICE=1.
* The two detectors never read each other's output.
"""
from __future__ import annotations

import os
from typing import Protocol

import numpy as np

from .audio_ingestion import AudioWindow, assess_quality
from .models import SRC_REAL, SRC_UNAVAILABLE, VoiceResult


class Detector(Protocol):
    dimension: str
    name: str
    available: bool

    def detect(self, window: AudioWindow) -> VoiceResult: ...


def _unavailable(dimension: str, method: str, why: str, limitations: list[str], t: float = 0.0) -> VoiceResult:
    return VoiceResult(dimension, "unavailable", None, 0.0, [why], method, limitations, SRC_UNAVAILABLE, t)


class UnavailableDetector:
    available = False

    def __init__(self, dimension: str, reason: str, how_to: str = ""):
        self.dimension = dimension
        self.name = "none"
        self.reason = reason
        self.how_to = how_to

    def detect(self, window: AudioWindow) -> VoiceResult:
        lim = ["No detector is installed, so nothing was measured."]
        if self.how_to:
            lim.append(self.how_to)
        return _unavailable(self.dimension, "unavailable", self.reason, lim, window.t_start)


class SpliceHeuristic:
    """EXPERIMENTAL. Compares the background-noise level of different PAUSES inside one window.
    If two steady pauses differ by a large step (default 12 dB) the window is flagged, because a
    jump in room noise can happen where two recordings are joined.

    It is NOT a trained model. It has only been checked against the demo clips in this repo
    (an untouched clip stays clear; the constructed splice is flagged). It also fires on
    legitimate things such as noise gates, automatic gain control, a fan switching on or the
    microphone being moved, and it says nothing when a window has fewer than two steady pauses."""

    dimension = "manipulation"
    name = "experimental-pause-noise-step"
    available = True
    MAX_CONFIDENCE = 0.4
    FRAMES_PER_RUN = 30  # a "steady pause" lasts at least 300 ms
    FLAT_DB = 6.0  # level may vary by less than this within a steady pause
    LIMITATIONS = [
        "Heuristic, not a trained model; not validated against real voice-conversion or splicing attacks.",
        "Noise gates, automatic gain control, moving the microphone or sudden background noise cause false positives.",
        "Cannot detect clean splices or voice conversion that preserve the noise floor.",
        "Needs at least two steady pauses (>= 300 ms) in the window; otherwise the result is inconclusive.",
        "Cannot identify a speaker or a specific voice clone.",
    ]

    def __init__(self, step_db: float = 12.0):
        self.step_db = step_db

    def _result(self, status: str, score: float | None, ev: list[str], window: AudioWindow) -> VoiceResult:
        conf = self.MAX_CONFIDENCE if status in ("indicators", "clear") else 0.0
        return VoiceResult(self.dimension, status, score, conf, ev, self.name, self.LIMITATIONS, SRC_REAL,
                           window.t_start, True)

    def pause_levels(self, window: AudioWindow) -> list[tuple[float, float]]:
        """Return (time_s, level_dB) for each steady pause found in the window."""
        x = window.samples.astype(np.float64)
        fl, hop = int(0.02 * window.sample_rate), int(0.01 * window.sample_rate)
        n = (len(x) - fl) // hop + 1
        w = self.FRAMES_PER_RUN
        if n < w + 1:
            return []
        frames = np.stack([x[i * hop : i * hop + fl] for i in range(n)])
        db = 10 * np.log10(np.mean(frames**2, axis=1) + 1e-12)
        median_level = np.percentile(db, 50)
        ok = np.array([
            (db[i : i + w].max() - db[i : i + w].min()) < self.FLAT_DB and np.median(db[i : i + w]) < median_level
            for i in range(n - w + 1)
        ])
        runs: list[tuple[float, float]] = []
        i = 0
        while i < len(ok):
            if ok[i]:
                j = i
                while j < len(ok) and ok[j]:
                    j += 1
                runs.append((window.t_start + i * 0.01, float(np.median(db[i : j + w - 1]))))
                i = j
            else:
                i += 1
        return runs

    def detect(self, window: AudioWindow) -> VoiceResult:
        why = assess_quality(window)
        if why:
            return self._result("inconclusive", None, [why], window)
        runs = self.pause_levels(window)
        if len(runs) < 2:
            return self._result("inconclusive", None, ["Fewer than two steady pauses in this window to compare."], window)
        steps = [(abs(runs[k + 1][1] - runs[k][1]), runs[k + 1][0]) for k in range(len(runs) - 1)]
        best, at = max(steps)
        if best >= self.step_db:
            score = float(np.clip((best - self.step_db) / 20.0 + 0.5, 0, 1))
            return self._result("indicators", round(score, 3),
                                [f"Background noise between pauses changed by {best:.1f} dB around {at:.1f}s."], window)
        return self._result("clear", round(float(np.clip(best / (self.step_db * 4), 0, 0.2)), 3),
                            ["Background noise level is consistent between pauses."], window)


class DetectorRegistry:
    """Holds the active detectors. Swap in real models by constructing with different detectors."""

    def __init__(self, synthetic: Detector | None = None, manipulation: Detector | None = None):
        self.synthetic: Detector = synthetic or UnavailableDetector(
            "synthetic",
            "No anti-spoofing model is installed.",
            "Install the optional model (see README: 'Optional ML') to enable real synthetic-speech analysis.",
        )
        self.manipulation: Detector = manipulation or UnavailableDetector(
            "manipulation",
            "No voice-manipulation model is available.",
            "Set TRINETRA_EXPERIMENTAL_SPLICE=1 to enable the experimental noise-floor check.",
        )

    @classmethod
    def from_env(cls) -> "DetectorRegistry":
        synthetic = None
        try:
            from .aasist_detector import AasistDetector  # optional: needs torch + weights

            synthetic = AasistDetector.try_load()
        except Exception:  # noqa: BLE001 - any failure means "unavailable", never a crash
            synthetic = None
        manipulation = SpliceHeuristic() if os.environ.get("TRINETRA_EXPERIMENTAL_SPLICE") == "1" else None
        return cls(synthetic, manipulation)

    def status(self) -> dict[str, dict]:
        return {
            "synthetic": {"available": bool(self.synthetic.available), "name": self.synthetic.name,
                          "experimental": False},
            "manipulation": {"available": bool(self.manipulation.available), "name": self.manipulation.name,
                             "experimental": isinstance(self.manipulation, SpliceHeuristic)},
        }


_default: DetectorRegistry | None = None


def default_registry() -> DetectorRegistry:
    global _default
    if _default is None:
        _default = DetectorRegistry.from_env()
    return _default


def detect_synthetic_speech(window: AudioWindow, registry: DetectorRegistry | None = None) -> VoiceResult:
    try:
        return (registry or default_registry()).synthetic.detect(window)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("synthetic", "error", f"Detector failed: {exc}", ["Detector raised an error; result discarded."], window.t_start)


def detect_voice_manipulation(window: AudioWindow, registry: DetectorRegistry | None = None) -> VoiceResult:
    try:
        return (registry or default_registry()).manipulation.detect(window)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("manipulation", "error", f"Detector failed: {exc}", ["Detector raised an error; result discarded."], window.t_start)
