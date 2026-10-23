from app.alert_manager import AlertManager
from app.config import load_rules, with_overrides
from app.fraud_analysis import ScamAnalyzer
from app.models import VoiceResult, SRC_SIMULATED
from app.risk_engine import RiskEngine

RULES = load_rules()


def engine(rules=None):
    r = rules or RULES
    return RiskEngine(r), ScamAnalyzer(r)


def say(eng, an, t, text, speaker="caller"):
    fs = an.analyze(text, t, speaker)
    eng.add_findings(fs)
    return fs


def voice(dim, t, status="indicators", score=0.85, conf=0.8, source=SRC_SIMULATED, experimental=False):
    return VoiceResult(dim, status, score, conf, ["test evidence"], "test", ["test limitation"], source, t, experimental)


def overrides(**sections):
    return with_overrides(RULES, **sections)


def manager(rules=None, quiet=None):
    return AlertManager(rules or RULES, quiet)
