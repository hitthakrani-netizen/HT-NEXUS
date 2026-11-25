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
)


def assess_scan_coverage(diagnostics: list[dict], expected_count: int) -> dict:
    """A partial candle/history scan must never publish a 'strongest' trade.

    A stock explicitly rejected for a stale/no-trade live quote is a completed
    screening decision. Worker exceptions and insufficient candle history are
    incomplete because they can hide a stronger eligible setup.
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
