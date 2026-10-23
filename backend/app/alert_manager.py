"""Decides when an assessment becomes a user-facing alert, and whether it pings.

Anti-spam rules (all thresholds in config/risk_rules.yaml -> alerts):

* No alert below "elevated". Elevated is a silent visual indicator.
* An alert is only raised for NEW evidence. Re-hearing the same phrase is a duplicate.
* A genuinely worse situation (higher level, or score up by `escalation_delta`) is an escalation:
  it bypasses cooldowns and always pings (if sound is allowed for that level).
* Otherwise new evidence on an existing incident is rate-limited by `cooldown_s`, and updates
  never ping twice within `global_min_gap_s`.
* Quiet mode: only critical alerts carry sound/vibration. Visual warnings are unaffected.

Time is call time (seconds) supplied by the caller, so behaviour is deterministic and testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import level_rank
from .models import Assessment
from .risk_engine import CATEGORY_NAMES, _fmt

HEADLINES = {
    "elevated": "Suspicious signals",
    "high": "POSSIBLE PHONE SCAM",
    "critical": "LIKELY SCAM – ACT NOW",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def evidence_key(e: dict[str, Any]) -> str:
    if e["kind"] == "conversation":
        return f"conv:{e['category']}:{_norm(e['excerpt'])}"
    return f"voice:{e['dimension']}"


def evidence_keys(a: Assessment) -> set[str]:
    return {evidence_key(e) for e in a.evidence}


@dataclass
class Decision:
    alert: dict[str, Any] | None
    suppressed: str | None = None  # "duplicate" | "cooldown" | "below-threshold"


@dataclass
class _Incident:
    last_t: float
    rank: int


class AlertManager:
    def __init__(self, rules: dict[str, Any], quiet_mode: bool | None = None):
        self.cfg = rules["alerts"]
        self.elevated_at = rules["levels"]["elevated"]
        self.quiet_mode = self.cfg.get("quiet_mode", False) if quiet_mode is None else quiet_mode
        self.reset()

    def reset(self) -> None:
        self._n = 0
        self.seen_keys: set[str] = set()
        self.incidents: dict[str, _Incident] = {}
        self.max_rank = 0
        self.max_score = 0.0
        self.last_ping_t: float | None = None
        self.log: list[dict[str, Any]] = []  # every decision, for diagnostics/tests

    def set_quiet(self, quiet: bool) -> None:
        self.quiet_mode = quiet

    # ------------------------------------------------------------------------------------
    def decide(self, a: Assessment) -> Decision:
        rank = level_rank(a.level)
        if not a.alert_eligible or rank < 1:
            return self._log(Decision(None, "below-threshold"), a)
        keys = evidence_keys(a)
        new_keys = keys - self.seen_keys
        incident_key = a.primary_category or "voice"
        inc = self.incidents.get(incident_key)
        escalation = rank > self.max_rank or (
            self.max_rank > 0 and a.score >= self.max_score + self.cfg["escalation_delta"]
        )

        if not escalation:
            if not new_keys:
                return self._log(Decision(None, "duplicate"), a)
            if inc is not None and a.t - inc.last_t < self.cfg["cooldown_s"]:
                self.seen_keys |= new_keys
                return self._log(Decision(None, "cooldown"), a)

        first = self.max_rank == 0
        reason = "new" if first else ("escalation" if escalation else "update")
        ping_allowed = rank >= 2 and (not self.quiet_mode or a.level == "critical")
        if ping_allowed and not escalation and self.last_ping_t is not None:
            if a.t - self.last_ping_t < self.cfg["global_min_gap_s"]:
                ping_allowed = False
        # Non-escalating updates are visual refreshes. Only a brand-new tactic at critical level may
        # still ping (and only after the global gap), never a repeat of an existing incident.
        if reason == "update" and (rank < 3 or inc is not None):
            ping_allowed = False

        self.seen_keys |= new_keys | keys
        self.incidents[incident_key] = _Incident(a.t, rank)
        self.max_rank = max(self.max_rank, rank)
        self.max_score = max(self.max_score, a.score)
        if ping_allowed:
            self.last_ping_t = a.t

        self._n += 1
        alert = self._build(a, reason, ping_allowed, new_keys)
        return self._log(Decision(alert), a)

    # ------------------------------------------------------------------------------------
    def _build(self, a: Assessment, reason: str, ping: bool, new_keys: set[str]) -> dict[str, Any]:
        vib = None
        if ping:
            vib = self.cfg["vibration"].get("critical" if a.level == "critical" else "high")
        conv = [e for e in a.evidence if e["kind"] == "conversation"]
        voice = [e for e in a.evidence if e["kind"] == "voice"]
        fresh = [e for e in conv if evidence_key(e) in new_keys]
        top = max(fresh, key=lambda e: e["score"]) if fresh else (conv[0] if conv else None)
        why = a.reasons[0]
        scam_driven = a.score >= self.elevated_at  # otherwise the level comes from voice signals alone
        if top is not None and scam_driven:
            why = f"The caller used {CATEGORY_NAMES.get(top['category'], top['category'])} (at {_fmt(top['t'])})."
        elif voice:
            why = next((r for r in a.reasons if "indicators present" in r), why)
        return {
            "id": f"a{self._n}",
            "t": a.t,
            "level": a.level,
            "kind": "indicator" if a.level == "elevated" else "warning",
            "headline": HEADLINES[a.level],
            "reason": why,
            "reasons": a.reasons,
            "trigger": reason,  # new | escalation | update
            "evidence": {
                "transcript": top["excerpt"] if top else None,
                "matched": top["matched"] if top else [],
                "spans": top["spans"] if top else [],
                "detected_at": top["t"] if top else a.t,
                "conversation": conv,
                "voice": voice,
            },
            "conversation_risk": a.level,
            "voice_authenticity": a.voice_authenticity,
            "recommended_action": a.recommended_action,
            "uncertainty": a.uncertainty,
            "sound": "critical" if (ping and a.level == "critical") else ("high" if ping else None),
            "vibrate": vib,
            "quiet_mode": self.quiet_mode,
        }

    def _log(self, d: Decision, a: Assessment) -> Decision:
        self.log.append({"t": a.t, "level": a.level, "alerted": d.alert is not None, "suppressed": d.suppressed,
                         "ping": bool(d.alert and d.alert["sound"])})
        return d
