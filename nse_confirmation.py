"""Conservative stock-level confirmation using NSE public website snapshots.

These endpoints are best-effort website data, not a licensed real-time feed.
They are used only as independent corroboration alongside Dhan candles and
Dhan's live option chain. Missing or contradictory NSE data must never be
silently converted into a positive signal.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd


SYMBOL_KEYS = ("symbol", "underlying", "underlying_symbol", "underlyingsymbol", "ticker")
PRICE_CHANGE_KEYS = (
    "pchange", "percentchange", "changepercentage", "pricechangepercent",
    "changeinpercent", "change", "netchange", "changepct",
)
OI_CHANGE_KEYS = (
    "changeinoi", "changeinopeninterest", "oi_change", "oichange",
    "pchangeinoi", "percentchangeinoi", "changeinoipercent",
    "chgOi", "chgOiPct",
)
OI_LATEST_KEYS = ("latestoi", "openinterest", "oi", "latest_oi")
OI_PREVIOUS_KEYS = ("prevoi", "previousoi", "previous_oi", "prev_oi")


def _normalise_symbol(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip().upper()
    text = re.sub(r"^(NSE:)", "", text)
    text = re.sub(r"[-_ ]EQ$", "", text)
    return text


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _rows(frame: Any) -> list[dict]:
    if isinstance(frame, pd.DataFrame):
        return frame.to_dict("records")
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    if isinstance(frame, dict):
        return [frame]
    return []


def _find_row(frame: Any, symbol: str) -> dict | None:
    target = _normalise_symbol(symbol)
    for row in _rows(frame):
        values = {_key(k): v for k, v in row.items()}
        found = None
        for name in SYMBOL_KEYS:
            if _key(name) in values:
                found = values[_key(name)]
                break
        if found is not None and _normalise_symbol(found) == target:
            return row
    return None


def _number(row: dict | None, names: tuple[str, ...]) -> float | None:
    if not row:
        return None
    values = {_key(k): v for k, v in row.items()}
    for name in names:
        raw = values.get(_key(name))
        if raw is None or str(raw).strip() in ("", "-", "NA", "N/A", "None"):
            continue
        try:
            number = float(str(raw).replace(",", "").replace("%", "").strip())
            if pd.notna(number):
                return number
        except (TypeError, ValueError):
            continue
    return None


def _oi_change(row: dict | None) -> float | None:
    change = _number(row, OI_CHANGE_KEYS)
    if change is not None:
        return change
    latest = _number(row, OI_LATEST_KEYS)
    previous = _number(row, OI_PREVIOUS_KEYS)
    if latest is not None and previous is not None:
        return latest - previous
    return None


def confirm_stock_with_nse(snapshot: dict, symbol: str, side: str) -> dict:
    """Require two independent NSE stock feeds and directional price agreement.

    A positive OI change is treated only as fresh positioning; direction is
    taken from the stock price-change fields and must agree with the Dhan
    candle signal. OI alone never identifies buyer versus seller.
    """
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        return {
            "available": False, "matched": False, "aligned": False,
            "score_bonus": 0, "conflict": True, "reason": "Unsupported stock direction.",
            "evidence": [],
        }

    active_frame = snapshot.get("Most Active Underlying")
    volume_frame = snapshot.get("Volume Gainers")
    oi_frame = snapshot.get("OI Spurts")
    frames = (active_frame, volume_frame, oi_frame)
    available_sources = sum(bool(_rows(frame)) for frame in frames)
    if available_sources < 2:
        return {
            "available": False, "matched": False, "aligned": False,
            "score_bonus": 0, "conflict": False,
            "reason": f"Only {available_sources}/3 NSE stock feeds returned usable rows; public NSE corroboration is unavailable.",
            "evidence": [],
        }

    active_row = _find_row(active_frame, symbol)
    volume_row = _find_row(volume_frame, symbol)
    oi_row = _find_row(oi_frame, symbol)
    evidence = []
    if active_row is not None:
        evidence.append("Most Active Underlying")
    if volume_row is not None:
        evidence.append("Volume Gainers")

    oi_delta = _oi_change(oi_row)
    oi_conflict = oi_row is not None and oi_delta is not None and oi_delta <= 0
    if oi_row is not None and oi_delta is not None and oi_delta > 0:
        evidence.append("Positive OI build-up")

    if not evidence and oi_row is None:
        return {
            "available": True, "matched": False, "aligned": False,
            "score_bonus": 0, "conflict": False,
            "reason": f"{symbol} did not appear in the available NSE stock activity, volume-gainer or OI-spurt feeds.",
            "evidence": [],
        }

    price_changes = [
        value for value in (
            _number(active_row, PRICE_CHANGE_KEYS),
            _number(volume_row, PRICE_CHANGE_KEYS),
            _number(oi_row, PRICE_CHANGE_KEYS),
        ) if value is not None
    ]
    if not price_changes:
        return {
            "available": True, "matched": True, "aligned": False,
            "score_bonus": 0, "conflict": False,
            "reason": "NSE matched the stock but returned no usable price-change direction.",
            "evidence": evidence,
            "oi_change": oi_delta,
        }

    expected_positive = side == "LONG"
    price_aligned = all(value > 0 if expected_positive else value < 0 for value in price_changes)
    if oi_conflict:
        reason = "NSE OI-spurt row shows non-positive OI change; fresh positioning is not confirmed."
    elif not price_aligned:
        reason = "NSE price-change direction is mixed or opposite to the Dhan stock signal."
    elif len(evidence) < 2:
        reason = f"Only {len(evidence)} independent NSE confirmations matched; at least two are required."
    else:
        reason = "NSE stock activity, volume/OI evidence and price direction agree with the Dhan stock signal."

    conflict = bool(oi_conflict or (price_changes and not price_aligned))
    # Two matching evidence categories are needed for a positive NSE bonus.
    # A missing/partial public snapshot is neutral; explicit contradictory data is not.
    aligned = bool(price_aligned and len(evidence) >= 2 and not oi_conflict)
    bonus = min(10.0, 2.0 * len(evidence) + 2.0) if aligned else 0.0
    return {
        "available": True,
        "conflict": conflict,
        "matched": bool(active_row is not None or volume_row is not None or oi_row is not None),
        "aligned": aligned,
        "score_bonus": bonus,
        "reason": reason,
        "evidence": evidence,
        "price_change_values": price_changes,
        "oi_change": oi_delta,
        "available_sources": available_sources,
    }
