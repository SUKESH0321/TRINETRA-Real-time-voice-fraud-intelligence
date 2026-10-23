"""Loads config/risk_rules.yaml. Thresholds are data, not code."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "risk_rules.yaml"
LEVELS = ["low", "elevated", "high", "critical"]


def load_rules(path: Path | str | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def with_overrides(rules: dict[str, Any], **sections: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of rules with section dicts merged in (used by tests / settings)."""
    out = copy.deepcopy(rules)
    for name, values in sections.items():
        out.setdefault(name, {}).update(values)
    return out


def level_rank(level: str) -> int:
    return LEVELS.index(level)
