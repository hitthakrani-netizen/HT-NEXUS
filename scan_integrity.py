"""Full-universe scan coverage checks for HT NEXUS."""
from __future__ import annotations


DATA_FAILURE_PREFIXES = (
    "SCAN ERROR:",
    "Dhan rejected",
    "Dhan HTTP",
    "Dhan API",
    "Network error",
    "Data/API error:",
)


def _is_data_failure(status: str) -> bool:
    """Return whether a row hides an unassessed stock rather than an ineligible one.

    A returned quote with no recent trade or zero day volume is a completed
    eligibility decision: that underlying is not currently tradeable. Likewise,
    a valid candle response with too little history means that stock is outside
    the strategy's history eligibility. Neither should let one illiquid/newly
    listed F&O name veto every other fully screened candidate.

    Missing quote records, malformed quote data and worker/API failures remain
    scan-integrity failures because the stock could not be assessed reliably.
    """
    if status.startswith("Rejected: no fresh NSE_EQ live quote"):
        return "INACTIVE:" not in status
    return status.startswith(DATA_FAILURE_PREFIXES)


def assess_scan_coverage(diagnostics: list[dict], expected_count: int) -> dict:
    """Check that every F&O underlying was either assessed or explicitly excluded.

    Technical rejections, inactive/stale quote exclusions and insufficient
    history are completed per-stock decisions. Missing data records, processing
    errors and unprocessed underlyings keep the whole scan INCOMPLETE so the
    app never publishes a potentially non-strongest candidate.
    """
    rows = [row for row in (diagnostics or []) if isinstance(row, dict) and row.get("Stock")]
    failures = [
        row for row in rows
        if _is_data_failure(str(row.get("Status", "")))
    ]
    inactive_quotes = [
        row for row in rows
        if str(row.get("Status", "")).startswith("Rejected: no fresh NSE_EQ live quote")
        and "INACTIVE:" in str(row.get("Status", ""))
    ]
    insufficient_history = [
        row for row in rows
        if str(row.get("Status", "")).startswith("Insufficient history:")
    ]
    reasons = []
    if expected_count <= 0:
        reasons.append("F&O universe is empty.")
    if len(rows) != expected_count:
        reasons.append(f"Processed {len(rows)} of {expected_count} F&O stocks.")
    if failures:
        reasons.append(f"{len(failures)} stock worker(s) had missing quote/data or processing failures.")
    return {
        "complete": not reasons,
        "expected": int(expected_count),
        "processed": len(rows),
        "failed": len(failures),
        "inactive_quotes": len(inactive_quotes),
        "insufficient_history": len(insufficient_history),
        "eligible_for_confirmation": max(
            0, len(rows) - len(failures) - len(inactive_quotes) - len(insufficient_history)
        ),
        "reason": " ".join(reasons) if reasons else (
            "Every F&O stock was screened or explicitly excluded by quote/history eligibility."
        ),
    }
