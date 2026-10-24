"""Transcription: model lifecycle, streaming commit logic, session/WS integration. The model is mocked
everywhere except the one optional smoke test at the bottom (which says whether it used the real model)."""
import sys
import threading
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.audio_ingestion import SAMPLE_RATE
from app.main import create_app
from app.session import CallSession, clean_transcript
from app.transcription import (SETUP_CMD, StreamingTranscriber, TranscriptionLoadError, TranscriptionService,
                               TranscriptionUnavailable, UnavailableTranscriber, Word, load_faster_whisper)
from app.voice_analysis import DetectorRegistry

rng = np.random.default_rng(11)


class FakeASR:
    """Deterministic stand-in for a speech model. `truth` is [(word, start_s, end_s)] in call time.
    Like a real model it does not emit a word that is cut off by the end of the window."""

    name, available = "fake-asr", True

    def __init__(self, truth, fail_times=0):
        self.truth, self.fail_times, self.calls = truth, fail_times, 0

    def status(self):
        return {"state": "ready", "name": self.name, "code": "ready", "detail": "fake", "action": "", "cause": ""}

    def transcribe_words(self, window):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("decoder crashed")
        return [Word(t, s - window.t_start, e - window.t_start) for t, s, e in self.truth
                if s >= window.t_start - 1e-6 and e <= window.t_end - 0.3]


def speech(sentence, start, step=0.45):
    out, t = [], start
    for w in sentence.split():
        out.append((w, round(t, 2), round(t + step * 0.8, 2)))
        t += step
    return out


TRUTH = speech("Please tell me your OTP now.", 1.0) + speech("Then read the code you received.", 6.0)


def noise(seconds, amp=0.1):
    return (rng.normal(0, amp, int(seconds * SAMPLE_RATE))).astype(np.float32)


def pcm(seconds, amp=0.1):
    return (np.clip(noise(seconds, amp), -1, 1) * 32767).astype("<i2").tobytes()


def stream(asr, seconds, chunk=0.1, **kw):
    st = StreamingTranscriber(asr, **kw)
    events = []
    for _ in range(int(round(seconds / chunk))):
        st.append(noise(chunk))
        if st.due():
            events.append((st.total, st.step()))
    return st, events


# ================================================================ streaming logic
def test_sentences_are_emitted_once_in_order_with_low_latency():
    st, events = stream(FakeASR(TRUTH), 13.0)
    finals = [(emit_t, f) for emit_t, r in events for f in r.finals]
    assert [f.text for _, f in finals] == ["Please tell me your OTP now.", "Then read the code you received."]
    for emit_t, f in finals:
        assert 0 <= emit_t - f.t <= 4.0  # text arrives within ~4 s of being spoken, not at the end of the call


def test_interim_text_appears_before_the_sentence_is_final():
    _, events = stream(FakeASR(TRUTH), 13.0)
    first_final = next(i for i, (_, r) in enumerate(events) if r.finals)
    partials_before = [r.partial for _, r in events[:first_final] if r.partial]
    assert partials_before and "Please" in partials_before[-1]


def test_overlapping_decodes_never_repeat_text():
    _, events = stream(FakeASR(TRUTH), 13.0, hop_s=1.0)  # heavier overlap
    words = " ".join(f.text for _, r in events for f in r.finals).split()
    assert words == [w for w, _, _ in TRUTH]


def test_silence_is_not_decoded_and_makes_no_text():
    asr = FakeASR(TRUTH)
    st = StreamingTranscriber(asr)
    for _ in range(80):
        st.append(np.zeros(1600, np.float32))
        if st.due():
            r = st.step()
            assert r.silent and not r.finals and not r.partial
    assert asr.calls == 0


def test_pause_ends_an_utterance_without_punctuation():
    truth = speech("call me back on this number", 1.0)
    _, events = stream(FakeASR(truth), 10.0)
    finals = [f.text for _, r in events for f in r.finals]
    assert finals == ["call me back on this number"]


def test_flush_finalises_the_trailing_fragment():
    truth = speech("Hello there. And one more thing", 1.0)
    asr = FakeASR(truth)
    st, events = stream(asr, 4.2)
    done = [f.text for _, r in events for f in r.finals]
    assert "And one more thing" not in " ".join(done)
    res = st.flush()
    assert [f.text for f in res.finals][-1].endswith("one more thing")
    assert st.buffer_s == 0


