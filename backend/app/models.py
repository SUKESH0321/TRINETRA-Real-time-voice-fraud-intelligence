"""Plain data structures shared by every module. All serialise to JSON-safe dicts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Where a result came from. Shown on every item in the UI.
SRC_REAL = "real"              # computed by code running on actual audio/text
SRC_SCRIPTED = "scripted"      # transcript line injected by a demo scenario
SRC_SIMULATED = "simulated"    # voice evidence injected by a demo scenario (NOT a detector output)
SRC_MANUAL = "manual"          # typed in by the user
SRC_UNAVAILABLE = "unavailable"


@dataclass
class TranscriptLine:
    id: str
    t: float
    speaker: str               # "caller" | "you"
    text: str
    source: str                # scripted | manual | real
    method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    """One conversational scam finding with evidence spans."""
    id: str
    category: str
    label: str
    severity: str              # low | elevated | high | critical
    score: float
    confidence: float
    matched: list[str]
    excerpt: str
    spans: list[list[int]]     # [start, end] offsets into excerpt
    t: float
    action: str
    method: str                # e.g. "rule-based:credential_request"
    source: str
    rule_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceResult:
    """Output of detect_synthetic_speech / detect_voice_manipulation."""
    dimension: str             # "synthetic" | "manipulation"
    status: str                # unavailable | inconclusive | clear | indicators
    score: float | None
    confidence: float
    evidence: list[str]
    method: str
    limitations: list[str]
    source: str                # real | simulated | unavailable
    t: float = 0.0
    experimental: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DimensionState:
    value: float | None
    status: str
    source: str
    label: str
    method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Assessment:
    t: float
    level: str
    score: float
    confidence: float
    reasons: list[str]
    evidence: list[dict[str, Any]]
    uncertainty: list[str]
    recommended_action: str
    alert_eligible: bool
    primary_category: str | None
    dimensions: dict[str, DimensionState]
    voice_authenticity: str
    bumped: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d
