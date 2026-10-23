"""Pluggable transcription. Status is always explicit so the UI never fakes live captions.

Backends
* unavailable    - default when no local model is installed (live mic gives no transcript;
                   use scripted scenarios or the manual transcript box).
* faster-whisper - optional local model (no network after the model is downloaded).
"""
from __future__ import annotations

import os
from typing import Protocol

import numpy as np

from .audio_ingestion import AudioWindow


class Transcriber(Protocol):
    name: str
    available: bool

    def status(self) -> dict: ...
    def transcribe(self, window: AudioWindow) -> str: ...


class UnavailableTranscriber:
    name = "none"
    available = False

    def __init__(self, reason: str = "No local speech-recognition model installed."):
        self.reason = reason

    def status(self) -> dict:
        return {"state": "unavailable", "name": self.name,
                "detail": self.reason + " Use a scripted scenario or the manual transcript box."}

    def transcribe(self, window: AudioWindow) -> str:
        return ""


class FasterWhisperTranscriber:
    available = True

    def __init__(self, model_size: str = "base.en"):
        from faster_whisper import WhisperModel  # lazy: optional dependency

        self.name = f"faster-whisper:{model_size}"
        self.model = WhisperModel(model_size, device=os.environ.get("TRINETRA_WHISPER_DEVICE", "cpu"), compute_type="int8")

    def status(self) -> dict:
        return {"state": "ready", "name": self.name, "detail": "Local model, English only. Audio stays on this machine."}

    def transcribe(self, window: AudioWindow) -> str:
        segments, _ = self.model.transcribe(window.samples.astype(np.float32), language="en", vad_filter=True,
                                            beam_size=1, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


def make_transcriber() -> Transcriber:
    if os.environ.get("TRINETRA_TRANSCRIBER", "auto") == "none":
        return UnavailableTranscriber("Disabled by TRINETRA_TRANSCRIBER=none.")
    try:
        return FasterWhisperTranscriber(os.environ.get("TRINETRA_WHISPER_MODEL", "base.en"))
    except Exception as exc:  # noqa: BLE001
        return UnavailableTranscriber(f"faster-whisper not installed or failed to load ({type(exc).__name__}).")
