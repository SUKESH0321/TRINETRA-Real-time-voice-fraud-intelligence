"""Scripted demo scenarios. Deterministic: events fire at fixed call-times from JSON files.

Each event is either
  * transcript - a scripted transcript line (source=scripted); analysed by the REAL scam analyzer
  * voice      - injected voice evidence (source=simulated); NOT a detector output
and, when a scenario has an audio clip and a real detector is installed, the clip is also
analysed in 3 s windows by the real detectors (source=real).
Everything flows through CallSession, the same path live audio uses.
"""
from __future__ import annotations

import asyncio
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from .audio_ingestion import AudioWindow, RollingBuffer, SAMPLE_RATE
from .models import SRC_SCRIPTED, SRC_SIMULATED, SRC_UNAVAILABLE, VoiceResult
from .session import CallSession

SCENARIO_DIR = Path(__file__).resolve().parent / "scenario_data"
AUDIO_DIR = SCENARIO_DIR / "audio"


@dataclass
class Scenario:
    id: str
    data: dict[str, Any]

    @property
    def audio_path(self) -> Path | None:
        name = self.data.get("audio")
        p = AUDIO_DIR / name if name else None
        return p if p and p.exists() else None

    def public(self) -> dict[str, Any]:
        d = self.data
        return {
            "id": self.id,
            "title": d["title"],
            "summary": d["summary"],
            "voice_kind": d["voice_kind"],
            "voice_note": d.get("voice_note", ""),
            "duration": d["duration"],
            "has_audio": self.audio_path is not None,
            "audio_url": f"/api/scenarios/{self.id}/audio" if self.audio_path else None,
            "expected": d["expected"],
            "injected": [e for e in d["events"] if e["type"] == "voice"],
            "bonus": d.get("bonus", False),
            "order": d.get("order", 99),
        }


def load_scenarios(directory: Path | None = None) -> dict[str, Scenario]:
    out: dict[str, Scenario] = {}
    for p in sorted((directory or SCENARIO_DIR).glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out[data["id"]] = Scenario(data["id"], data)
    return dict(sorted(out.items(), key=lambda kv: kv[1].data.get("order", 99)))


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1 or wf.getframerate() != SAMPLE_RATE:
            raise ValueError(f"{path.name}: demo audio must be 16 kHz mono 16-bit PCM")
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def build_timeline(s: Scenario, session: CallSession) -> list[dict[str, Any]]:
    """Merge scripted events with real audio-window analysis events, sorted by call time."""
    events = [dict(e) for e in s.data["events"]]
    # Real analysis of the clip, only when a detector is installed. Otherwise one 'unavailable' note each.
    reg = session.registry
    path = s.audio_path
    if path is not None and (reg.synthetic.available or reg.manipulation.available):
        samples = read_wav(path)
        buf = RollingBuffer()
        step = SAMPLE_RATE // 2
        for i in range(0, len(samples), step):
            for w in buf.push(samples[i : i + step]):
                events.append({"t": round(w.t_end, 2), "type": "audio_window", "window": w})
    events.sort(key=lambda e: (e["t"], 0 if e["type"] != "audio_window" else 1))
    return events


def initial_voice_notes(s: Scenario, session: CallSession) -> list[VoiceResult]:
    """At t=0: tell the UI what the real detectors can/can't do for this scenario."""
    reg = session.registry
    has_audio = s.audio_path is not None
    out = []
    for dim, det in (("synthetic", reg.synthetic), ("manipulation", reg.manipulation)):
        if has_audio and det.available:
            continue  # real windows will report
        why = ("This scenario has no audio clip; nothing to analyse." if not has_audio
               else f"No {dim} detector is installed.")
        out.append(VoiceResult(dim, "unavailable", None, 0.0, [why], "unavailable",
                               ["Scenario-injected evidence (if any) is labelled 'simulated' and is not a detector output."],
                               SRC_UNAVAILABLE, 0.0))
    return out


def apply_event(session: CallSession, ev: dict[str, Any]) -> list[dict[str, Any]]:
    if ev["type"] == "transcript":
        return session.ingest_transcript(ev["t"], ev.get("speaker", "caller"), ev["text"], SRC_SCRIPTED, "scenario-script")
    if ev["type"] == "voice":
        res = VoiceResult(
            dimension=ev["dimension"],
            status=ev["status"],
            score=ev.get("score"),
            confidence=ev.get("confidence", 0.8),
            evidence=ev.get("evidence", []),
            method="scenario-injected",
            limitations=["Injected by the demo scenario to illustrate this signal. It is not the output of a detector."],
            source=SRC_SIMULATED,
            t=ev["t"],
        )
        return session.ingest_voice(res)
    if ev["type"] == "audio_window":
        w: AudioWindow = ev["window"]
        return session.ingest_audio_window(w)
    return []


def run_sync(session: CallSession, s: Scenario) -> list[dict[str, Any]]:
    """Run the whole scenario instantly. Used by tests and the CLI check; identical logic to play()."""
    msgs = session.start("scenario", {"scenario_id": s.id, "title": s.data["title"], "duration": s.data["duration"]})
    for note in initial_voice_notes(s, session):
        msgs.extend(session.ingest_voice(note))
    for ev in build_timeline(s, session):
        msgs.extend(apply_event(session, ev))
    msgs.extend(session.end(s.data["duration"]))
    return msgs


Send = Callable[[dict[str, Any]], Awaitable[None]]


async def play(session: CallSession, s: Scenario, send: Send, speed: float = 1.0) -> None:
    """Real-time playback. Messages are identical to run_sync; only the pacing differs."""
    speed = max(0.25, min(speed, 20.0))
    for m in session.start("scenario", {"scenario_id": s.id, "title": s.data["title"], "speed": speed,
                                         "duration": s.data["duration"]}):
        await send(m)
    for note in initial_voice_notes(s, session):
        for m in session.ingest_voice(note):
            await send(m)
    now = 0.0
    for ev in build_timeline(s, session):
        if ev["t"] > now:
            await asyncio.sleep((ev["t"] - now) / speed)
            now = ev["t"]
        for m in apply_event(session, ev):
            await send(m)
    if s.data["duration"] > now:
        await asyncio.sleep((s.data["duration"] - now) / speed)
    for m in session.end(s.data["duration"]):
        await send(m)
