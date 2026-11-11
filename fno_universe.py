"""Build the complete NSE stock-option universe from Dhan's daily instrument master."""
from __future__ import annotations

from io import BytesIO
import re

import pandas as pd
import requests

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


class UniverseError(RuntimeError):
    """Raised when the current Dhan master cannot produce a trustworthy F&O universe."""


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def _clean_security_id(value) -> str:
    """Normalize CSV numeric IDs such as 1333, 1333.0, or whitespace-padded values."""
    if pd.isna(value):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        number = float(raw)
        if number.is_integer() and number > 0:
            return str(int(number))
    except (TypeError, ValueError, OverflowError):
        pass
    return ""


def load_nse_stock_fno_universe(timeout: int = 30) -> list[dict]:
    """Return every NSE EQ cash stock that has NSE OPTSTK contracts in Dhan's master.

    The primary join is the official UNDERLYING_SECURITY_ID -> cash SECURITY_ID
    relationship. Symbol matching is retained only as a guarded fallback for rows
    where Dhan has not populated the underlying ID. No fixed/partial stock list is used.
    """
    try:
        response = requests.get(
            MASTER_URL,
            headers={"User-Agent": "HT-NEXUS/1.0", "Accept": "text/csv,*/*"},
            timeout=timeout,
        )
        response.raise_for_status()
        frame = pd.read_csv(BytesIO(response.content), low_memory=False)
    except Exception as exc:
        raise UniverseError(f"Dhan instrument master download/parse failed: {exc}") from exc

    required = {
        "EXCH_ID", "SEGMENT", "SECURITY_ID", "INSTRUMENT",
        "UNDERLYING_SECURITY_ID", "UNDERLYING_SYMBOL", "SYMBOL_NAME",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise UniverseError(
            "Dhan detailed instrument master schema changed; missing columns: "
            + ", ".join(sorted(missing))
        )

    for col in ("EXCH_ID", "SEGMENT", "INSTRUMENT"):
        frame[col] = frame[col].map(_clean_text)

    # NSE stock options only. OPTIDX/index contracts are deliberately excluded.
    opt = frame[
        (frame["EXCH_ID"] == "NSE")
        & (frame["SEGMENT"] == "D")
        & (frame["INSTRUMENT"] == "OPTSTK")
    ].copy()
    if opt.empty:
        raise UniverseError(
            "Dhan master contains zero NSE OPTSTK rows. Refusing to scan a partial or guessed list."
        )

    cash_mask = (
        (frame["EXCH_ID"] == "NSE")
        & (frame["SEGMENT"] == "E")
        & (frame["INSTRUMENT"] == "EQUITY")
    )
    # Current Dhan detailed master provides SERIES. Keep EQ shares only, but
    # tolerate a future schema where the series column is absent.
    if "SERIES" in frame.columns:
        cash_mask &= frame["SERIES"].map(_clean_text).eq("EQ")
    cash = frame.loc[cash_mask].copy()
    if cash.empty:
        raise UniverseError("Dhan master has NSE OPTSTK contracts but no NSE EQ cash-equity rows.")

    cash["_security_id"] = cash["SECURITY_ID"].map(_clean_security_id)
    cash["_symbol"] = cash["SYMBOL_NAME"].map(_clean_text)
    cash = cash[(cash["_security_id"] != "") & (cash["_symbol"] != "")]
    cash = cash.drop_duplicates(subset=["_security_id"], keep="first")

    cash_by_id = {
        row["_security_id"]: row
        for _, row in cash.iterrows()
    }
    cash_by_symbol = {
        row["_symbol"]: row
        for _, row in cash.iterrows()
    }

    # Count listed option contracts by underlying ID and use the exact ID join.
    opt["_underlying_id"] = opt["UNDERLYING_SECURITY_ID"].map(_clean_security_id)
    opt["_underlying_symbol"] = opt["UNDERLYING_SYMBOL"].map(_clean_text)
    contract_counts = {}
    symbol_counts = {}
    for _, option in opt.iterrows():
        underlying_id = option["_underlying_id"]
        underlying_symbol = option["_underlying_symbol"]
        if underlying_id:
            contract_counts[underlying_id] = contract_counts.get(underlying_id, 0) + 1
        if underlying_symbol:
            symbol_counts[underlying_symbol] = symbol_counts.get(underlying_symbol, 0) + 1

    # Build unique underlying -> cash-equity matches. ID is authoritative;
    # symbol fallback is used only when the derivative row has no underlying ID.
    matched = {}
    unmatched = []
    for _, option in opt.iterrows():
        underlying_id = option["_underlying_id"]
        underlying_symbol = option["_underlying_symbol"]
        # Prefer Dhan's underlying ID. If it does not resolve to an NSE_EQ
        # row (some master snapshots use a different cash/derivative ID field),
        # fall back to the exact normalized underlying symbol.
        cash_row = cash_by_id.get(underlying_id) if underlying_id else None
        if cash_row is None and underlying_symbol:
            cash_row = cash_by_symbol.get(underlying_symbol)
        if cash_row is None:
            if underlying_id or underlying_symbol:
                unmatched.append((underlying_id, underlying_symbol))
            continue

        cash_id = cash_row["_security_id"]
        symbol = cash_row["_symbol"]
        matched[symbol] = {
            "symbol": symbol,
            "security_id": cash_id,
            "fno_security_id": underlying_id or cash_id,
            "sector": "UNKNOWN",
            "option_contract_count": contract_counts.get(underlying_id, 0) if underlying_id else symbol_counts.get(underlying_symbol, 0),
        }

    rows = sorted(matched.values(), key=lambda item: item["symbol"])
    if not rows:
        sample = ", ".join(
            f"{sid or 'no-id'}:{sym or 'no-symbol'}" for sid, sym in unmatched[:5]
        )
        raise UniverseError(
            "Dhan master has NSE OPTSTK rows, but none mapped to NSE EQ cash stocks "
            f"by underlying security ID. OPTSTK rows={len(opt)}, NSE EQ rows={len(cash)}. "
            f"Sample unmatched underlyings: {sample or 'not available'}"
        )

    return rows
