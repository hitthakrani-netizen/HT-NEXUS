"""Deterministic single-best stock signal selection for HT NEXUS."""
from __future__ import annotations

from math import isfinite


def _rank_value(signal: dict, key: str) -> float:
    try:
        value = float(signal.get(key, 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return float("-inf")
    return value if isfinite(value) else float("-inf")


def select_strongest_signal(signals):
    """Return at most one candidate using the app's documented ranking order.

    Rank score is primary; setup score and risk/reward break ties. Invalid
    non-dict entries are ignored, and an empty/invalid collection returns None.
    """
    candidates = [item for item in (signals or []) if isinstance(item, dict)]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            _rank_value(item, "rank_score"),
            _rank_value(item, "score"),
            _rank_value(item, "risk_reward"),
        ),
    )
