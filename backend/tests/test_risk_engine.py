import pytest

from app.config import level_rank
from app.models import SRC_UNAVAILABLE, VoiceResult

from .helpers import engine, overrides, say, voice


def test_empty_call_is_low():
    eng, _ = engine()
    a = eng.assess(5)
    assert a.level == "low"
    assert not a.alert_eligible
    assert a.score == 0


def test_otp_request_alone_is_high_without_any_voice_detector():
    eng, an = engine()
    say(eng, an, 18, "Please read the code you just received so I can secure your account.")
    a = eng.assess(18)
    assert a.level == "high"
    assert a.alert_eligible
    assert a.primary_category == "credential_request"
    assert "Do not share the code" in a.recommended_action
    assert a.dimensions["synthetic"].status == "unavailable"
    assert any("conversation content only" in u or "unavailable" in u.lower() for u in a.uncertainty)
    assert a.voice_authenticity.startswith("Inconclusive")


def test_impersonation_alone_stays_low():
    eng, an = engine()
    say(eng, an, 3, "Good morning, this is Rahul calling from the bank.")
    assert eng.assess(3).level == "low"


def test_corroboration_adds_distinct_categories_not_repeats():
    eng, an = engine()
    for t in (10, 12, 14, 16):
        say(eng, an, t, "Tell me your OTP right now.")
    repeated = eng.assess(16).score
    eng2, an2 = engine()
    say(eng2, an2, 10, "Tell me your OTP right now.")
    assert repeated == pytest.approx(eng2.assess(10).score, abs=0.05)  # repeats do not stack
    say(eng2, an2, 11, "Install AnyDesk and do not call the bank.")
    assert eng2.assess(11).score > repeated  # distinct tactics do


def test_levels_follow_thresholds():
    eng, an = engine()
    say(eng, an, 1, "This is the bank. Tell me your OTP. Install AnyDesk.")
    a = eng.assess(1)
    assert a.level == "critical"
    assert a.score >= 0.8


def test_synthetic_voice_alone_never_exceeds_elevated():
    eng, _ = engine()
    eng.add_voice(voice("synthetic", 5, score=0.99, conf=0.99))
    a = eng.assess(5)
    assert a.level == "elevated"
    assert a.primary_category is None
    assert a.dimensions["scam"].value == 0
    assert "not proof of fraud" in " ".join(a.reasons).lower()
    assert "fraud confirmed" not in a.recommended_action.lower()


def test_both_voice_dimensions_alone_still_capped():
    eng, _ = engine()
    eng.add_voice(voice("synthetic", 5, score=0.99, conf=0.99))
    eng.add_voice(voice("manipulation", 5, score=0.99, conf=0.99))
    assert eng.assess(5).level == "elevated"


def test_voice_cap_is_configurable():
    eng, _ = engine(overrides(voice={"voice_only_max_level": "low"}))
    eng.add_voice(voice("synthetic", 5))
    assert eng.assess(5).level == "low"


def test_voice_dimensions_are_independent():
    eng, _ = engine()
    eng.add_voice(voice("synthetic", 5))
    a = eng.assess(5)
    assert a.dimensions["synthetic"].status == "indicators"
    assert a.dimensions["manipulation"].status == "unavailable"
    assert a.dimensions["manipulation"].value is None
    assert a.dimensions["scam"].value == 0


def test_scam_risk_independent_of_voice_result():
    eng, an = engine()
    say(eng, an, 10, "Tell me your OTP now.")
    base = eng.assess(10)
    eng.add_voice(voice("synthetic", 10, status="clear", score=0.05))
    clear = eng.assess(10)
    assert base.level == clear.level
    assert base.score == clear.score
    assert clear.dimensions["synthetic"].status == "clear"


def test_synthetic_plus_scam_reports_both_without_bumping_by_default():
    eng, an = engine()
    say(eng, an, 10, "Tell me your OTP now.")
    eng.add_voice(voice("synthetic", 10))
    a = eng.assess(10)
    assert a.level == "high"
    assert not a.bumped
    kinds = {e["kind"] for e in a.evidence}
    assert kinds == {"conversation", "voice"}


def test_corroboration_bump_when_enabled():
    eng, an = engine(overrides(voice={"corroboration_bumps_level": True}))
    say(eng, an, 10, "Tell me your OTP now.")
    eng.add_voice(voice("synthetic", 10))
    a = eng.assess(10)
    assert a.level == "critical" and a.bumped
    assert any("corroborate" in r for r in a.reasons)


def test_bump_requires_scam_evidence():
    eng, _ = engine(overrides(voice={"corroboration_bumps_level": True}))
    eng.add_voice(voice("synthetic", 10))
    assert eng.assess(10).level == "elevated"


def test_low_confidence_voice_result_is_ignored():
    eng, _ = engine()
    eng.add_voice(voice("synthetic", 5, score=0.95, conf=0.2))
    a = eng.assess(5)
    assert a.level == "low"
    assert a.dimensions["synthetic"].status in ("clear", "inconclusive")


def test_unavailable_detector_is_reported_unavailable_not_clear():
    eng, _ = engine()
    eng.add_voice(VoiceResult("synthetic", "unavailable", None, 0.0, ["none"], "none", [], SRC_UNAVAILABLE, 0))
    d = eng.assess(1).dimensions["synthetic"]
    assert d.status == "unavailable" and d.value is None


def test_evidence_decays_with_call_time():
    eng, an = engine()
    say(eng, an, 0, "Tell me your OTP now.")
    fresh = eng.assess(0).score
    later = eng.assess(600).score
    assert later < fresh / 4
    assert eng.assess(600).level == "low"


def test_low_confidence_findings_are_dropped_by_threshold():
    rules = overrides(scam={"min_finding_confidence": 0.99})
    eng, an = engine(rules)
    assert say(eng, an, 1, "Tell me your OTP now.") == []
    assert eng.assess(1).level == "low"


def test_thresholds_are_adjustable():
    rules = overrides(levels={"elevated": 0.30, "high": 0.90, "critical": 0.95})
    eng, an = engine(rules)
    say(eng, an, 1, "Tell me your OTP now.")
    assert eng.assess(1).level == "elevated"


def test_safe_context_produces_no_risk():
    eng, an = engine()
    say(eng, an, 7, "Never share your OTP with anyone.")
    a = eng.assess(7)
    assert a.level == "low"
    assert level_rank(a.level) == 0
