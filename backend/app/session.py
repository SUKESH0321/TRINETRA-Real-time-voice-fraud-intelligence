"""CallSession: one call's worth of state. Transport-agnostic (no sockets, no clocks).

Every input (scripted transcript line, manual text, audio window, injected voice evidence) goes
through the SAME path: analysis -> risk engine -> alert manager -> outbound messages. That is why
scripted demos and live audio behave identically and are comparable.

Messages returned are JSON-serialisable dicts with a monotonically increasing `seq`.
"""
from __future__ import annotations

import re
import uuid
from collections import deque
from statistics import mean
from typing import Any

import numpy as np

from . import audio_ingestion as ai
from .alert_manager import AlertManager
from .config import level_rank, load_rules
from .fraud_analysis import ScamAnalyzer
from .models import SRC_MANUAL, SRC_REAL, SRC_SCRIPTED, SRC_SIMULATED, TranscriptLine, VoiceResult
from .risk_engine import RiskEngine, _fmt
from .transcription import StreamingTranscriber, StepResult, Transcriber, TranscriptionUnavailable, UnavailableTranscriber
from .voice_analysis import DetectorRegistry, detect_synthetic_speech, detect_voice_manipulation

_JUNK = re.compile(r"(?:\s*/+\s*){2,}|\.{4,}|[♪♫]+")