def test_decoder_disagreement_delays_commit_until_two_decodes_agree():
    class Flip(FakeASR):
        def transcribe_words(self, window):
            words = super().transcribe_words(window)
            if self.calls == 2 and words:  # second decode mishears the first word
                words[0] = Word("Pleas", words[0].start, words[0].end)
            return words

    st, events = stream(Flip(speech("Please tell me your OTP now.", 1.0)), 9.0)
    assert [f.text for _, r in events for f in r.finals] == ["Please tell me your OTP now."]


def test_very_long_unbroken_speech_is_committed_in_bounded_chunks():
    truth = speech(" ".join(["word"] * 60), 0.5, step=0.4)  # 24 s with no punctuation and no pause
    st, events = stream(FakeASR(truth), 26.0, max_buffer_s=8.0, max_pending_words=15)
    assert st.buffer_s <= 8.5  # the buffer never grows without bound
    assert sum(len(f.text.split()) for _, r in events for f in r.finals) >= 30


# ================================================================ session integration
def make_session(asr, **kw):
    s = CallSession(registry=DetectorRegistry(), transcriber=asr)
    s.start("live-mic")
    return s


def feed(s, seconds, chunk=0.1):
    out = []
    for _ in range(int(round(seconds / chunk))):
        out += s.ingest_audio_chunk(pcm(chunk))
    return out


def test_live_audio_produces_labelled_transcript_findings_and_an_alert():
    s = make_session(FakeASR(TRUTH))
    msgs = feed(s, 13.0)
    lines = [m["line"] for m in msgs if m["type"] == "transcript"]
    assert [l["text"] for l in lines] == ["Please tell me your OTP now.", "Then read the code you received."]
    assert all(l["source"] == "real" and l["method"] == "fake-asr" and l["session"] == s.session_id for l in lines)
    assert all(l["t"] > 0 for l in lines)
    assert any(m["type"] == "finding" and m["finding"]["category"] == "credential_request" for m in msgs)
    assert any(m["type"] == "alert" and m["alert"]["level"] in ("high", "critical") for m in msgs)
    assert any(m["type"] == "transcript_partial" and m["text"] for m in msgs)  # interim text was shown while speaking


def test_advice_about_otp_does_not_raise_an_alert():
    s = make_session(FakeASR(speech("Never share your OTP with anyone.", 1.0)))
    msgs = feed(s, 8.0)
    assert [m["line"]["text"] for m in msgs if m["type"] == "transcript"] == ["Never share your OTP with anyone."]
    assert not [m for m in msgs if m["type"] in ("finding", "alert")]


def test_status_messages_report_transcribing_then_ready_with_diagnostics():
    s = make_session(FakeASR(TRUTH))
    msgs = feed(s, 6.0)
    states = [m["state"] for m in msgs if m["type"] == "transcription_status"]
    assert "transcribing" in states and "ready" in states
    diag = [m for m in msgs if m["type"] == "transcription_status"][-1]["diag"]
    assert diag["frames"] >= 50 and diag["audio_s"] == pytest.approx(6.0, abs=1.1)  # the last status is from the last decode and diag["decodes"] >= 1


def test_end_of_call_flushes_the_last_buffered_speech():
    s = make_session(FakeASR(speech("Please tell me your OTP", 1.0)))  # no end punctuation, call ends mid-sentence
    feed(s, 5.2)
    assert not [m for m in s.history if m["type"] == "alert"]  # nothing final yet: no alert from a fragment
    ended = s.end()
    assert any(m["type"] == "transcript" for m in ended)
    assert any(m["type"] == "alert" for m in ended)
    assert ended[-1]["type"] == "timeline" and s.state == "ended"


def test_decoder_failure_is_reported_then_recovers_without_inventing_text():
    asr = FakeASR(TRUTH, fail_times=2)
    s = make_session(asr)
    msgs = feed(s, 13.0)
    errs = [m for m in msgs if m["type"] == "error" and m["code"] == "transcription_failed"]
    assert errs and "decoder crashed" in errs[0]["message"]
    assert any(m["type"] == "transcription_status" and m["state"] == "error" for m in msgs)
    assert s.state == "running"
    texts = [m["line"]["text"] for m in msgs if m["type"] == "transcript"]
    assert texts  # later decodes recovered
    assert all(set(t.lower().replace(".", "").split()) <= {w.lower().strip(".") for w, _, _ in TRUTH} for t in texts)


