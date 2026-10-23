"""CallSession: one call's worth of state. Transport-agnostic (no sockets, no clocks).

Every input (scripted transcript line, manual text, audio window, injected voice evidence) goes
through the SAME path: analysis -> risk engine -> alert manager -> outbound messages. That is why
scripted demos and live audio behave identically and are comparable.

Messages returned are JSON-serialisable dicts with a monotonically increasing `seq`.
"""
from __future__ import annotations

import re
from collections import deque
from statistics import mean
from typing import Any

from . import audio_ingestion as ai
from .alert_manager import AlertManager
from .config import level_rank, load_rules
from .fraud_analysis import ScamAnalyzer
from .models import SRC_MANUAL, SRC_REAL, SRC_SCRIPTED, SRC_SIMULATED, TranscriptLine, VoiceResult
from .risk_engine import RiskEngine, _fmt
from .transcription import Transcriber, UnavailableTranscriber
from .voice_analysis import DetectorRegistry, detect_synthetic_speech, detect_voice_manipulation

_JUNK = re.compile(r"(?:\s*/+\s*){2,}|\.{4,}|[♪♫]+")


def clean_transcript(text: str) -> str:
    """Drop typical speech-model artifacts (repeated slashes, music notes) and near-empty output."""
    text = _JUNK.sub(" ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if sum(ch.isalpha() for ch in text) < 2:
        return ""
    return text


def split_complete(text: str) -> tuple[str, str]:
    """Split text into (complete sentences, trailing fragment without end punctuation)."""
    m = list(re.finditer(r"[.!?]+[\"')\]]*(?:\s|$)", text))
    if not m:
        return "", text.strip()
    cut = m[-1].end()
    return text[:cut].strip(), text[cut:].strip()


class CallSession:
    def __init__(
        self,
        rules: dict[str, Any] | None = None,
        registry: DetectorRegistry | None = None,
        transcriber: Transcriber | None = None,
        quiet_mode: bool | None = None,
    ):
        self.rules = rules or load_rules()
        self.registry = registry or DetectorRegistry()
        self.transcriber = transcriber or UnavailableTranscriber()
        self.analyzer = ScamAnalyzer(self.rules)
        self.engine = RiskEngine(self.rules)
        self.alerts = AlertManager(self.rules, quiet_mode)
        self.buffer = ai.RollingBuffer()
        self.tx_buffer = ai.RollingBuffer(window_s=5.0, hop_s=5.0)
        self._carry = ""
        self._recent: dict[str, deque[VoiceResult]] = {"synthetic": deque(maxlen=5), "manipulation": deque(maxlen=5)}
        self.mode = "idle"
        self.state = "idle"
        self.t = 0.0
        self._seq = 0
        self._tl = 0
        self._ln = 0
        self._voice_status: dict[str, str] = {}
        self.history: list[dict[str, Any]] = []  # every message emitted this call

    # ---- helpers -------------------------------------------------------------------------
    def _emit(self, msgs: list[dict[str, Any]], msg: dict[str, Any]) -> None:
        self._seq += 1
        msg["seq"] = self._seq
        msgs.append(msg)
        self.history.append(msg)

    def _timeline(self, msgs: list[dict[str, Any]], t: float, kind: str, level: str, title: str, detail: str,
                  source: str, method: str = "") -> None:
        self._tl += 1
        entry = {"id": f"e{self._tl}", "t": round(t, 2), "clock": _fmt(t), "kind": kind, "level": level,
                 "title": title, "detail": detail, "source": source, "method": method}
        self._emit(msgs, {"type": "timeline", "entry": entry})

    def capabilities(self) -> dict[str, Any]:
        return {"detectors": self.registry.status(), "transcription": self.transcriber.status()}

    # ---- lifecycle -----------------------------------------------------------------------
    def start(self, mode: str, info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.analyzer.reset()
        self.engine.reset()
        self.alerts.reset()
        self.buffer = ai.RollingBuffer()
        self.tx_buffer = ai.RollingBuffer(window_s=5.0, hop_s=5.0)
        self._carry = ""
        self._recent = {"synthetic": deque(maxlen=5), "manipulation": deque(maxlen=5)}
        self._seq = self._tl = self._ln = 0
        self._voice_status = {}
        self.history = []
        self.mode, self.state, self.t = mode, "running", 0.0
        msgs: list[dict[str, Any]] = []
        self._emit(msgs, {"type": "call_state", "state": "running", "mode": mode, "t": 0.0, "info": info or {},
                          "capabilities": self.capabilities()})
        label = {"scenario": "Scripted demo call started", "live-mic": "Live microphone analysis started",
                 "webrtc": "In-app WebRTC call connected"}.get(mode, "Call started")
        self._timeline(msgs, 0.0, "call", "low", label, (info or {}).get("title", ""),
                       SRC_SCRIPTED if mode == "scenario" else SRC_REAL)
        self._emit(msgs, {"type": "risk", "assessment": self.engine.assess(0.0).to_dict()})
        return msgs

    def end(self, t: float | None = None) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if self.state != "running":
            return msgs
        t = self.t if t is None else t
        if self._carry:  # speech that never got an end-of-sentence mark is still analysed
            msgs.extend(self.ingest_transcript(t, "caller", self._carry, SRC_REAL, self.transcriber.name))
            self._carry = ""
        self.state = "ended"
        a = self.engine.assess(t)
        self._emit(msgs, {"type": "call_state", "state": "ended", "mode": self.mode, "t": round(t, 2),
                          "summary": {"level": a.level, "alerts": sum(1 for m in self.history if m["type"] == "alert")}})
        self._timeline(msgs, t, "call", "low", "Call ended", f"Final conversation risk: {a.level}", SRC_REAL)
        return msgs

    def set_quiet(self, quiet: bool) -> None:
        self.alerts.set_quiet(quiet)

    # ---- inputs --------------------------------------------------------------------------
    def ingest_transcript(self, t: float, speaker: str, text: str, source: str = SRC_SCRIPTED,
                          method: str = "") -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        text = (text or "").strip()
        if not text:
            return msgs
        self.t = max(self.t, t)
        self._ln += 1
        line = TranscriptLine(f"l{self._ln}", round(t, 2), speaker, text, source, method)
        self._emit(msgs, {"type": "transcript", "line": line.to_dict()})
        findings = self.analyzer.analyze(text, t, speaker, source if source in (SRC_SCRIPTED, SRC_MANUAL) else SRC_SCRIPTED)
        self.engine.add_findings(findings)
        for f in findings:
            self._emit(msgs, {"type": "finding", "finding": f.to_dict()})
            self._timeline(msgs, f.t, "finding", f.severity, f.label, f"“{f.excerpt}”", f.source, f.method)
        self._assess(msgs, t)
        return msgs

    def ingest_voice(self, result: VoiceResult) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        self.t = max(self.t, result.t)
        self.engine.add_voice(result)
        self._emit(msgs, {"type": "voice", "result": result.to_dict()})
        key = f"{result.status}:{result.source}"
        if self._voice_status.get(result.dimension) != key and result.status in ("indicators", "unavailable"):
            name = "Synthetic-speech" if result.dimension == "synthetic" else "Voice-manipulation"
            if result.status == "indicators":
                title = f"{name} indicators"
                level = "elevated"
            else:
                title = f"{name} detector unavailable"
                level = "low"
            tag = " (simulated by scenario)" if result.source == SRC_SIMULATED else ""
            self._timeline(msgs, result.t, "voice", level, title + tag, "; ".join(result.evidence), result.source, result.method)
        self._voice_status[result.dimension] = key
        self._assess(msgs, result.t)
        return msgs

    def ingest_audio_chunk(self, data: bytes, t_hint: float | None = None) -> list[dict[str, Any]]:
        """Live audio path: validate, window, run real detectors + (optional) transcription."""
        msgs: list[dict[str, Any]] = []
        try:
            samples = ai.decode_pcm16(data)
        except ai.AudioError as exc:
            self._emit(msgs, {"type": "error", "code": exc.code, "message": exc.message})
            return msgs
        if self.state != "running":
            return msgs
        before = self.buffer.total_s
        self._emit(msgs, {"type": "audio_metrics", "t": round(before + len(samples) / ai.SAMPLE_RATE, 2),
                          **ai.metrics(samples)})
        for w in self.buffer.push(samples):
            msgs.extend(self.ingest_audio_window(w))
        if self.transcriber.available:  # separate, non-overlapping 5 s windows so lines are not repeated
            for tw in self.tx_buffer.push(samples):
                msgs.extend(self.transcribe_window(tw))
        return msgs

    def ingest_audio_window(self, w: ai.AudioWindow) -> list[dict[str, Any]]:
        """Run the two independent voice detectors on one window."""
        msgs: list[dict[str, Any]] = []
        for det, res in ((self.registry.synthetic, detect_synthetic_speech(w, self.registry)),
                         (self.registry.manipulation, detect_voice_manipulation(w, self.registry))):
            res.t = round(w.t_end, 2)
            if res.status == "unavailable" and self.engine.voice[res.dimension] is not None:
                continue  # "no detector" is reported once, and never replaces an existing result
            if getattr(det, "needs_smoothing", False):
                res = self._smooth(res)
            msgs.extend(self.ingest_voice(res))
        return msgs

    def _smooth(self, res: VoiceResult, min_windows: int = 3, min_hits: int = 3) -> VoiceResult:
        """Noisy per-window models: report 'indicators' only when most recent windows agree."""
        if res.status not in ("indicators", "clear") or res.score is None:
            return res
        recent = self._recent[res.dimension]
        recent.append(res)
        if len(recent) < min_windows:
            return VoiceResult(res.dimension, "inconclusive", None, 0.0,
                               [f"Collecting audio windows ({len(recent)}/{min_windows}) before reporting."],
                               res.method, res.limitations, res.source, res.t, res.experimental)
        hits = [r for r in recent if r.status == "indicators"]
        if len(hits) >= min_hits:
            return VoiceResult(res.dimension, "indicators", round(mean(r.score for r in hits), 3),
                               round(mean(r.confidence for r in hits), 3),
                               [f"{len(hits)} of the last {len(recent)} audio windows were above the model's boundary."],
                               res.method, res.limitations, res.source, res.t, res.experimental)
        return VoiceResult(res.dimension, "clear", round(mean(r.score for r in recent), 3),
                           round(mean(r.confidence for r in recent), 3),
                           [f"Only {len(hits)} of the last {len(recent)} windows were above the model's boundary."],
                           res.method, res.limitations, res.source, res.t, res.experimental)

    def transcribe_window(self, w: ai.AudioWindow) -> list[dict[str, Any]]:
        """Local speech recognition for one window. Sentence fragments are carried into the next window
        so that a request split across two windows is still analysed as one sentence."""
        msgs: list[dict[str, Any]] = []
        text = ""
        if ai.assess_quality(w) is None:
            try:
                text = clean_transcript(self.transcriber.transcribe(w))
            except Exception as exc:  # noqa: BLE001
                self._emit(msgs, {"type": "error", "code": "transcription_failed", "message": str(exc)})
        combined = f"{self._carry} {text}".strip()
        if not text:  # silence: whatever was pending is now a finished utterance
            complete, rest = combined, ""
        else:
            complete, rest = split_complete(combined)
            if len(rest) > 200:
                complete, rest = combined, ""
        self._carry = rest
        if complete:
            msgs.extend(self.ingest_transcript(w.t_end, "caller", complete, SRC_REAL, self.transcriber.name))
        return msgs

    # ---- core ----------------------------------------------------------------------------
    def _assess(self, msgs: list[dict[str, Any]], t: float) -> None:
        a = self.engine.assess(t)
        self._emit(msgs, {"type": "risk", "assessment": a.to_dict()})
        d = self.alerts.decide(a)
        if d.alert:
            self._emit(msgs, {"type": "alert", "alert": d.alert})
            al = d.alert
            ping = "ping" if al["sound"] else "silent"
            self._timeline(msgs, t, "alert", al["level"], f"{al['headline'].title() if al['level'] == 'elevated' else al['headline']} "
                           f"[{al['level']} · {al['trigger']} · {ping}]", al["reason"], SRC_REAL, "alert-manager")

    def snapshot(self) -> dict[str, Any]:
        """Final summary used by tests and the scenario checker."""
        alerts = [m["alert"] for m in self.history if m["type"] == "alert"]
        risks = [m["assessment"] for m in self.history if m["type"] == "risk"]
        return {
            "max_level": max((r["level"] for r in risks), key=level_rank, default="low"),
            "final_level": risks[-1]["level"] if risks else "low",
            "alerts": alerts,
            "pings": [a for a in alerts if a["sound"]],
            "findings": [m["finding"] for m in self.history if m["type"] == "finding"],
        }
