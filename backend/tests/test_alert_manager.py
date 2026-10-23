from .helpers import engine, manager, overrides, say, voice


def run(eng, an, am, t, text):
    say(eng, an, t, text)
    return am.decide(eng.assess(t))


def test_no_alert_for_low_risk():
    eng, an = engine()
    am = manager()
    d = run(eng, an, am, 5, "See you at dinner on Saturday.")
    assert d.alert is None and d.suppressed == "below-threshold"


def test_high_alert_pings_with_vibration_and_explanation():
    eng, an = engine()
    am = manager()
    d = run(eng, an, am, 18, "Please read the code you just received so I can secure your account.")
    al = d.alert
    assert al["level"] == "high" and al["sound"] == "high"
    assert al["vibrate"] == [200]
    assert "one-time code" in al["reason"]
    assert al["evidence"]["detected_at"] == 18
    assert "Read the code" in al["evidence"]["transcript"] or "read the code" in al["evidence"]["transcript"]
    assert "Do not share" in al["recommended_action"]
    assert al["voice_authenticity"].startswith("Inconclusive")


def test_elevated_is_silent_visual_only():
    eng, an = engine()
    am = manager()
    eng.add_voice(voice("synthetic", 4))
    d = am.decide(eng.assess(4))
    assert d.alert["level"] == "elevated"
    assert d.alert["sound"] is None and d.alert["vibrate"] is None
    assert d.alert["kind"] == "indicator"


def test_same_phrase_is_deduplicated():
    eng, an = engine()
    am = manager()
    first = run(eng, an, am, 10, "Tell me your OTP.")
    assert first.alert
    second = run(eng, an, am, 12, "Tell me your OTP.")
    assert second.alert is None and second.suppressed == "duplicate"


def test_new_phrase_same_incident_inside_cooldown_is_suppressed():
    eng, an = engine()
    am = manager()
    run(eng, an, am, 10, "Tell me your OTP.")
    d = run(eng, an, am, 20, "Please share the verification code with me.")
    assert d.alert is None and d.suppressed == "cooldown"
    assert sum(1 for x in am.log if x["ping"]) == 1


def test_new_phrase_after_cooldown_updates_silently():
    eng, an = engine()
    am = manager()
    run(eng, an, am, 10, "Tell me your OTP.")
    d = run(eng, an, am, 70, "Please share the verification code with me.")
    assert d.alert is not None
    assert d.alert["trigger"] == "update"
    assert d.alert["sound"] is None  # a repeat of an existing incident never pings


def test_escalation_bypasses_cooldown_and_pings():
    eng, an = engine()
    am = manager()
    first = run(eng, an, am, 10, "Tell me your OTP.")
    assert first.alert["level"] == "high"
    d = run(eng, an, am, 12, "Now install AnyDesk so I can reverse the transaction.")
    assert d.alert["trigger"] == "escalation"
    assert d.alert["level"] == "critical"
    assert d.alert["sound"] == "critical"
    assert d.alert["vibrate"] == [300, 120, 300, 120, 500]
    assert "remote access" in d.alert["reason"]


def test_elevated_then_high_escalates_with_ping():
    eng, an = engine()
    am = manager()
    eng.add_voice(voice("synthetic", 2))
    assert am.decide(eng.assess(2)).alert["sound"] is None
    d = run(eng, an, am, 9, "Tell me your OTP.")
    assert d.alert["sound"] == "high" and d.alert["trigger"] == "escalation"


def test_quiet_mode_mutes_high_but_not_critical():
    eng, an = engine()
    am = manager(quiet=True)
    d = run(eng, an, am, 10, "Tell me your OTP.")
    assert d.alert["level"] == "high" and d.alert["sound"] is None and d.alert["vibrate"] is None
    d = run(eng, an, am, 12, "Now install AnyDesk so I can reverse the transaction.")
    assert d.alert["level"] == "critical" and d.alert["sound"] == "critical"


def test_quiet_mode_can_be_toggled_mid_call():
    eng, an = engine()
    am = manager()
    am.set_quiet(True)
    assert run(eng, an, am, 10, "Tell me your OTP.").alert["sound"] is None


def test_new_critical_tactic_pings_only_after_global_gap():
    eng, an = engine()
    am = manager()
    run(eng, an, am, 10, "This is the bank. Tell me your OTP.")
    run(eng, an, am, 12, "Install AnyDesk now.")  # escalation: ping
    d = run(eng, an, am, 14, "Move your savings to the safe account.")
    # a different tactic 2 s later: shown, but within the global gap it must not ping again
    if d.alert is not None:
        assert d.alert["sound"] is None
    assert sum(1 for x in am.log if x["ping"]) <= 2


def test_cooldown_is_configurable():
    rules = overrides(alerts={"cooldown_s": 5})
    eng, an = engine(rules)
    am = manager(rules)
    run(eng, an, am, 10, "Tell me your OTP.")
    d = run(eng, an, am, 20, "Please share the verification code with me.")
    assert d.alert is not None  # cooldown elapsed


def test_never_more_than_one_ping_for_repeated_otp_requests():
    eng, an = engine()
    am = manager()
    for i, t in enumerate(range(10, 60, 3)):
        run(eng, an, am, t, f"Tell me your OTP now number {i}, please share the code.")
    assert sum(1 for x in am.log if x["ping"]) == 1
