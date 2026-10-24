"""Transparent risk engine.

Three evidence streams are kept separately and never blindly summed:

* scam      - conversational findings (rule-based), per-category, decaying with call time
* synthetic - synthetic-speech indicators (detector or scenario-injected)
* manipulation - voice-manipulation indicators (detector or scenario-injected)

The overall level is driven by the scam evidence. Voice signals alone are capped
(`voice.voice_only_max_level`), and can never produce a "fraud" verdict. If scam evidence is
already at least elevated, strong voice indicators may raise the level one step, and that is
reported as its own reason so the reasoning stays visible.

All thresholds come from config/risk_rules.yaml.
"""
from __future__ import annotations

from typing import Any

from .config import LEVELS, level_rank
from .models import Assessment, DimensionState, Finding, VoiceResult, SRC_NO_AUDIO, SRC_SIMULATED, SRC_UNAVAILABLE

CATEGORY_NAMES = {
    "credential_request": "a request for a one-time code, PIN or password",
    "remote_access": "a request to install or allow remote access",
    "safe_account_transfer": "an instruction to move money to a 'safe' account",
    "urgent_money_request": "an urgent request for money",
    "untraceable_payment": "a request to pay with gift cards, crypto or wire",
    "impersonation": "a claim to be an official or relative",
    "threat_pressure": "threats or pressure",
    "bypass_verification": "pressure to avoid official verification",
    "secrecy": "a request to keep the call secret",
}


