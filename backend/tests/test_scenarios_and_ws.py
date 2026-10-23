import json

import pytest
from fastapi.testclient import TestClient

from app.config import load_rules
from app.main import create_app
from app.scenarios import load_scenarios, run_sync
from app.session import CallSession
from app.transcription import UnavailableTranscriber
from app.voice_analysis import DetectorRegistry

SCENARIOS = load_scenarios()


def fresh_session():
    return CallSession(load_rules(), DetectorRegistry(), UnavailableTranscriber())


def test_at_least_six_scenarios_present():
    assert len(SCENARIOS) >= 6
    assert {"legit-human", "human-otp-scam", "synthetic-legit", "synthetic-relative-scam", "safe-context-otp",
            "manipulated-audio"} <= set(SCENARIOS)


@pytest.mark.parametrize("sid", list(SCENARIOS))
def test_scenario_meets_expected_outcome(sid):
    s = fresh_session()
    run_sync(s, SCENARIOS[sid])
    snap, exp = s.snapshot(), SCENARIOS[sid].data["expected"]
    assert snap["max_level"] == exp["max_level"]
    assert len(snap["pings"]) == exp["pings"]


@pytest.mark.parametrize("sid", list(SCENARIOS))
def test_scenario_replay_is_deterministic(sid):
    a, b = fresh_session(), fresh_session()
    assert run_sync(a, SCENARIOS[sid]) == run_sync(b, SCENARIOS[sid])
    # and a second run on the SAME session object (replay button) gives the same result
    first = run_sync(a, SCENARIOS[sid])
    assert first == run_sync(a, SCENARIOS[sid])


def test_safe_scenarios_never_raise_the_otp_scam_warning():
    for sid in ("legit-human", "safe-context-otp", "synthetic-legit", "manipulated-audio"):
        s = fresh_session()
        run_sync(s, SCENARIOS[sid])
        snap = s.snapshot()
        assert not [a for a in snap["alerts"] if a["level"] in ("high", "critical")], sid
        assert not snap["pings"], sid


def test_human_otp_scenario_warns_at_18s_with_separate_voice_status():
    s = fresh_session()
    run_sync(s, SCENARIOS["human-otp-scam"])
    snap = s.snapshot()
    al = snap["pings"][0]
    assert al["t"] == 18 and al["level"] == "high"
    assert "one-time code" in al["reason"]
    assert al["voice_authenticity"].startswith("Inconclusive")
    assert "fraud confirmed" not in json.dumps(al).lower()


def test_synthetic_legit_has_no_scam_findings_but_shows_voice_indicators():
    s = fresh_session()
    msgs = run_sync(s, SCENARIOS["synthetic-legit"])
    assert not [m for m in msgs if m["type"] == "finding"]
    final = [m for m in msgs if m["type"] == "risk"][-1]["assessment"]
    assert final["dimensions"]["synthetic"]["status"] == "indicators"
    assert final["dimensions"]["synthetic"]["source"] == "simulated"
    assert final["dimensions"]["scam"]["value"] == 0


def test_synthetic_scam_keeps_voice_and_scam_separate():
    s = fresh_session()
    msgs = run_sync(s, SCENARIOS["synthetic-relative-scam"])
    final = [m for m in msgs if m["type"] == "risk"][-1]["assessment"]
    assert final["dimensions"]["synthetic"]["status"] == "indicators"
    assert final["dimensions"]["manipulation"]["status"] == "unavailable"
    assert final["dimensions"]["scam"]["value"] >= 0.8
    voice_first = next(m for m in msgs if m["type"] == "alert")
    assert voice_first["alert"]["level"] == "elevated"  # voice indicators alone were only Elevated


def test_simulated_evidence_is_always_labelled():
    for sid, sc in SCENARIOS.items():
        s = fresh_session()
        msgs = run_sync(s, sc)
        for m in msgs:
            if m["type"] == "voice" and m["result"]["method"] == "scenario-injected":
                assert m["result"]["source"] == "simulated", sid
            if m["type"] == "transcript":
                assert m["line"]["source"] == "scripted", sid


def test_timeline_is_chronological_for_events():
    s = fresh_session()
    msgs = run_sync(s, SCENARIOS["escalating-bank-scam"])
    ts = [m["entry"]["t"] for m in msgs if m["type"] == "timeline"]
    assert ts == sorted(ts)
    assert all(m["entry"]["clock"] for m in msgs if m["type"] == "timeline")


# ---- websocket delivery ----------------------------------------------------------------------
@pytest.fixture
def client():
    app = create_app(registry=DetectorRegistry(), transcriber=UnavailableTranscriber())
    with TestClient(app) as c:
        yield c


def collect(ws, until_state="ended", limit=500):
    out = []
    for _ in range(limit):
        m = ws.receive_json()
        out.append(m)
        if m.get("type") == "call_state" and m.get("state") == until_state:
            out.append(ws.receive_json())  # the trailing "Call ended" timeline entry
            break
    return out


def test_rest_endpoints(client):
    assert client.get("/api/health").json()["ok"] is True
    lst = client.get("/api/scenarios").json()
    assert len(lst) >= 6 and all("expected" in s for s in lst)
    assert client.get("/api/rules").json()["levels"]["high"] == 0.55
    assert client.get("/api/scenarios/legit-human/audio").status_code == 404


