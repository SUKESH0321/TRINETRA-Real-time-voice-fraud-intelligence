import pytest

from app.config import load_rules
from app.fraud_analysis import ScamAnalyzer


@pytest.fixture
def an():
    return ScamAnalyzer(load_rules())


def cats(an, text, t=10.0, speaker="caller"):
    return {f.category for f in an.analyze(text, t, speaker)}


# ---- must fire -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Please tell me your OTP so I can verify your account.",
        "Read the code you just received so I can secure your account.",
        "I need your ATM PIN to cancel the transaction.",
        "Can you share your CVV number with me?",
        "Tell me the six-digit code that was sent to your phone.",
        "Don't worry, just tell me your password.",
        "What is your UPI PIN?",
        "Your OTP, please.",
    ],
)
def test_credential_requests_fire(an, text):
    assert "credential_request" in cats(an, text)


def test_otp_request_has_evidence_and_action(an):
    f = an.analyze("Please tell me your OTP so I can verify your account.", 18.0)[0]
    assert f.category == "credential_request"
    assert f.severity == "high"
    assert any("OTP" in m for m in f.matched)
    assert "Do not share" in f.action
    assert f.method.startswith("rule-based")
    assert f.t == 18.0
    for a, b in f.spans:
        assert f.excerpt[a:b] in f.matched


def test_pronoun_followup_resolves_recent_code(an):
    assert cats(an, "We have sent a verification code to your phone.", t=5) == set()
    assert "credential_request" in cats(an, "Read it out to me now.", t=9)


def test_pronoun_followup_expires(an):
    cats(an, "We have sent a verification code to your phone.", t=5)
    assert "credential_request" not in cats(an, "Read it out to me now.", t=60)


@pytest.mark.parametrize(
    "text,cat",
    [
        ("Please install AnyDesk so I can fix your phone.", "remote_access"),
        ("Download the remote access app now.", "remote_access"),
        ("Transfer your savings to the safe account I am giving you.", "safe_account_transfer"),
        ("Move the money to a secure account immediately.", "safe_account_transfer"),
        ("Mom, I need money urgently, please send 20000 rupees right now.", "urgent_money_request"),
        ("Buy gift cards and read me the numbers.", "untraceable_payment"),
        ("This is Rahul calling from the bank fraud department.", "impersonation"),
        ("Hi grandma, it's me, I changed my number.", "impersonation"),
        ("Your account will be blocked within 10 minutes.", "threat_pressure"),
        ("A warrant for your arrest has been issued.", "threat_pressure"),
        ("Don't call your bank, they will not understand.", "bypass_verification"),
        ("Please don't tell anyone about this call.", "secrecy"),
        ("Keep this between us.", "secrecy"),
        ("Do not hang up the phone.", "secrecy"),
    ],
)
def test_other_categories_fire(an, text, cat):
    assert cat in cats(an, text)


# ---- must NOT fire ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Never share your OTP with anyone.",
        "Our bank will never ask for your PIN or password.",
        "Please do not share your CVV with anyone, even bank staff.",
        "Scammers may ask you for your OTP, so be careful.",
        "Your OTP is 482913 and it is valid for five minutes.",
        "I have sent you an OTP, it expires in 10 minutes.",
        "Did anyone ask you for your OTP today?",
        "There is no need to share your password with us.",
        "Never tell anyone your PIN.",
        "We do not ask customers to install remote access software.",
        "Never transfer money to a safe account, it is a scam.",
        "Don't ever send money to someone you haven't met.",
    ],
)
def test_safe_context_does_not_fire_credential_or_transfer(an, text):
    found = cats(an, text)
    assert not found & {
        "credential_request",
        "remote_access",
        "safe_account_transfer",
        "urgent_money_request",
        "secrecy",
        "bypass_verification",
    }, found


def test_plain_mentions_are_not_flagged(an):
    for text in [
        "I called the bank this morning about my savings.",
        "I forgot my password again, so annoying.",
        "OTP deliveries are quite slow on this network.",
        "I'll send you the money for lunch tomorrow.",
    ]:
        assert cats(an, text) == set(), text


def test_legitimate_conversation_has_no_findings(an):
    lines = [
        "Hi, it's good to hear from you. How was the trip?",
        "We should move the meeting to three o'clock if that works.",
        "I'll send you the document by email this afternoon.",
        "Thanks, see you at dinner on Saturday.",
    ]
    assert all(cats(an, l) == set() for l in lines)


def test_user_speech_is_never_flagged(an):
    assert cats(an, "Should I tell you my OTP?", speaker="you") == set()
    assert cats(an, "My OTP is 123456, do you need it?", speaker="you") == set()


def test_mixed_clause_only_flags_the_request(an):
    found = cats(an, "Never share your OTP with anyone, but right now tell me your OTP.")
    assert "credential_request" in found


def test_negation_in_separate_sentence_does_not_cancel_request(an):
    a = ScamAnalyzer(load_rules())
    assert cats(a, "Never share your OTP with anyone.", t=1) == set()
    assert "credential_request" in cats(a, "Now tell me your OTP.", t=3)


def test_finding_ids_are_deterministic():
    r = load_rules()
    a, b = ScamAnalyzer(r), ScamAnalyzer(r)
    text = "This is the bank. Tell me your OTP right now or your account will be blocked."
    assert [f.to_dict() for f in a.analyze(text, 1)] == [f.to_dict() for f in b.analyze(text, 1)]