def clean_transcript(text: str) -> str:
    """Drop typical speech-model artifacts (repeated slashes, music notes) and near-empty output."""
    text = _JUNK.sub(" ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if sum(ch.isalpha() for ch in text) < 2:
        return ""
    return text


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
        self.asr = StreamingTranscriber(self.transcriber)
        self.session_id = ""
        self._pending_windows: deque[ai.AudioWindow] = deque(maxlen=2)  # detector windows; oldest dropped if we fall behind
        self._reset_diag()
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

    def _reset_diag(self) -> None:
        self.diag: dict[str, Any] = {"frames": 0, "audio_s": 0.0, "rms": 0.0, "quiet_s": 0.0, "decodes": 0, "decode_ms": 0,
                                      "buffer_s": 0.0, "finals": 0, "windows_skipped": 0, "invalid_chunks": 0}
        self._asr_state = ""
        self._asr_error = ""
        self._last_partial = ""

    @property
    def audio_s(self) -> float:
        """Call clock for live modes: seconds of audio actually received."""
        return self.buffer.total_s

    def capabilities(self) -> dict[str, Any]:
        return {"detectors": self.registry.status(), "transcription": self.transcriber.status()}

    # ---- lifecycle -----------------------------------------------------------------------
    def start(self, mode: str, info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.analyzer.reset()
        self.engine.reset()
        self.alerts.reset()
        self.buffer = ai.RollingBuffer()
        self.asr = StreamingTranscriber(self.transcriber)
        self._pending_windows.clear()
        self._reset_diag()
        self.session_id = str((info or {}).get("scenario_id") or "") if mode == "scenario" else uuid.uuid4().hex[:8]
        self._recent = {"synthetic": deque(maxlen=5), "manipulation": deque(maxlen=5)}
        self._seq = self._tl = self._ln = 0
        self._voice_status = {}
        self.history = []
        self.mode, self.state, self.t = mode, "running", 0.0
        msgs: list[dict[str, Any]] = []
        self._emit(msgs, {"type": "call_state", "state": "running", "mode": mode, "t": 0.0, "info": info or {},
                          "session": self.session_id, "capabilities": self.capabilities()})
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
        if self.mode in ("live-mic", "webrtc"):
            msgs.extend(self.flush_transcription())  # decode the last buffered speech before closing
            t = self.audio_s if t is None else max(t, self.audio_s)
        t = self.t if t is None else t
        self.state = "ended"
        a = self.engine.assess(t)
        peak = max((m["assessment"]["level"] for m in self.history if m["type"] == "risk"), key=level_rank, default=a.level)
        self._emit(msgs, {"type": "call_state", "state": "ended", "mode": self.mode, "t": round(t, 2),
                          "summary": {"level": a.level, "peak": peak, "alerts": sum(1 for m in self.history if m["type"] == "alert")}})
        detail = f"Final conversation risk: {a.level}" + (f" (peak during the call: {peak})" if peak != a.level else "")
        self._timeline(msgs, t, "call", "low", "Call ended", detail, SRC_REAL)
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
        line = TranscriptLine(f"l{self._ln}", round(t, 2), speaker, text, source, method, self.session_id)
        self._emit(msgs, {"type": "transcript", "line": line.to_dict()})
        findings = self.analyzer.analyze(text, t, speaker, source)
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
        return self.ingest_audio_chunks([data])

    def ingest_audio_chunks(self, chunks: list[bytes], process: bool = True) -> list[dict[str, Any]]:
        """Live audio path. Validates and buffers every chunk (never drops audio), then, if `process`, runs the
        slow work (voice detectors, transcription). The server batches chunks when processing falls behind."""
        msgs: list[dict[str, Any]] = []
        if self.state != "running":
            return msgs
        parts = []
        for data in chunks:
            try:
                parts.append(ai.decode_pcm16(data))
            except ai.AudioError as exc:
                self.diag["invalid_chunks"] += 1
                self._emit(msgs, {"type": "error", "code": exc.code, "message": exc.message})
        if parts:
            samples = np.concatenate(parts)
            self.diag["frames"] += len(parts)
            self.diag["audio_s"] = round(self.diag["audio_s"] + len(samples) / ai.SAMPLE_RATE, 2)
            m = ai.metrics(samples)
            self.diag["rms"] = m["rms"]
            self.diag["quiet_s"] = round(self.diag["quiet_s"] + len(samples) / ai.SAMPLE_RATE, 2) if m["rms"] < ai.SILENCE_RMS else 0.0
            for w in self.buffer.push(samples):
                if len(self._pending_windows) == self._pending_windows.maxlen:
                    self.diag["windows_skipped"] += 1
                self._pending_windows.append(w)
            if self.transcriber.available:  # audio is only kept for recognition while a model can use it
                self.asr.append(samples)
            self._emit(msgs, {"type": "audio_metrics", "t": round(self.buffer.total_s, 2), **m})
        if process:
            msgs.extend(self.process())
        return msgs

    def process(self) -> list[dict[str, Any]]:
        """Slow work for the audio received so far. The server runs the two compute halves in parallel threads
        (see main.py); this synchronous form is what tests and simple callers use."""
        msgs = self.begin_process()
        voice = self.compute_voice()
        asr = self.compute_asr()
        msgs.extend(self.apply_results(voice, asr))
        return msgs

    def begin_process(self) -> list[dict[str, Any]]:
        """Announce work that is about to start so the UI can show 'Transcribing…' honestly."""
        msgs: list[dict[str, Any]] = []
        if self.transcriber.available and self.asr.due():
            self._emit(msgs, {"type": "transcription_status", "state": "transcribing", "code": "transcribing",
                              "detail": "Transcribing…", "action": "", "name": self.transcriber.name, "diag": dict(self.diag)})
        return msgs

    def compute_voice(self) -> list[VoiceResult]:
        """Run the voice detectors on queued windows. Touches only detector state: safe to run in its own thread."""
        out: list[VoiceResult] = []
        reported_unavailable: set[str] = set()
        while self._pending_windows:
            w = self._pending_windows.popleft()
            for res in self._detect_window(w):
                if res.status == "unavailable":
                    if res.dimension in reported_unavailable or self.engine.voice[res.dimension] is not None:
                        continue  # "no detector" is reported once and never replaces an existing result
                    reported_unavailable.add(res.dimension)
                out.append(res)
        return out

    def compute_asr(self) -> list[tuple[str, Any]]:
        """Decode due speech hops. Touches only the streaming transcriber: safe to run in its own thread."""
        out: list[tuple[str, Any]] = []
        hops = 0
        while self.transcriber.available and self.asr.due() and hops < 4:
            hops += 1
            try:
                out.append(("step", self.asr.step()))
            except TranscriptionUnavailable as exc:
                out.append(("error", ("model_unavailable", str(exc))))
                break
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                out.append(("error", ("transcription_failed", f"{type(exc).__name__}: {exc}"[:240])))
                break
        return out

    def apply_results(self, voice: list[VoiceResult], asr: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        """Fold computed results into session state and produce messages. Runs on a single thread."""
        msgs: list[dict[str, Any]] = []
        for res in voice:
            msgs.extend(self.ingest_voice(res))
        for kind, payload in asr:
            if kind == "step":
                self._asr_error = ""
                msgs.extend(self._apply_step(payload))
            else:
                self._on_asr_error(msgs, *payload)
        if not self.transcriber.available:
            msgs.extend(self._asr_status())
        return msgs

    # ---- speech recognition ----------------------------------------------------------------
    def _asr_status(self, state: str | None = None, error: str = "") -> list[dict[str, Any]]:
        """Emit a transcription_status message when the state or its explanation changed (or, while ready, with fresh diagnostics)."""
        st = self.transcriber.status()
        if state is None:
            state = "ready" if self.transcriber.available else st.get("state", "unavailable")
        key = f"{state}:{error or st.get('detail', '')}"
        out: list[dict[str, Any]] = []
        if key != self._asr_state or state == "ready":
            self._asr_state = key
            self._emit(out, {"type": "transcription_status", "state": state, "code": st.get("code", ""),
                             "detail": error or st.get("detail", ""), "action": st.get("action", ""), "name": st.get("name", ""),
                             "diag": dict(self.diag)})
        return out

    def _on_asr_error(self, msgs: list[dict[str, Any]], code: str, text: str) -> None:
        # keep the audio bounded and keep going: the next hop will try again
        if self.asr.buffer_s > self.asr.max_buffer_s:
            self.asr._trim_to(self.asr.total - self.asr.max_buffer_s)
        self.asr._since = 0.0
        if text != self._asr_error:
            self._asr_error = text
            self._emit(msgs, {"type": "error", "code": code, "message": f"Transcription error: {text}"})
        self._emit(msgs, {"type": "transcription_status", "state": "error", "code": code, "detail": text,
                          "action": "Press Retry. If it keeps failing, check the backend log.", "name": self.transcriber.name,
                          "diag": dict(self.diag)})

    def _apply_step(self, res: StepResult) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        self.diag["buffer_s"] = round(res.buffer_s, 2)
        if res.decoded:
            self.diag["decodes"] += 1
            self.diag["decode_ms"] = res.decode_ms
        for fin in res.finals:
            text = clean_transcript(fin.text)
            if text:
                self.diag["finals"] += 1
                msgs.extend(self.ingest_transcript(fin.t, "caller", text, SRC_REAL, self.transcriber.name))
        partial = clean_transcript(res.partial)
        if partial != self._last_partial:
            self._last_partial = partial
            self._emit(msgs, {"type": "transcript_partial", "text": partial, "t": round(self.asr.total, 2), "session": self.session_id})
        msgs.extend(self._asr_status("ready"))
        return msgs

    def flush_transcription(self) -> list[dict[str, Any]]:
        """Decode whatever audio is still buffered and finalise it. Called when the call ends."""
        msgs: list[dict[str, Any]] = []
        if not self.transcriber.available:
            return msgs
        try:
            res = self.asr.flush()
        except Exception as exc:  # noqa: BLE001
            self._on_asr_error(msgs, "transcription_failed", f"{type(exc).__name__}: {exc}"[:240])
            return msgs
        msgs.extend(self._apply_step(res))
        if self._last_partial:
            self._last_partial = ""
            self._emit(msgs, {"type": "transcript_partial", "text": "", "t": round(self.asr.total, 2), "session": self.session_id})
        return msgs

    def _detect_window(self, w: ai.AudioWindow) -> list[VoiceResult]:
        """Run the two independent voice detectors on one window (smoothing noisy per-window models)."""
        out: list[VoiceResult] = []
        for det, res in ((self.registry.synthetic, detect_synthetic_speech(w, self.registry)),
                         (self.registry.manipulation, detect_voice_manipulation(w, self.registry))):
            res.t = round(w.t_end, 2)
            if getattr(det, "needs_smoothing", False):
                res = self._smooth(res)
            out.append(res)
        return out

    def ingest_audio_window(self, w: ai.AudioWindow) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        for res in self._detect_window(w):
            if res.status == "unavailable" and self.engine.voice[res.dimension] is not None:
                continue  # "no detector" is reported once, and never replaces an existing result
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