def test_unavailable_model_is_reported_and_never_fabricates_text():
    s = make_session(UnavailableTranscriber("No model.", "not_installed"))
    msgs = feed(s, 6.0)
    assert not [m for m in msgs if m["type"] in ("transcript", "transcript_partial")]
    st = [m for m in msgs if m["type"] == "transcription_status"]
    assert st and st[0]["state"] == "unavailable" and SETUP_CMD in st[0]["action"]


def test_sessions_are_isolated():
    a = make_session(FakeASR(speech("Please tell me your OTP now.", 1.0)))
    b = make_session(FakeASR(speech("Nice weather today.", 1.0)))
    out_a, out_b = [], []
    for _ in range(80):  # interleave the two calls chunk by chunk
        out_a += a.ingest_audio_chunk(pcm(0.1))
        out_b += b.ingest_audio_chunk(pcm(0.1))
    ta = [m["line"]["text"] for m in out_a if m["type"] == "transcript"]
    tb = [m["line"]["text"] for m in out_b if m["type"] == "transcript"]
    assert ta == ["Please tell me your OTP now."] and tb == ["Nice weather today."]
    assert a.session_id != b.session_id
    assert any(m["type"] == "finding" for m in out_a) and not any(m["type"] == "finding" for m in out_b)


def test_batched_chunks_give_the_same_transcript_as_one_by_one():
    chunks = [pcm(0.1) for _ in range(130)]
    one = make_session(FakeASR(TRUTH))
    for c in chunks:
        one.ingest_audio_chunk(c)
    batched = make_session(FakeASR(TRUTH))
    for i in range(0, len(chunks), 13):
        batched.ingest_audio_chunks(chunks[i:i + 13])
    t = lambda s: [m["line"]["text"] for m in s.history if m["type"] == "transcript"]
    assert t(one) == t(batched) == ["Please tell me your OTP now.", "Then read the code you received."]


def test_transcript_cleaning_removes_model_artifacts():
    assert clean_transcript("I need a // // // // //") == "I need a"
    assert clean_transcript("♪♪") == ""
    assert clean_transcript("Hello   there") == "Hello there"


def test_scripted_scenarios_are_unaffected_by_the_transcription_state():
    from app.scenarios import load_scenarios, run_sync

    sc = load_scenarios()["human-otp-scam"]
    base = run_sync(CallSession(registry=DetectorRegistry(), transcriber=UnavailableTranscriber()), sc)
    with_asr = run_sync(CallSession(registry=DetectorRegistry(), transcriber=FakeASR(TRUTH)), sc)
    strip = lambda ms: [{k: v for k, v in m.items() if k != "capabilities"} for m in ms]
    assert strip(base) == strip(with_asr)
    assert {m["line"]["source"] for m in base if m["type"] == "transcript"} == {"scripted"}


# ================================================================ model lifecycle
def wait_state(svc, state, timeout=3.0):
    import time
    end = time.time() + timeout
    while svc.state != state and time.time() < end:
        time.sleep(0.01)
    return svc.state


def test_service_reports_loading_then_ready():
    gate = threading.Event()
    asr = FakeASR([])

    def loader(report):
        report("Downloading…")
        gate.wait(2)
        return asr

    svc = TranscriptionService(loader)
    assert svc.state == "idle"
    svc.start()
    assert svc.state == "loading" and not svc.available
    with pytest.raises(TranscriptionUnavailable):
        svc.transcribe_words(None)
    gate.set()
    assert wait_state(svc, "ready") == "ready" and svc.available and svc.name == "fake-asr"


def test_missing_package_is_unavailable_with_the_exact_setup_command():
    def loader(report):
        raise TranscriptionLoadError("not_installed", "faster-whisper is not installed.", f"Run `{SETUP_CMD}`.", "ModuleNotFoundError")

    svc = TranscriptionService(loader)
    svc.start(blocking=True)
    st = svc.status()
    assert st["state"] == "unavailable" and st["code"] == "not_installed" and SETUP_CMD in st["action"] and st["cause"]


