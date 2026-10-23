import numpy as np
import pytest

from app.audio_ingestion import SAMPLE_RATE
from app.session import CallSession, clean_transcript, split_complete
from app.transcription import UnavailableTranscriber, make_transcriber
from app.voice_analysis import DetectorRegistry

rng = np.random.default_rng(3)


class FakeTranscriber:
    """Returns scripted text per 5 s window; stands in for a real speech model."""

    name, available = "fake-asr", True

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def status(self):
        return {"state": "ready", "name": self.name, "detail": "fake"}

    def transcribe(self, window):
        return self.outputs.pop(0) if self.outputs else ""


def pcm_speechy(seconds):
    n = int(seconds * SAMPLE_RATE)
    x = rng.normal(0, 0.1, n).astype(np.float32)  # loud enough to pass the silence gate
    return (x * 32767).astype("<i2").tobytes()


def feed(session, seconds):
    msgs = []
    for _ in range(int(seconds * 2)):
        msgs += session.ingest_audio_chunk(pcm_speechy(0.5))
    return msgs


def test_clean_transcript_removes_model_artifacts():
    assert clean_transcript("I need a // // // // //") == "I need a"
    assert clean_transcript("♪♪") == ""
    assert clean_transcript("   ") == ""
    assert clean_transcript("Hello   there") == "Hello there"


def test_split_complete():
    assert split_complete("Hi there. I need your") == ("Hi there.", "I need your")
    assert split_complete("no punctuation here") == ("", "no punctuation here")
    assert split_complete("All done!") == ("All done!", "")


def test_request_split_across_windows_is_still_detected():
    s = CallSession(registry=DetectorRegistry(), transcriber=FakeTranscriber(["Hello, this is the bank. Please tell me your", "OTP so I can verify your account."]))
    s.start("live-mic")
    msgs = feed(s, 10.2)
    lines = [m["line"] for m in msgs if m["type"] == "transcript"]
    assert [l["text"] for l in lines] == ["Hello, this is the bank.", "Please tell me your OTP so I can verify your account."]
    assert all(l["source"] == "real" and l["method"] == "fake-asr" for l in lines)
    assert any(m["type"] == "alert" and m["alert"]["level"] == "high" for m in msgs)


def test_overlapping_detector_windows_do_not_repeat_transcript_lines():
    s = CallSession(registry=DetectorRegistry(), transcriber=FakeTranscriber(["Just a normal sentence."] * 3))
    s.start("live-mic")
    msgs = feed(s, 15.2)
    assert len([m for m in msgs if m["type"] == "transcript"]) == 3  # one per 5 s window, not one per 1 s hop


def test_trailing_fragment_is_flushed_when_call_ends():
    s = CallSession(registry=DetectorRegistry(), transcriber=FakeTranscriber(["Please tell me your OTP now"]))
    s.start("live-mic")
    feed(s, 5.2)
    assert not [m for m in s.history if m["type"] == "alert"]  # still waiting for the sentence to finish
    ended = s.end()
    assert any(m["type"] == "alert" for m in ended)


def test_unavailable_transcriber_never_produces_text():
    s = CallSession(registry=DetectorRegistry(), transcriber=UnavailableTranscriber())
    s.start("live-mic")
    assert not [m for m in feed(s, 6.2) if m["type"] == "transcript"]


def test_failing_transcriber_reports_error_without_crashing():
    class Boom(FakeTranscriber):
        def transcribe(self, window):
            raise RuntimeError("asr crashed")

    s = CallSession(registry=DetectorRegistry(), transcriber=Boom([]))
    s.start("live-mic")
    msgs = feed(s, 5.2)
    assert any(m["type"] == "error" and m["code"] == "transcription_failed" for m in msgs)
    assert s.state == "running"


def test_make_transcriber_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TRINETRA_TRANSCRIBER", "none")
    t = make_transcriber()
    assert not t.available and "Disabled" in t.status()["detail"]


def test_make_transcriber_degrades_when_model_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("faster_whisper"):
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **k)

    monkeypatch.setenv("TRINETRA_TRANSCRIBER", "auto")
    monkeypatch.setattr(builtins, "__import__", fake_import)
    t = make_transcriber()
    assert not t.available and t.status()["state"] == "unavailable"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("faster_whisper") is None, reason="faster-whisper not installed"
)
def test_real_whisper_transcribes_demo_clip_if_installed():
    from pathlib import Path

    from app.audio_ingestion import AudioWindow
    from app.scenarios import AUDIO_DIR, read_wav
    from app.transcription import FasterWhisperTranscriber

    try:
        tr = FasterWhisperTranscriber("base.en")
    except Exception as exc:  # model not downloaded / offline
        pytest.skip(f"model unavailable: {exc}")
    x = read_wav(Path(AUDIO_DIR) / "synthetic_relative_scam.wav")[: 5 * SAMPLE_RATE]
    text = tr.transcribe(AudioWindow(x, SAMPLE_RATE, 0)).lower()
    assert "grandma" in text
