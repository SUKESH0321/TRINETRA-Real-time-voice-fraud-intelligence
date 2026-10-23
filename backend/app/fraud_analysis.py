"""Rule-based scam-conversation analysis.

This is a transparent phrase/context rule engine, NOT a trained model. Every finding carries
method="rule-based:<rule_id>" so the UI can say so. It works fully offline, English only.

Key behaviours
* Only the *caller's* speech is judged. Lines from "you" are never flagged.
* Safe context: "Never share your OTP" or "scammers may ask for your PIN" is advice, not a request.
* Mentions are not enough: asking for an OTP needs a request cue, a bare "OTP" mention does not fire.
* Findings carry evidence spans (offsets into the sentence excerpt) for highlighting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .models import Finding, SRC_SCRIPTED, SRC_MANUAL

I = re.IGNORECASE


def _rx(p: str) -> re.Pattern[str]:
    return re.compile(p, I)


# --------------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------------
CRED = _rx(
    r"\b(?:o\W?t\W?p|one[- ]?time[- ](?:password|passcode|code|pin)|verification\s+code|security\s+code"
    r"|authenti[cs]ation\s+code|auth\s+code|recovery\s+code|backup\s+code|secret\s+code"
    r"|(?:atm\s+|upi\s+|debit\s+|credit\s+|card\s+|m)?pin(?:\s+(?:number|code))?"
    r"|(?:net\s?banking\s+|login\s+|account\s+|email\s+|bank\s+)?password|passcode|cvv|cvc"
    r"|card\s+verification\s+(?:value|code)"
    r"|(?:[4-8]|four|five|six|seven|eight)[- ]digit\s+(?:code|number|pin)"
    r"|code\s+(?:that\s+)?you\s+(?:have\s+|just\s+)*(?:received|got|get)"
    r"|code\s+(?:sent|texted)\s+to\s+(?:your|the)\s+(?:phone|mobile|number))\b"
)
REQ = _rx(
    r"\b(?:tell|read(?:\s+out|\s+back)?|share|give|provide|confirm|say|dictate|spell|state|reveal|disclose"
    r"|forward|enter|type|verify|need|want|require|requires|asking\s+for|ask\s+for|what(?:'s|\s+is|\s+are)"
    r"|(?:send|text|message|whatsapp)(?:\s+(?:it|them|that|this))?(?:\s+over)?\s+to\s+me|send\s+me|text\s+me)\b"
)
TRAILING_REQ = _rx(r"\b(?:please|now|asap|quickly|immediately)\b")
PRETEXT = _rx(
    r"\b(?:so\s+(?:that\s+)?i\s+can|to\s+(?:verify|secure|confirm|protect|unblock|cancel|stop|avoid|complete|process|reverse)"
    r"|for\s+(?:verification|security|safety))\b"
)
INFO_ONLY = _rx(
    r"\b(?:you\s+(?:will|may|might|should)\s+(?:receive|get)|will\s+be\s+sent|has\s+been\s+sent|have\s+sent"
    r"|(?:i|we)(?:'ve|\s+have)\s+sent|is\s+valid\s+for|expires?\s+in|(?:is|was)\s+\d{4,8})\b"
)
DIRECT_TO_ME = _rx(r"\b(?:to\s+me|to\s+us|send\s+me|text\s+me|tell\s+me|give\s+me|read\s+(?:it\s+)?(?:out\s+)?to\s+me)\b")
PRONOUN_REQ = _rx(
    r"\b(?:read|tell|share|give|say|confirm|send|provide|dictate|forward)\s+(?:it|that|them|those|these"
    r"|the\s+(?:code|number|numbers|digits|message|sms|text))(?:\s+(?:out|back|over|here))?(?:\s+to\s+me|\s+now|\s+please|\s+quickly)?\b"
)

SAFE = _rx(
    r"\bnever\b"
    r"|\b(?:do(?:es)?\s+not|don'?t|doesn'?t|did\s+not|didn'?t|should\s+not|shouldn'?t|must\s+not|mustn'?t|will\s+not|won'?t"
    r"|would\s+not|wouldn'?t|cannot|can'?t|no\s+one\s+should)\s+(?:(?!worry)\w+\s+)?"
    r"(?:ask|share|give|tell|reveal|disclose|provide|read|send|say|enter|type|forward|click|install|download|transfer|pay)\b"
    r"|\bnot\s+to\s+(?:share|give|tell|reveal|disclose|provide|read|send|say|enter|type|install|download|transfer|pay)\b"
    r"|\bno\s+(?:need|reason)\s+to\s+(?:share|give|tell|provide|send)\b"
    r"|\b(?:beware|be\s+careful|be\s+aware|watch\s+out|scammers?|fraudsters?|phishing|stay\s+safe|safety\s+tip|awareness)\b"
    r"|\b(?:did|has|have|had|was|were)\s+(?:anyone|someone|somebody)\b.{0,40}\b(?:ask|call|tell)"
)

APP = _rx(
    r"\b(?:any\s?desk|team\s?viewer|quick\s?support|ultra\s?viewer|rust\s?desk|supremo|ammyy"
    r"|remote\s+(?:access|desktop|support|control|assistance)(?:\s+(?:app|application|software|tool))?"
    r"|screen[- ]shar(?:e|ing)\s+(?:app|application|software))\b"
)
APP_ACT = _rx(r"\b(?:install|download|open|launch|start|run|use|get|go\s+to|enable|allow|grant|give\s+me)\b")
GIVE_ACCESS = _rx(
    r"\b(?:give|grant|allow)\s+(?:me|us)\s+(?:remote\s+)?access\s+to\s+your\s+(?:phone|computer|laptop|pc|screen|device)\b"
)

MOVE = _rx(r"\b(?:transfer|move|send|deposit|wire|pay|remit|shift)\b")
SAFEACC = _rx(
    r"\b(?:safe|secure|secured|protected|holding|rbi|government|verification|temporary|escrow|monitored|special)"
    r"\s+(?:bank\s+)?account\b"
)

MONEY = _rx(
    r"\b(?:money|cash|funds|payment)\b|(?:rs\.?|inr|₹|\$|usd|€|£)\s?\d[\d,]*"
    r"|\b\d[\d,]*\s?(?:rupees?|dollars?|thousand|lakh|k)\b|\b(?:rupees?|dollars?)\b"
)
MONEY_ASK = _rx(
    r"\b(?:send|transfer|wire|pay|give|lend|need|deposit|put|upi|gpay|google\s?pay|phonepe|paytm)\b"
)
URGENT = _rx(
    r"\b(?:urgent(?:ly)?|right\s+now|immediately|asap|quickly|emergency|as\s+soon\s+as\s+possible|today|tonight"
    r"|within\s+(?:\w+\s+){0,2}(?:minutes?|hours?)|hurry)\b"
)
DISTRESS = _rx(
    r"\b(?:in\s+trouble|accident|hospital|arrested|stuck|stranded|bail|lost\s+my\s+(?:wallet|phone)|emergency)\b"
)
SELF_GIVING = _rx(
    r"\b(?:i|we)(?:'ll|'ve|\s+will|\s+have|\s+am|\s+shall)\s+(?:\w+\s+){0,2}(?:send|transfer|pay|give|credit|refund|deposit)\b"
)

UNTRACEABLE = _rx(
    r"\b(?:gift\s?cards?|itunes\s+cards?|google\s+play\s+cards?|steam\s+cards?|bitcoin(?:\s+atm)?|crypto(?:currency)?"
    r"|western\s+union|moneygram)\b"
)
UNTRACEABLE_ACT = _rx(r"\b(?:buy|pay|send|purchase|transfer|deposit|load|scratch|read|get)\b")

INST = (
    r"(?:bank|police|cyber\s?crime|cbi|customs|income\s+tax|tax\s+(?:department|office)|irs|government|microsoft|apple|amazon"
    r"|tech(?:nical)?\s+support|fraud\s+(?:department|team|prevention|desk)|security\s+(?:department|team)|rbi|reserve\s+bank"
    r"|court|narcotics|trai|telecom|sbi|hdfc|icici|axis|paypal|visa|mastercard|social\s+security|customer\s+(?:care|support|service))"
)
INTRO = (
    r"(?:this\s+is|i\s*am|i'm|my\s+name\s+is|calling\s+(?:you\s+)?from|speaking\s+(?:to\s+you\s+)?from"
    r"|(?:i\s+)?(?:work|working)\s+(?:at|for|with|in)|on\s+behalf\s+of|from\s+the)"
)
IMPERSONATE_INST = _rx(rf"\b{INTRO}\b[^.!?]{{0,60}}?\b{INST}\b")
IMPERSONATE_OFFICER = _rx(
    rf"\b{INTRO}\b[^.!?]{{0,40}}?\b(?:officer|inspector|sub[- ]inspector|constable|commissioner|detective|manager|executive)\b"
)
NOT_FROM = _rx(r"\bnot\s+(?:from|calling|with)\b")
RELATIVE = _rx(
    r"\b(?:it'?s\s+me\b|this\s+is\s+(?:your\s+)?(?:son|daughter|grandson|granddaughter|grandchild|nephew|niece|brother|sister|cousin|dad|mom|mum|uncle|aunt)"
    r"|i'?m\s+your\s+(?:son|daughter|grandson|granddaughter|grandchild|nephew|niece|brother|sister|cousin)"
    r"|hi\s+(?:grandma|grandpa|nana|mom|mum|dad|mama|papa)"
    r"|(?:got|have)\s+a\s+new\s+number|changed\s+my\s+number|lost\s+my\s+phone)"
)

THREAT = _rx(
    r"\byour\s+(?:bank\s+)?(?:account|card|sim|number|aadhaar|pan|licen[sc]e|connection|services?|computer|device|identity)\b"
    r"(?:\W+\w+){0,6}?\W+(?:will\s+be\s+|is\s+being\s+|has\s+been\s+|have\s+been\s+|is\s+|are\s+|been\s+|get\s+)?"
    r"(?:block(?:ed)?|suspend(?:ed)?|froz[ae]n|freez(?:e|ing)|deactivat\w*|terminat\w*|clos(?:ed|ure)|compromis\w+|hack(?:ed)?|seiz\w+|cancel(?:l?ed)?)\b"
    r"|\b(?:arrest(?:ed)?|warrant|jail|prison|legal\s+action|prosecut\w+|police\s+case|case\s+(?:will\s+be\s+)?(?:filed|registered)"
    r"|lose\s+(?:all\s+)?(?:your\s+)?(?:money|savings|funds)|lawsuit)\b"
)
PRESSURE = _rx(
    r"\b(?:within\s+(?:\w+\s+){0,2}(?:minutes?|mins?|hours?)|immediately|right\s+now|last\s+warning|final\s+(?:warning|notice)"
    r"|or\s+else|act\s+now|before\s+it'?s\s+too\s+late|time\s+is\s+running\s+out|urgent(?:ly)?)\b"
)

BYPASS = _rx(
    r"\b(?:don'?t|do\s+not|no\s+need\s+to|not\s+necessary\s+to|shouldn'?t|should\s+not|never)\s+(?:\w+\s+){0,2}"
    r"(?:visit|go\s+to|call|contact|check\s+with|verify\s+(?:with|it)|inform|use|open|log\s?in\s+to|trust)\s+(?:\w+\s+){0,3}"
    r"(?:bank|branch|official|app|helpline|customer\s+care|number|website)\b"
    r"|\b(?:skip|bypass|ignore|avoid)\s+(?:the\s+)?(?:\w+\s+){0,2}(?:verification|official|procedure|process|security\s+check)\b"
    r"|\b(?:this\s+is\s+)?(?:not|no)\s+(?:a\s+)?(?:normal|regular|usual)\s+(?:procedure|process)\b"
    r"|\b(?:just|only)\s+trust\s+me\b"
)

SECRECY = _rx(
    r"\b(?:don'?t|do\s+not|never|not\s+to|shouldn'?t|should\s+not|mustn'?t|must\s+not)\s+(?:\w+\s+){0,2}"
    r"(?:tell|inform|mention|discuss|talk\s+to|speak\s+to|involve|alert)\s+(?:\w+\s+){0,3}"
    r"(?:anyone|anybody|any\s?one|family|wife|husband|friends?|parents?|children|kids|your\s+(?:mom|mother|dad|father|son|daughter)"
    r"|bank\s+staff|the\s+bank|police|neighbou?rs?)\b"
    r"|\bkeep\s+(?:this|it|the\s+call|everything)\s+(?:a\s+)?(?:secret|confidential|private|between\s+us|to\s+yourself)\b"
    r"|\bbetween\s+(?:you\s+and\s+me|us)\b"
    r"|\b(?:don'?t|do\s+not|never)\s+(?:hang\s+up|disconnect|cut\s+(?:the|this)\s+call|end\s+the\s+call|put\s+the\s+phone\s+down)\b"
)

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_SPLIT = re.compile(r"(?:,\s*|\s+)(?:but|however|although|though|except)\s+|;\s*", I)

LABELS = {
    "credential_request": "Request for a one-time code, PIN or password",
    "remote_access": "Request to install or allow remote access",
    "safe_account_transfer": "Instruction to move money to a 'safe' account",
    "urgent_money_request": "Urgent request for money",
    "untraceable_payment": "Request for gift-card, crypto or wire payment",
    "impersonation": "Claims to be an official or a relative",
    "threat_pressure": "Threat or pressure tactic",
    "bypass_verification": "Pressure to avoid official verification",
    "secrecy": "Request to keep the call secret",
}


@dataclass
class _Hit:
    category: str
    rule_id: str
    spans: list[tuple[int, int]]  # relative to the clause
    confidence: float
    boost: float = 0.0


def _spans(*matches: re.Match[str] | None) -> list[tuple[int, int]]:
    return [m.span() for m in matches if m]


class ScamAnalyzer:
    """Stateful per session (it remembers a recent code mention to resolve 'read it to me')."""

    def __init__(self, rules: dict[str, Any]):
        scam = rules["scam"]
        self.weights: dict[str, float] = scam["category_weights"]
        self.min_conf: float = scam["min_finding_confidence"]
        self.actions: dict[str, str] = rules["actions"]
        self.levels: dict[str, float] = rules["levels"]
        self._n = 0
        self._recent_cred_t: float | None = None
        self._clause_rules: list[Callable[[str, str, float], list[_Hit]]] = [
            self._credential_request,
            self._remote_access,
            self._safe_account,
            self._urgent_money,
            self._untraceable,
            self._impersonation,
            self._threat,
            self._bypass,
            self._secrecy,
        ]

    # ---- public --------------------------------------------------------------------------
    def reset(self) -> None:
        self._n = 0
        self._recent_cred_t = None

    def analyze(self, text: str, t: float, speaker: str = "caller", source: str = SRC_SCRIPTED) -> list[Finding]:
        if speaker != "caller" or not text or not text.strip():
            return []
        text = text.replace("’", "'").replace("‘", "'")
        findings: list[Finding] = []
        for sent, sent_off in self._sentences(text):
            seen: dict[str, _Hit] = {}
            clause_hits: list[tuple[_Hit, int, int]] = []
            for clause, c_off in self._clauses(sent):
                for fn in self._clause_rules:
                    for hit in fn(clause, sent, t):
                        # keep the strongest hit per category within this sentence
                        if hit.category not in seen or hit.confidence > seen[hit.category].confidence:
                            seen[hit.category] = hit
                            clause_hits.append((hit, c_off, len(clause)))
            for hit, c_off, _ in clause_hits:
                if seen.get(hit.category) is not hit:
                    continue
                if hit.confidence < self.min_conf:
                    continue
                score = min(0.95, self.weights.get(hit.category, 0.3) + hit.boost)
                spans = [[a + c_off, b + c_off] for a, b in hit.spans]
                matched = [sent[a:b] for a, b in spans]
                self._n += 1
                findings.append(
                    Finding(
                        id=f"f{self._n}",
                        category=hit.category,
                        label=LABELS.get(hit.category, hit.category),
                        severity=self._severity(score),
                        score=round(score, 3),
                        confidence=round(hit.confidence, 2),
                        matched=matched,
                        excerpt=sent,
                        spans=spans,
                        t=round(t, 2),
                        action=self.actions.get(hit.category, self.actions["default"]),
                        method=f"rule-based:{hit.rule_id}",
                        source=source if source in (SRC_SCRIPTED, SRC_MANUAL) else SRC_SCRIPTED,
                        rule_id=hit.rule_id,
                    )
                )
        return findings

    # ---- helpers -------------------------------------------------------------------------
    def _severity(self, score: float) -> str:
        if score >= self.levels["critical"]:
            return "critical"
        if score >= self.levels["high"]:
            return "high"
        if score >= self.levels["elevated"]:
            return "elevated"
        return "low"

    @staticmethod
    def _sentences(text: str) -> list[tuple[str, int]]:
        out, pos = [], 0
        for part in SENT_SPLIT.split(text):
            if not part.strip():
                continue
            idx = text.find(part, pos)
            out.append((part.strip(), idx))
            pos = idx + len(part)
        return out

    @staticmethod
    def _clauses(sentence: str) -> list[tuple[str, int]]:
        out, pos = [], 0
        for m in CLAUSE_SPLIT.finditer(sentence):
            out.append((sentence[pos : m.start()], pos))
            pos = m.end()
        out.append((sentence[pos:], pos))
        return [(c, o) for c, o in out if c.strip()]

    @staticmethod
    def _safe(clause: str, sentence: str) -> bool:
        # awareness cues usually sit in the same clause; scam-awareness words may sit anywhere in the sentence
        return bool(SAFE.search(clause)) or bool(
            re.search(r"\b(?:beware|scammers?|fraudsters?|phishing|stay\s+safe|safety\s+tip|awareness)\b", sentence, I)
        )

    # ---- rules ---------------------------------------------------------------------------
    def _credential_request(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        cred = CRED.search(clause)
        if cred:
            if self._safe(clause, sentence):
                return []
            before = clause[: cred.start()]
            after = clause[cred.end():]
            req = None
            for m in REQ.finditer(before):
                if cred.start() - m.end() <= 80:
                    req = m
            trailing = TRAILING_REQ.search(after)
            if INFO_ONLY.search(clause) and not DIRECT_TO_ME.search(clause):
                self._recent_cred_t = t
                return []
            if not req and not trailing:
                self._recent_cred_t = t  # a plain mention: remember it for "read it to me" but don't flag
                return []
            self._recent_cred_t = t
            pretext = PRETEXT.search(clause)
            conf = 0.9 if pretext else (0.8 if req else 0.6)
            return [_Hit("credential_request", "credential_request", _spans(req, cred, pretext), conf, 0.05 if pretext else 0.0)]
        # "Read it to me" after a recent code mention
        if self._recent_cred_t is not None and 0 <= t - self._recent_cred_t <= 20:
            m = PRONOUN_REQ.search(clause)
            if m and not self._safe(clause, sentence):
                pretext = PRETEXT.search(clause)
                return [_Hit("credential_request", "credential_request_pronoun", _spans(m, pretext), 0.75, 0.05 if pretext else 0.0)]
        return []

    def _remote_access(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if self._safe(clause, sentence):
            return []
        app, act = APP.search(clause), APP_ACT.search(clause)
        if app and act:
            return [_Hit("remote_access", "remote_access", _spans(act, app), 0.85)]
        m = GIVE_ACCESS.search(clause)
        if m:
            return [_Hit("remote_access", "remote_access_grant", _spans(m), 0.85)]
        return []

    def _safe_account(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if self._safe(clause, sentence):
            return []
        acc = SAFEACC.search(clause)
        if not acc:
            return []
        for mv in MOVE.finditer(clause[: acc.start()]):
            if acc.start() - mv.end() <= 100:
                return [_Hit("safe_account_transfer", "safe_account_transfer", _spans(mv, acc), 0.9)]
        return []

    def _urgent_money(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if self._safe(clause, sentence) or SELF_GIVING.search(clause):
            return []
        money, ask = MONEY.search(clause), MONEY_ASK.search(clause)
        if not (money and ask):
            return []
        urgent = URGENT.search(clause) or URGENT.search(sentence) or DISTRESS.search(sentence)
        if not urgent:
            return []
        return [_Hit("urgent_money_request", "urgent_money_request", _spans(ask, money, urgent), 0.75)]

    def _untraceable(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if self._safe(clause, sentence):
            return []
        item, act = UNTRACEABLE.search(clause), UNTRACEABLE_ACT.search(clause)
        if item and act:
            return [_Hit("untraceable_payment", "untraceable_payment", _spans(act, item), 0.8)]
        return []

    def _impersonation(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if NOT_FROM.search(clause):
            return []
        m = IMPERSONATE_INST.search(clause)
        if m:
            return [_Hit("impersonation", "impersonation_institution", _spans(m), 0.65)]
        m = IMPERSONATE_OFFICER.search(clause)
        if m:
            return [_Hit("impersonation", "impersonation_officer", _spans(m), 0.6)]
        m = RELATIVE.search(clause)
        if m:
            return [_Hit("impersonation", "impersonation_relative", _spans(m), 0.6)]
        return []

    def _threat(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if self._safe(clause, sentence):
            return []
        m = THREAT.search(clause)
        if not m:
            return []
        press = PRESSURE.search(sentence)
        return [_Hit("threat_pressure", "threat_pressure", _spans(m, press), 0.8 if press else 0.7, 0.05 if press else 0.0)]

    def _bypass(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if CRED.search(sentence):  # "never ... PIN" advice context
            return []
        m = BYPASS.search(clause)
        return [_Hit("bypass_verification", "bypass_verification", _spans(m), 0.65)] if m else []

    def _secrecy(self, clause: str, sentence: str, t: float) -> list[_Hit]:
        if CRED.search(sentence):
            return []
        m = SECRECY.search(clause)
        return [_Hit("secrecy", "secrecy", _spans(m), 0.7)] if m else []