def test_load_failure_is_an_error_with_cause_and_can_be_retried_to_success():
    calls = {"n": 0}

    def loader(report):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TranscriptionLoadError("download_failed", "no network", "Retry", "URLError")
        return FakeASR([])

    svc = TranscriptionService(loader)
    svc.start(blocking=True)
    assert svc.state == "error" and svc.code == "download_failed" and svc.cause == "URLError"
    svc.retry()
    assert wait_state(svc, "ready") == "ready" and calls["n"] == 2


def test_unexpected_loader_exception_becomes_an_error_not_a_dead_thread():
    svc = TranscriptionService(lambda report: (_ for _ in ()).throw(OSError("disk on fire")))
    svc.start(blocking=True)
    st = svc.status()
    assert st["state"] == "error" and "disk on fire" in st["detail"] and st["code"] == "load_failed"


def test_disabled_by_environment(monkeypatch):
    monkeypatch.setenv("TRINETRA_TRANSCRIBER", "none")
    st = TranscriptionService.from_env().status()
    assert st["state"] == "unavailable" and st["code"] == "disabled"


class FakeWhisperModule:
    """Injects a fake `faster_whisper` so load_faster_whisper can be tested without the real package."""

    def __init__(self, cached=True, download_ok=True):
        self.cached, self.download_ok, self.calls = cached, download_ok, []
        outer = self

        class WhisperModel:
            def __init__(self, size, **kw):
                outer.calls.append(kw.get("local_files_only"))
                if kw.get("local_files_only") and not outer.cached:
                    raise OSError("not in cache")
                if not kw.get("local_files_only") and not outer.download_ok:
                    raise ConnectionError("Temporary failure in name resolution")

        self.module = types.SimpleNamespace(WhisperModel=WhisperModel)


def test_loader_uses_the_local_cache_and_does_not_download(monkeypatch):
    fake = FakeWhisperModule(cached=True)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake.module)
    t = load_faster_whisper()
    assert t.available and fake.calls == [True]  # one local-only load, no download


def test_loader_downloads_once_when_not_cached_and_reports_progress(monkeypatch):
    fake = FakeWhisperModule(cached=False, download_ok=True)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake.module)
    seen = []
    assert load_faster_whisper(seen.append).available
    assert fake.calls == [True, False] and any("Downloading" in s for s in seen)


def test_loader_reports_download_failure_with_cause(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", FakeWhisperModule(cached=False, download_ok=False).module)
    with pytest.raises(TranscriptionLoadError) as e:
        load_faster_whisper()
    assert e.value.code == "download_failed" and "name resolution" in e.value.cause and SETUP_CMD in e.value.action


def test_loader_offline_mode_never_downloads(monkeypatch):
    fake = FakeWhisperModule(cached=False)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake.module)
    monkeypatch.setenv("TRINETRA_OFFLINE", "1")
    with pytest.raises(TranscriptionLoadError) as e:
        load_faster_whisper()
    assert e.value.code == "model_missing_offline" and fake.calls == [True]


def test_loader_reports_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import raises ImportError
    with pytest.raises(TranscriptionLoadError) as e:
        load_faster_whisper()
    assert e.value.code == "not_installed" and SETUP_CMD in e.value.action


# ================================================================ HTTP + WebSocket
@pytest.fixture
def client_with(request):
    def make(asr):
        return TestClient(create_app(registry=DetectorRegistry(), transcriber=asr))
    return make


def test_health_and_retry_endpoints_reflect_the_service():
    calls = {"n": 0}

    def loader(report):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TranscriptionLoadError("not_installed", "not installed", f"Run `{SETUP_CMD}`.", "ModuleNotFoundError")
        return FakeASR([])

    svc = TranscriptionService(loader)
    with TestClient(create_app(registry=DetectorRegistry(), transcriber=svc)) as c:
        assert wait_state(svc, "unavailable") == "unavailable"
        h = c.get("/api/health").json()["transcription"]
        assert h["state"] == "unavailable" and h["code"] == "not_installed" and SETUP_CMD in h["action"]
        c.post("/api/transcription/retry")
        assert wait_state(svc, "ready") == "ready"
        assert c.get("/api/health").json()["transcription"]["state"] == "ready"


