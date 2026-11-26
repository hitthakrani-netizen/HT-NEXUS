"""Full-universe scan coverage checks for HT NEXUS."""
from __future__ import annotations


DATA_FAILURE_PREFIXES = (
    "SCAN ERROR:",
    "Insufficient history:",
    "Dhan rejected",
    "Dhan HTTP",
    "Dhan API",
    "Network error",
    "Data/API error:",
    # A stock without a fresh quote was not technically screened; it could hide
    # the strongest candidate, so the whole-universe result must be INCOMPLETE.
    "Rejected: no fresh NSE_EQ live quote",
)


def assess_scan_coverage(diagnostics: list[dict], expected_count: int) -> dict:
    """A partial candle/history scan must never publish a 'strongest' trade.

    A stock rejected after a valid candle evaluation is a completed screening
    decision. Missing/stale initial quotes, worker exceptions and insufficient
    candle history are incomplete because they can hide a stronger setup.
    """
    rows = [row for row in (diagnostics or []) if isinstance(row, dict) and row.get("Stock")]
    failures = [
        row for row in rows
        if str(row.get("Status", "")).startswith(DATA_FAILURE_PREFIXES)
    ]
    reasons = []
    if expected_count <= 0:
        reasons.append("F&O universe is empty.")
    if len(rows) != expected_count:
        reasons.append(f"Processed {len(rows)} of {expected_count} F&O stocks.")
    if failures:
        reasons.append(f"{len(failures)} stock worker(s) had data/history failures.")
    return {
        "complete": not reasons,
        "expected": int(expected_count),
        "processed": len(rows),
        "failed": len(failures),
        "reason": " ".join(reasons) if reasons else "Every F&O stock completed data and history screening.",
    }
