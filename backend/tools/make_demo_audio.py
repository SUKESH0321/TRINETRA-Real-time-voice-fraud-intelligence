"""Generates the demo audio clips with the Windows built-in text-to-speech voices (Windows only).

* The clips are genuinely synthetic speech, produced locally. No third-party recordings are used.
* Scenario transcript times are rewritten to match when each line is actually spoken.
* "manipulated-audio" is built by JOINING two segments that have different background noise.
  It is a constructed demo sample, not a real attack.

Usage:  python tools/make_demo_audio.py        (from the backend folder, any Python with numpy)
The generated .wav files and updated scenario JSON are committed, so end users do not need to run this.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "app" / "scenario_data"
AUDIO = DATA / "audio"
PS1 = Path(__file__).resolve().parent / "tts.ps1"
SR = 16000
LEAD_IN, GAP, TAIL = 1.0, 1.3, 4.0

# scenario id -> (voice, wav name)
JOBS = {
    "synthetic-legit": ("Microsoft Zira Desktop", "synthetic_appointment.wav"),
    "synthetic-relative-scam": ("Microsoft David Desktop", "synthetic_relative_scam.wav"),
    "manipulated-audio": ("Microsoft Zira Desktop", "manipulated_splice.wav"),
}
rng = np.random.default_rng(1234)


def synth(text: str, voice: str, out: Path) -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        txt = Path(td) / "t.txt"
        txt.write_text(text, encoding="utf-8")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), "-TextFile", str(txt),
             "-Out", str(out), "-Voice", voice],
            check=True, capture_output=True,
        )
    with wave.open(str(out), "rb") as wf:
        assert wf.getframerate() == SR and wf.getnchannels() == 1 and wf.getsampwidth() == 2
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def noise(seconds: float, amp: float) -> np.ndarray:
    n = int(seconds * SR)
    white = rng.normal(0, 1, n)
    # gently low-passed so it sounds like room noise rather than hiss
    k = np.ones(4) / 4
    return (np.convolve(white, k, mode="same") * amp).astype(np.float32)


def write_wav(path: Path, x: np.ndarray) -> None:
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((x * 32767).astype("<i2").tobytes())


def build(sid: str, voice: str, wav_name: str) -> None:
    path = next(p for p in DATA.glob("*.json") if json.loads(p.read_text(encoding="utf-8"))["id"] == sid)
    sc = json.loads(path.read_text(encoding="utf-8"))
    lines = [e for e in sc["events"] if e["type"] == "transcript"]
    AUDIO.mkdir(parents=True, exist_ok=True)
    pieces: list[np.ndarray] = []
    starts: list[float] = []
    cursor = 0.0
    manip = sid == "manipulated-audio"
    split_after = 2  # first two lines come from "recording A", the rest from "recording B"
    splice_t = None
    with tempfile.TemporaryDirectory() as td:
        for i, ev in enumerate(lines):
            speech = synth(ev["text"], voice, Path(td) / f"l{i}.wav")
            floor = (0.0015 if i < split_after else 0.02) if manip else 0.0015
            lead = LEAD_IN if i == 0 else GAP
            if manip and i == split_after:
                splice_t = cursor  # the join happens where recording B starts
            pieces.append(noise(lead, floor))
            cursor += lead
            starts.append(round(cursor, 1))
            seg = speech + noise(len(speech) / SR, floor)  # background noise under the speech as well
            pieces.append(seg)
            cursor += len(seg) / SR
        floor_end = 0.02 if manip else 0.0015
        pieces.append(noise(TAIL, floor_end))
        cursor += TAIL
    audio = np.concatenate(pieces)
    peak = float(np.max(np.abs(audio)))
    audio = audio / peak * 0.8 if peak > 0 else audio
    write_wav(AUDIO / wav_name, audio)

    for ev, t in zip(lines, starts):
        ev["t"] = t
    for ev in sc["events"]:
        if ev["type"] == "voice":
            ev["t"] = round((splice_t or 0) + 1.5, 1) if manip else round(starts[0] + 3.0, 1)
            if manip:
                ev["evidence"] = [f"Scenario-labelled: this clip was edited by joining two recordings (noise floor changes around {int(splice_t // 60):02d}:{int(splice_t % 60):02d})."]
    sc["events"].sort(key=lambda e: e["t"])
    sc["duration"] = int(np.ceil(cursor))
    sc["audio"] = wav_name
    if manip and splice_t is not None:
        sc["summary"] = f"A benign delivery message whose audio was edited: two recordings with different background noise are joined at about {int(splice_t // 60):02d}:{int(splice_t % 60):02d}."
    path.write_text(json.dumps(sc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{sid}: {cursor:.1f}s -> {wav_name}  line starts {starts}")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("make_demo_audio.py needs the Windows built-in voices (SAPI).")
    for sid, (voice, wav) in JOBS.items():
        build(sid, voice, wav)