def test_ws_live_stream_transcribes_and_flushes_on_stop(client_with):
    truth = speech("Please tell me your OTP now.", 1.0) + speech("and then call me", 6.0)  # last part has no end mark
    with client_with(FakeASR(truth)) as c, c.websocket_connect("/ws/session") as ws:
        hello = ws.receive_json()
        assert hello["capabilities"]["transcription"]["state"] == "ready"
        ws.send_json({"type": "start_live", "mode": "live-mic"})
        for _ in range(100):  # 10 s of audio in 100 ms frames, exactly as the AudioWorklet sends them
            ws.send_bytes(pcm(0.1))
        ws.send_json({"type": "stop"})
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "call_state" and m["state"] == "ended":
                break
    lines = [m["line"]["text"] for m in msgs if m["type"] == "transcript"]
    assert lines[0] == "Please tell me your OTP now."
    assert lines[-1].endswith("call me")  # flushed on stop: the last, unfinished speech was not lost
    assert any(m["type"] == "alert" for m in msgs)


def test_ws_without_a_model_reports_it_and_keeps_scripted_demos_working(client_with):
    with client_with(UnavailableTranscriber("No model.", "not_installed")) as c, c.websocket_connect("/ws/session") as ws:
        assert ws.receive_json()["capabilities"]["transcription"]["state"] == "unavailable"
        ws.send_json({"type": "start_live", "mode": "live-mic"})
        for _ in range(8):
            ws.send_bytes(pcm(0.1))
        ws.send_json({"type": "stop"})
        seen = []
        while True:
            m = ws.receive_json()
            seen.append(m["type"])
            if m["type"] == "call_state" and m["state"] == "ended":
                break
        assert "transcription_status" in seen and "transcript" not in seen
        ws.send_json({"type": "start_scenario", "scenario_id": "human-otp-scam", "speed": 20})
        out = []
        while True:
            m = ws.receive_json()
            out.append(m)
            if m["type"] == "call_state" and m["state"] == "ended":
                break
        assert any(m["type"] == "alert" for m in out)


# ================================================================ optional real-model smoke test
@pytest.mark.skipif(__import__("importlib").util.find_spec("faster_whisper") is None, reason="faster-whisper not installed")
def test_real_model_streams_a_spoken_clip_if_available():
    """Runs the REAL model on the bundled spoken clip through the streaming path. Skipped when the model is absent."""
    from pathlib import Path

    from app.scenarios import AUDIO_DIR, read_wav

    svc = TranscriptionService()
    svc.start(blocking=True)
    if svc.state != "ready":
        pytest.skip(f"real model not available: {svc.code}")
    x = read_wav(Path(AUDIO_DIR) / "synthetic_relative_scam.wav")
    st = StreamingTranscriber(svc)
    finals = []
    for i in range(0, len(x), 1600):
        st.append(x[i:i + 1600])
        if st.due():
            finals += [f.text for f in st.step().finals]
    finals += [f.text for f in st.flush().finals]
    text = " ".join(finals).lower()
    print(f"\n[REAL MODEL {svc.name}] streamed transcript: {text}")
    assert "grandma" in text and "number" in text


def test_findings_and_timeline_from_real_transcription_are_labelled_real_not_scripted():
    s = make_session(FakeASR(TRUTH))
    msgs = feed(s, 13.0)
    findings = [m["finding"] for m in msgs if m["type"] == "finding"]
    assert findings and {f["source"] for f in findings} == {"real"}
    tl = [m["entry"] for m in msgs if m["type"] == "timeline" and m["entry"]["kind"] == "finding"]
    assert tl and {e["source"] for e in tl} == {"real"}
    ev = [e for m in msgs if m["type"] == "risk" for e in m["assessment"]["evidence"] if e["kind"] == "conversation"]
    assert ev and {e["source"] for e in ev} == {"real"}


def test_manual_and_scripted_labels_are_preserved():
    s = CallSession(registry=DetectorRegistry(), transcriber=UnavailableTranscriber())
    s.start("live-mic")
    manual = s.ingest_transcript(1.0, "caller", "Please tell me your OTP now.", "manual", "manual-input")
    assert {m["finding"]["source"] for m in manual if m["type"] == "finding"} == {"manual"}