class RiskEngine:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules
        self.findings: list[Finding] = []
        self.voice: dict[str, VoiceResult | None] = {"synthetic": None, "manipulation": None}
        self.voice_history: dict[str, list[VoiceResult]] = {"synthetic": [], "manipulation": []}

    # ---- evidence intake -----------------------------------------------------------------
    def reset(self) -> None:
        self.findings.clear()
        self.voice = {"synthetic": None, "manipulation": None}
        self.voice_history = {"synthetic": [], "manipulation": []}

    def add_findings(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    def add_voice(self, result: VoiceResult) -> None:
        prev = self.voice[result.dimension]
        if result.status in ("unavailable", "no_audio") and prev is not None and prev.status not in ("unavailable", "no_audio"):
            return  # "no detector" must never erase evidence that already exists (e.g. scenario-injected)
        self.voice[result.dimension] = result
        self.voice_history[result.dimension].append(result)

    # ---- scoring -------------------------------------------------------------------------
    @staticmethod
    def _fresh(age: float, half_life: float) -> float:
        return 0.5 ** (max(age, 0.0) / half_life) if half_life > 0 else 1.0

    def _scam(self, t: float) -> tuple[float, dict[str, tuple[float, Finding]]]:
        cfg = self.rules["scam"]
        best: dict[str, tuple[float, Finding]] = {}
        for f in self.findings:
            eff = f.score * self._fresh(t - f.t, cfg["half_life_s"])
            if f.category not in best or eff > best[f.category][0]:
                best[f.category] = (eff, f)
        if not best:
            return 0.0, best
        ranked = sorted(best.values(), key=lambda x: -x[0])
        top = ranked[0][0]
        corroboration = sum(eff for eff, _ in ranked[1:] if eff >= cfg["min_effective_weight"])
        score = min(cfg["max_score"], top + cfg["corroboration_factor"] * corroboration)
        return score, best

    def _voice_state(self, dim: str, t: float) -> tuple[float | None, DimensionState, VoiceResult | None]:
        cfg = self.rules["voice"]
        hist = [r for r in self.voice_history[dim] if r.status in ("indicators", "clear")]
        latest = self.voice[dim]
        if latest is None:
            return None, DimensionState(None, "unavailable", SRC_UNAVAILABLE, "Not analysed"), None
        if latest.status == "no_audio":
            return None, DimensionState(None, "no_audio", SRC_NO_AUDIO, "Not analysed: no audio", latest.method), latest
        if latest.status == "unavailable":
            return None, DimensionState(None, "unavailable", SRC_UNAVAILABLE, "Detector unavailable", latest.method), latest
        # strongest recent indicator, decayed; a later 'clear' result does not erase it instantly
        best, best_r = 0.0, None
        for r in hist:
            # experimental detectors have intentionally capped confidence; they are opt-in and shown as such
            if r.score is None or (r.confidence < cfg["min_confidence"] and not r.experimental):
                continue
            v = r.score * self._fresh(t - r.t, cfg["half_life_s"])
            if v > best:
                best, best_r = v, r
        if best_r is None:
            label = "Inconclusive" if latest.status == "inconclusive" else "No indicators detected"
            status = "inconclusive" if latest.status == "inconclusive" else "clear"
            return (None if status == "inconclusive" else 0.0), DimensionState(
                None if status == "inconclusive" else 0.0, status, latest.source, label, latest.method
            ), latest
        status = "indicators" if best >= cfg["indicator_threshold"] else "clear"
        label = "Indicators present" if status == "indicators" else "No strong indicators"
        if best_r.experimental:
            label += " (experimental)"
        return best, DimensionState(round(best, 3), status, best_r.source, label, best_r.method), best_r

    @staticmethod
    def _level_for(score: float, levels: dict[str, float]) -> str:
        if score >= levels["critical"]:
            return "critical"
        if score >= levels["high"]:
            return "high"
        if score >= levels["elevated"]:
            return "elevated"
        return "low"

    # ---- main entry point ----------------------------------------------------------------
    def assess(self, t: float) -> Assessment:
        rules = self.rules
        scam_score, by_cat = self._scam(t)
        syn_v, syn_state, syn_r = self._voice_state("synthetic", t)
        man_v, man_state, man_r = self._voice_state("manipulation", t)
        vcfg = rules["voice"]

        level = self._level_for(scam_score, rules["levels"])
        reasons: list[str] = []
        evidence: list[dict[str, Any]] = []
        uncertainty: list[str] = []
        bumped = False
        primary: str | None = None

        # scam evidence ------------------------------------------------------------------
        ranked = sorted(by_cat.items(), key=lambda kv: -kv[1][0])
        for cat, (eff, f) in ranked:
            if eff < 0.15:
                continue
            evidence.append(
                {
                    "kind": "conversation",
                    "category": cat,
                    "label": f.label,
                    "t": f.t,
                    "excerpt": f.excerpt,
                    "matched": f.matched,
                    "spans": f.spans,
                    "score": round(eff, 3),
                    "source": f.source,
                    "method": f.method,
                    "finding_id": f.id,
                }
            )
        if ranked and ranked[0][1][0] >= 0.15:
            primary = ranked[0][0]
            top_f = ranked[0][1][1]
            reasons.append(f"The caller used {CATEGORY_NAMES.get(primary, primary)} (at {_fmt(top_f.t)}).")
            extra = [CATEGORY_NAMES.get(c, c) for c, (e, _) in ranked[1:] if e >= rules["scam"]["min_effective_weight"]]
            if extra:
                reasons.append("Corroborating tactics: " + "; ".join(extra) + ".")

        # voice evidence (kept separate) -------------------------------------------------
        voice_strong = False
        for dim, state, res in (("synthetic", syn_state, syn_r), ("manipulation", man_state, man_r)):
            if state.status == "indicators" and res is not None:
                name = "Synthetic-speech indicators" if dim == "synthetic" else "Voice-manipulation indicators"
                informational = bool(res.experimental) and not vcfg.get("experimental_can_raise_level", False)
                reasons.append(
                    f"{name} present ({res.source}). "
                    + ("Experimental check, shown for information only: it does not change the risk level."
                       if informational else "This is not proof of fraud on its own.")
                )
                evidence.append(
                    {
                        "kind": "voice",
                        "dimension": dim,
                        "label": name,
                        "t": res.t,
                        "score": state.value,
                        "evidence": res.evidence,
                        "source": res.source,
                        "method": res.method,
                        "limitations": res.limitations,
                    }
                )
                if not informational and state.value is not None and state.value >= vcfg["indicator_threshold"]:
                    voice_strong = True

        if scam_score < rules["levels"]["elevated"]:
            # voice-only signals are capped: a synthetic voice is not a scam verdict
            if voice_strong:
                level = vcfg["voice_only_max_level"]
                uncertainty.append("Voice indicators alone are not evidence of fraud; no suspicious request has been heard.")
        elif voice_strong and vcfg.get("corroboration_bumps_level", False) and level != "critical":
            level = LEVELS[level_rank(level) + 1]
            bumped = True
            reasons.append("Voice indicators corroborate the conversational warning (raised one level; reported separately).")

        # uncertainty notes --------------------------------------------------------------
        if syn_state.status == "no_audio" and man_state.status == "no_audio":
            uncertainty.append("This scenario has no audio, so the voice detectors were not run; assessment is based on conversation content only.")
        elif syn_state.status == "unavailable" and man_state.status == "unavailable":
            uncertainty.append("Voice detectors are unavailable or not running; assessment is based on conversation content only.")
        elif syn_state.status == "unavailable":
            uncertainty.append("Synthetic-speech detector unavailable.")
        elif man_state.status == "unavailable":
            uncertainty.append("Voice-manipulation detector unavailable.")
        if any(e.get("source") == SRC_SIMULATED for e in evidence) or "simulated" in (syn_state.source, man_state.source):
            uncertainty.append("Some evidence is simulated by the demo scenario, not produced by a detector.")
        if not ranked:
            uncertainty.append("No suspicious request detected so far.")

        # confidence: only about how well-supported the conversation evidence is -----------
        if ranked and ranked[0][1][0] >= 0.15:
            confs = [f.confidence for _, (e, f) in ranked if e >= 0.15]
            confidence = round(min(0.95, sum(confs) / len(confs) + 0.05 * (len(confs) - 1)), 2)
        else:
            confidence = 0.0

        actions = rules["actions"]
        action = actions.get(primary, actions["default"]) if primary else "No action needed. Stay alert to requests for codes, money or remote access."
        if not primary and level != "low":
            action = "Voice indicators are not proof of fraud. Stay cautious and verify who you are speaking to before sharing anything."

        if not reasons:
            reasons.append("No suspicious requests or voice indicators so far.")

        voice_auth = _voice_authenticity(syn_state, man_state)
        return Assessment(
            t=round(t, 2),
            level=level,
            score=round(scam_score, 3),
            confidence=confidence,
            reasons=reasons,
            evidence=evidence,
            uncertainty=uncertainty,
            recommended_action=action,
            alert_eligible=level_rank(level) >= level_rank("elevated"),
            primary_category=primary,
            dimensions={
                "scam": DimensionState(round(scam_score, 3), level if scam_score else "low", "real", _scam_label(scam_score, rules), "rule-based"),
                "synthetic": syn_state,
                "manipulation": man_state,
            },
            voice_authenticity=voice_auth,
            bumped=bumped,
        )


def _scam_label(score: float, rules: dict[str, Any]) -> str:
    return {"low": "No suspicious requests", "elevated": "Suspicious signals", "high": "Likely scam tactics", "critical": "Strong scam indicators"}[
        RiskEngine._level_for(score, rules["levels"])
    ]


def _voice_authenticity(syn: DimensionState, man: DimensionState) -> str:
    if syn.status == "indicators" and man.status == "indicators":
        return "Synthetic and manipulation indicators"
    if syn.status == "indicators":
        return "Synthetic-speech indicators"
    if man.status == "indicators":
        return "Manipulation indicators"
    if syn.status == "no_audio" and man.status == "no_audio":
        return "Inconclusive (no audio to analyse)"
    if syn.status == "unavailable" and man.status == "unavailable":
        return "Inconclusive (no detector running)"
    if syn.status in ("inconclusive", "unavailable", "no_audio") or man.status in ("inconclusive", "unavailable", "no_audio"):
        return "Inconclusive"
    return "No indicators detected"


def _fmt(t: float) -> str:
    s = int(round(t))
    return f"{s // 60:02d}:{s % 60:02d}"
