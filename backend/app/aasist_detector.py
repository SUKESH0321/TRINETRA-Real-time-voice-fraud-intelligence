"""Optional synthetic-speech detector: AASIST (pretrained anti-spoofing model, NAVER Corp., MIT licence).

Not bundled. `scripts/setup` / the README ("Optional ML") fetch the reference implementation and
weights into backend/models/aasist and install CPU PyTorch. If any piece is missing,
`try_load()` returns None and TRINETRA reports the synthetic detector as unavailable.

What it is: a graph-attention network trained on ASVspoof 2019 LA (English, studio-quality speech,
spoofing systems from ~2019). What it is NOT: a validated detector for modern TTS, phone-quality
audio, noisy rooms or other languages. Its output is an uncalibrated score and is reported as such.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import numpy as np

from .audio_ingestion import AudioWindow, assess_quality
from .models import SRC_REAL, VoiceResult

NB_SAMP = 64600  # ~4.04 s at 16 kHz: the model's fixed input length


class AasistDetector:
    dimension = "synthetic"
    name = "AASIST (ASVspoof 2019 LA)"
    available = True
    needs_smoothing = True  # per-window output is noisy; the session requires agreement across windows
    LIMITATIONS = [
        "Pretrained on ASVspoof 2019 LA (English, studio audio, ~2019 spoofing systems); generalises poorly to modern TTS, phone codecs, noise and other languages.",
        "The score is an uncalibrated model output, not a probability that the caller is synthetic.",
        "A positive result is not proof of fraud; a negative result does not prove the voice is human.",
        "Short windows are repeated to reach the model's fixed 4 s input length.",
    ]

    def __init__(self, model, torch_module):
        self.model = model
        self.torch = torch_module

    @classmethod
    def default_dir(cls) -> Path:
        return Path(os.environ.get("TRINETRA_AASIST_DIR", Path(__file__).resolve().parent.parent / "models" / "aasist"))

    @classmethod
    def try_load(cls) -> "AasistDetector | None":
        if os.environ.get("TRINETRA_AASIST", "auto") == "off":
            return None
        root = cls.default_dir()
        code, conf, weights = root / "models" / "AASIST.py", root / "config" / "AASIST.conf", root / "models" / "weights" / "AASIST.pth"
        if not (code.exists() and conf.exists() and weights.exists()):
            return None
        import torch  # optional dependency

        spec = importlib.util.spec_from_file_location("aasist_reference", code)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = json.loads(conf.read_text(encoding="utf-8"))["model_config"]
        model = module.Model(cfg)
        model.load_state_dict(torch.load(weights, map_location="cpu"))
        model.eval()
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        return cls(model, torch)

    def detect(self, window: AudioWindow) -> VoiceResult:
        why = assess_quality(window)
        if why:
            return VoiceResult(self.dimension, "inconclusive", None, 0.0, [why], self.name, self.LIMITATIONS, SRC_REAL, window.t_start)
        x = window.samples.astype(np.float32)
        if len(x) < NB_SAMP:  # same padding strategy as the reference implementation: repeat
            x = np.tile(x, int(np.ceil(NB_SAMP / len(x))))
        x = x[:NB_SAMP]
        torch = self.torch
        with torch.no_grad():
            _, logits = self.model(torch.from_numpy(x).unsqueeze(0))
            p = torch.softmax(logits, dim=-1)[0]
        spoof = float(p[0])  # AASIST label convention: index 1 = bonafide, 0 = spoof
        confidence = round(abs(2 * spoof - 1), 3)
        status = "indicators" if spoof >= 0.5 else "clear"
        ev = [f"Model spoof score {spoof:.2f} (uncalibrated; 0.5 is the model's own decision boundary)."]
        return VoiceResult(self.dimension, status, round(spoof, 3), confidence, ev, self.name, self.LIMITATIONS,
                           SRC_REAL, window.t_start)
