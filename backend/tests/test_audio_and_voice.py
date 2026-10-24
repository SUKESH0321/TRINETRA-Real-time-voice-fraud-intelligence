import numpy as np
import pytest

from app.audio_ingestion import (AudioError, AudioWindow, RollingBuffer, SAMPLE_RATE, assess_quality, decode_pcm16,
                                 metrics)
from app.session import CallSession
from app.voice_analysis import (DetectorRegistry, SpliceHeuristic, UnavailableDetector, detect_synthetic_speech,
                                detect_voice_manipulation)

rng = np.random.default_rng(7)


def pcm(x):
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def speechy(seconds, floor_amp, burst_amp=0.3):
    """Speech-like test signal: 0.4 s bursts every 1 s over a steady noise floor (so 0.6 s pauses)."""
    n = int(seconds * SAMPLE_RATE)
    x = rng.normal(0, floor_amp, n)
    for start in range(0, n, SAMPLE_RATE):
        x[start : start + 6400] += rng.normal(0, burst_amp, min(6400, n - start))
    return x.astype(np.float32)


def noise_with_bursts(seconds, floor_amp, burst_amp=0.3):
    n = int(seconds * SAMPLE_RATE)
    x = rng.normal(0, floor_amp, n)
    for start in range(0, n, SAMPLE_RATE // 2):  # 'speech' bursts every half second
        x[start : start + 2400] += rng.normal(0, burst_amp, min(2400, n - start))
    return x.astype(np.float32)


# ---- ingestion -------------------------------------------------------------------------------
@pytest.mark.parametrize("data,code", [(b"", "empty_audio"), (None, "empty_audio"), (b"\x01\x02\x03", "bad_length")])
def test_invalid_audio_is_rejected(data, code):
    with pytest.raises(AudioError) as e:
        decode_pcm16(data)
    assert e.value.code == code


def test_wrong_sample_rate_and_oversize_rejected():
    with pytest.raises(AudioError) as e:
        decode_pcm16(b"\x00\x00" * 10, 44100)
    assert e.value.code == "bad_sample_rate"
    with pytest.raises(AudioError) as e:
        decode_pcm16(b"\x00\x00" * (SAMPLE_RATE * 6))
    assert e.value.code == "chunk_too_long"


def test_decode_round_trip_and_metrics():
    x = np.sin(np.linspace(0, 100, 1600)).astype(np.float32) * 0.5
    y = decode_pcm16(pcm(x))
    assert np.allclose(x, y, atol=1e-3)
    m = metrics(y)
    assert 0.3 < m["rms"] < 0.4 and m["clipping"] == 0


def test_quality_gate_flags_silence_and_short_windows():
    assert "silent" in assess_quality(AudioWindow(np.zeros(SAMPLE_RATE * 2, np.float32), SAMPLE_RATE, 0))
    assert "shorter" in assess_quality(AudioWindow(noise_with_bursts(0.5, 0.01), SAMPLE_RATE, 0))
    assert assess_quality(AudioWindow(noise_with_bursts(2, 0.01), SAMPLE_RATE, 0)) is None


def test_rolling_buffer_emits_overlapping_windows():
    buf = RollingBuffer(window_s=3, hop_s=1)
    out = []
    for _ in range(10):  # 10 x 0.5 s = 5 s
        out += buf.push(np.zeros(SAMPLE_RATE // 2, np.float32))
    assert [round(w.t_start, 1) for w in out] == [0.0, 1.0, 2.0]
    assert all(abs(w.duration - 3.0) < 1e-6 for w in out)


# ---- detectors -------------------------------------------------------------------------------
def test_default_detectors_are_honestly_unavailable():
    w = AudioWindow(noise_with_bursts(3, 0.01), SAMPLE_RATE, 0)
    reg = DetectorRegistry()
    for res in (detect_synthetic_speech(w, reg), detect_voice_manipulation(w, reg)):
        assert res.status == "unavailable"
        assert res.score is None  # no invented number
        assert res.source == "unavailable"
        assert res.limitations


class Boom:
    dimension, name, available = "synthetic", "boom", True

    def detect(self, window):
        raise RuntimeError("model crashed")


def test_detector_failure_degrades_to_unavailable():
    w = AudioWindow(noise_with_bursts(3, 0.01), SAMPLE_RATE, 0)
    res = detect_synthetic_speech(w, DetectorRegistry(synthetic=Boom()))
    assert res.status == "unavailable" and "model crashed" in res.evidence[0]


def test_splice_heuristic_flags_noise_floor_step_and_is_experimental():
    x = np.concatenate([speechy(1.6, 0.002), speechy(1.6, 0.05)])
    res = SpliceHeuristic().detect(AudioWindow(x, SAMPLE_RATE, 0))
    assert res.status == "indicators" and res.experimental and res.source == "real"
    assert res.confidence <= 0.4
    assert any("Cannot identify a speaker" in l for l in res.limitations)


def test_splice_heuristic_quiet_on_stationary_noise():
    res = SpliceHeuristic().detect(AudioWindow(speechy(3.2, 0.01), SAMPLE_RATE, 0))
    assert res.status == "clear"


def test_splice_heuristic_inconclusive_on_silence_or_short():
    assert SpliceHeuristic().detect(AudioWindow(np.zeros(SAMPLE_RATE * 3, np.float32), SAMPLE_RATE, 0)).status == "inconclusive"
    assert SpliceHeuristic().detect(AudioWindow(speechy(1.1, 0.01), SAMPLE_RATE, 0)).status == "inconclusive"
    # continuous, varying "speech" with no steady pauses to compare -> inconclusive, never a guess
    busy = (rng.uniform(0.05, 0.5, 160) .repeat(300) * rng.normal(0, 1, 160 * 300)).astype(np.float32)
    assert SpliceHeuristic().detect(AudioWindow(busy, SAMPLE_RATE, 0)).status == "inconclusive"


def spliced_window():
    return np.concatenate([speechy(1.6, 0.002), speechy(1.6, 0.05)])


def feed_windows(session, x, n):
    last = None
    for i in range(n):
        msgs = session.ingest_audio_window(AudioWindow(x, SAMPLE_RATE, float(i)))
        last = [m for m in msgs if m["type"] == "risk"][-1]["assessment"] if any(m["type"] == "risk" for m in msgs) else last
    return last


def test_synthetic_and_manipulation_detectors_do_not_share_results():
    s = CallSession(registry=DetectorRegistry(manipulation=SpliceHeuristic()))
    s.start("live-mic")
    risk = feed_windows(s, spliced_window(), 2)
    assert risk["dimensions"]["manipulation"]["status"] == "indicators"
    assert risk["dimensions"]["manipulation"]["label"].endswith("(experimental)")
    assert risk["dimensions"]["synthetic"]["status"] == "unavailable"  # untouched by the other detector
    assert risk["dimensions"]["scam"]["value"] == 0


def test_experimental_manipulation_is_shown_but_does_not_change_the_risk_level_by_default():
    s = CallSession(registry=DetectorRegistry(manipulation=SpliceHeuristic()))
    s.start("live-mic")
    risk = feed_windows(s, spliced_window(), 3)
    assert risk["dimensions"]["manipulation"]["status"] == "indicators"
    assert risk["level"] == "low" and not risk["alert_eligible"]
    assert any("information only" in r for r in risk["reasons"])
    assert not [m for m in s.history if m["type"] == "alert"]


def test_experimental_manipulation_can_raise_the_level_only_when_configured():
    from app.config import with_overrides, load_rules

    rules = with_overrides(load_rules(), voice={"experimental_can_raise_level": True})
    s = CallSession(rules=rules, registry=DetectorRegistry(manipulation=SpliceHeuristic()))
    s.start("live-mic")
    risk = feed_windows(s, spliced_window(), 3)
    assert risk["level"] == "elevated"  # still capped at the voice-only maximum, never a fraud verdict


def test_one_noisy_window_is_not_reported_as_manipulation():
    s = CallSession(registry=DetectorRegistry(manipulation=SpliceHeuristic()))
    s.start("live-mic")
    risk = feed_windows(s, spliced_window(), 1)
    assert risk["dimensions"]["manipulation"]["status"] in ("inconclusive", "clear")


def test_untouched_audio_is_never_flagged_by_the_default_registry_settings(monkeypatch):
    monkeypatch.delenv("TRINETRA_EXPERIMENTAL_SPLICE", raising=False)
    reg = DetectorRegistry.from_env()
    assert reg.manipulation.available and reg.status()["manipulation"]["experimental"] is True
    s = CallSession(registry=reg)
    s.start("live-mic")
    risk = feed_windows(s, speechy(3.2, 0.01), 4)
    assert risk["dimensions"]["manipulation"]["status"] in ("clear", "inconclusive")


def test_manipulation_check_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("TRINETRA_EXPERIMENTAL_SPLICE", "0")
    st = DetectorRegistry.from_env().status()["manipulation"]
    assert st["available"] is False


# ---- live session audio path -----------------------------------------------------------------
def test_session_rejects_invalid_audio_without_crashing():
    s = CallSession()
    s.start("live-mic")
    assert s.ingest_audio_chunk(b"")[0]["code"] == "empty_audio"
    assert s.ingest_audio_chunk(b"\x00")[0]["code"] == "bad_length"
    assert s.state == "running"


def test_session_streams_metrics_and_reports_transcription_unavailable():
    s = CallSession()
    s.start("live-mic")
    msgs = []
    for _ in range(8):
        msgs += s.ingest_audio_chunk(pcm(noise_with_bursts(0.5, 0.01)))
    assert any(m["type"] == "audio_metrics" for m in msgs)
    assert not any(m["type"] == "transcript" for m in msgs)  # never fabricates live captions
    start = s.history[0]
    assert start["capabilities"]["transcription"]["state"] == "unavailable"
    assert start["capabilities"]["detectors"]["synthetic"]["available"] is False


def test_audio_ignored_when_no_call_running():
    assert CallSession().ingest_audio_chunk(pcm(noise_with_bursts(0.5, 0.01))) == []


# ---- the bundled demo clips (real analysis of real audio files) ---------------------------------
def test_experimental_splice_check_on_bundled_clips():
    from pathlib import Path

    from app.scenarios import AUDIO_DIR, read_wav

    def flagged(name):
        x = read_wav(Path(AUDIO_DIR) / name)
        buf, det, hits = RollingBuffer(), SpliceHeuristic(), []
        for i in range(0, len(x), SAMPLE_RATE // 2):
            for w in buf.push(x[i : i + SAMPLE_RATE // 2]):
                if det.detect(w).status == "indicators":
                    hits.append(round(w.t_end, 1))
        return hits

    assert flagged("synthetic_appointment.wav") == []  # untouched TTS: no indicators
    assert flagged("synthetic_relative_scam.wav") == []
    spliced = flagged("manipulated_splice.wav")  # constructed join around 00:11
    assert spliced and all(10 <= t <= 14 for t in spliced)


# ---- smoothing of noisy per-window models --------------------------------------------------------
class Flaky:
    """Stand-in for a noisy per-window model: replays a scripted list of window scores."""

    dimension, name, available, needs_smoothing = "synthetic", "flaky-model", True, True

    def __init__(self, scores):
        self.scores = list(scores)

    def detect(self, window):
        from app.models import VoiceResult

        sc = self.scores.pop(0)
        return VoiceResult("synthetic", "indicators" if sc >= 0.5 else "clear", sc, abs(2 * sc - 1), ["x"], self.name, ["l"], "real", window.t_start)


def run_windows(scores):
    s = CallSession(registry=DetectorRegistry(synthetic=Flaky(scores)))
    s.start("live-mic")
    final = None
    for i in range(len(scores)):
        msgs = s.ingest_audio_window(AudioWindow(noise_with_bursts(3, 0.05), SAMPLE_RATE, float(i)))
        final = [m for m in msgs if m["type"] == "voice" and m["result"]["dimension"] == "synthetic"][-1]["result"]
    return final


def test_single_noisy_window_does_not_raise_indicators():
    assert run_windows([0.1, 0.1, 0.1, 0.1, 0.99])["status"] == "clear"


def test_indicators_need_agreement_across_windows():
    r = run_windows([0.9, 0.95, 0.2, 0.99, 0.97])
    assert r["status"] == "indicators" and "4 of the last 5" in r["evidence"][0]


def test_smoother_reports_inconclusive_until_enough_windows():
    assert run_windows([0.99, 0.99])["status"] == "inconclusive"


def test_old_detector_hits_expire_by_time_not_by_window_count():
    s = CallSession(registry=DetectorRegistry(synthetic=Flaky([0.99, 0.99, 0.99, 0.05, 0.05, 0.05])))
    s.start("live-mic")
    for i in range(3):  # three agreeing windows: indicators
        last = s.ingest_audio_window(AudioWindow(noise_with_bursts(3, 0.05), SAMPLE_RATE, float(i)))
    assert [m for m in last if m["type"] == "voice"][-1]["result"]["status"] == "indicators"
    final = None
    for i in range(3):  # much later (30 s on) the evidence is stale and must not carry over
        final = s.ingest_audio_window(AudioWindow(noise_with_bursts(3, 0.05), SAMPLE_RATE, 30.0 + i))
    status = [m for m in final if m["type"] == "voice"][-1]["result"]["status"]
    assert status == "clear"