def test_ws_hello_reports_capabilities(client):
    with client.websocket_connect("/ws/session") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["capabilities"]["detectors"]["synthetic"]["available"] is False
        assert len(hello["scenarios"]) >= 6


def test_ws_delivers_scenario_events_and_alert(client):
    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start_scenario", "scenario_id": "human-otp-scam", "speed": 20})
        msgs = collect(ws)
    types = [m["type"] for m in msgs]
    assert types[0] == "call_state" and msgs[0]["state"] == "running"
    assert {"transcript", "finding", "risk", "alert", "timeline"} <= set(types)
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs)
    alert = next(m["alert"] for m in msgs if m["type"] == "alert")
    assert alert["level"] == "high" and alert["sound"] == "high"


def test_ws_ws_stream_equals_sync_run(client):
    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start_scenario", "scenario_id": "safe-context-otp", "speed": 20})
        msgs = collect(ws)
    expected = run_sync(fresh_session(), SCENARIOS["safe-context-otp"])
    for m in msgs + expected:
        if m["type"] == "call_state" and "info" in m:
            m["info"].pop("speed", None)
    assert msgs == expected


def test_ws_manual_transcript_and_live_mode(client):
    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "manual_transcript", "text": "hello"})
        assert ws.receive_json()["code"] == "no_call"
        ws.send_json({"type": "start_live", "mode": "live-mic"})
        started = ws.receive_json()
        assert started["mode"] == "live-mic"
        ws.receive_json()  # timeline
        ws.receive_json()  # risk
        ws.send_json({"type": "manual_transcript", "speaker": "caller", "text": "Please tell me your OTP now."})
        got = [ws.receive_json() for _ in range(5)]
        kinds = [m["type"] for m in got]
        assert kinds[0] == "transcript" and got[0]["line"]["source"] == "manual"
        assert "finding" in kinds and "alert" in kinds


def test_ws_invalid_audio_frame_does_not_kill_session(client):
    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "start_live", "mode": "live-mic"})
        for _ in range(3):
            ws.receive_json()
        ws.send_bytes(b"\x01")  # odd length -> invalid PCM16
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "bad_length"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_bad_input_is_reported_not_fatal(client):
    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "bad_json"
        ws.send_json({"type": "start_scenario", "scenario_id": "nope"})
        assert ws.receive_json()["code"] == "unknown_scenario"
        ws.send_json({"type": "wat"})
        assert ws.receive_json()["code"] == "unknown_message"
        ws.send_json({"type": "start_live", "mode": "satellite"})
        assert ws.receive_json()["code"] == "bad_mode"


def test_ws_quiet_mode_setting_applies(client):
    with client.websocket_connect("/ws/session") as ws:
        ws.receive_json()
        ws.send_json({"type": "settings", "quiet_mode": True})
        assert ws.receive_json()["quiet_mode"] is True
        ws.send_json({"type": "start_scenario", "scenario_id": "human-otp-scam", "speed": 20})
        msgs = collect(ws)
    alert = next(m["alert"] for m in msgs if m["type"] == "alert")
    assert alert["level"] == "high" and alert["sound"] is None  # visual only in quiet mode


def test_signaling_relays_between_two_peers_and_limits_room(client):
    with client.websocket_connect("/ws/signal/room1") as a:
        assert a.receive_json()["type"] == "joined"
        with client.websocket_connect("/ws/signal/room1") as b:
            assert b.receive_json()["type"] == "joined"
            assert a.receive_json()["type"] == "peer_joined"
            a.send_json({"type": "offer", "sdp": "x"})
            assert b.receive_json() == {"type": "offer", "sdp": "x"}
            with client.websocket_connect("/ws/signal/room1") as c:
                assert c.receive_json()["type"] == "room_full"


# ---- a real detector for one dimension must not hide simulated evidence for the other --------------
class _AlwaysClear:
    dimension, name, available, needs_smoothing = "synthetic", "stub-clear", True, False

    def detect(self, window):
        from app.models import VoiceResult

        return VoiceResult("synthetic", "clear", 0.05, 0.9, ["stub"], self.name, ["stub"], "real", window.t_start)


def test_unavailable_window_results_never_erase_injected_evidence():
    s = CallSession(load_rules(), DetectorRegistry(synthetic=_AlwaysClear()), UnavailableTranscriber())
    msgs = run_sync(s, SCENARIOS["manipulated-audio"])  # has a clip, so windows run through the real detectors
    final = [m for m in msgs if m["type"] == "risk"][-1]["assessment"]
    assert final["dimensions"]["manipulation"]["status"] == "indicators"
    assert final["dimensions"]["manipulation"]["source"] == "simulated"
    assert final["dimensions"]["synthetic"]["status"] == "clear"
    assert final["dimensions"]["synthetic"]["source"] == "real"
    unavailable_msgs = [m for m in msgs if m["type"] == "voice" and m["result"]["status"] == "unavailable"]
    assert len(unavailable_msgs) <= 1  # reported once, not once per audio window
