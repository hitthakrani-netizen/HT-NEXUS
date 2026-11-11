"""Build the complete NSE stock-option universe from Dhan's daily instrument master."""
from __future__ import annotations

from io import BytesIO
import pandas as pd
import requests

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"


class UniverseError(RuntimeError):
    pass


def load_nse_stock_fno_universe(timeout: int = 30) -> list[dict]:
    """Return NSE cash-equity rows whose underlying has listed OPTSTK contracts.

    Security IDs are refreshed from Dhan's current master; no hardcoded 10-stock
    sample list is used. A stock is included only when both cash EQUITY and
    stock-option OPTSTK rows are present in the same master snapshot.
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
        "UNDERLYING_SECURITY_ID", "UNDERLYING_SYMBOL", "SYMBOL_NAME", "SERIES",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise UniverseError(
            "Dhan detailed instrument master schema changed; missing columns: "
            + ", ".join(sorted(missing))
        )

    for col in ("EXCH_ID", "SEGMENT", "INSTRUMENT", "SERIES"):
        frame[col] = frame[col].astype(str).str.strip().str.upper()

    opt = frame[
        (frame["EXCH_ID"] == "NSE")
        & (frame["SEGMENT"] == "D")
        & (frame["INSTRUMENT"] == "OPTSTK")
    ].copy()
    if opt.empty:
        raise UniverseError("Dhan master contains no NSE OPTSTK rows; refusing to fall back to a partial list.")

    eligible = set(
        opt["UNDERLYING_SYMBOL"].dropna().astype(str).str.strip().str.upper()
    )
    eligible.discard("")
    cash = frame[
        (frame["EXCH_ID"] == "NSE")
        & (frame["SEGMENT"] == "E")
        & (frame["INSTRUMENT"] == "EQUITY")
        & (frame["SERIES"] == "EQ")
    ].copy()
    cash["symbol"] = cash["SYMBOL_NAME"].fillna("").astype(str).str.strip().str.upper()
    cash = cash[cash["symbol"].isin(eligible)]
    cash = cash.drop_duplicates(subset=["symbol"], keep="first")

    opt["symbol"] = opt["UNDERLYING_SYMBOL"].fillna("").astype(str).str.strip().str.upper()
    underlier_ids = (
        opt.dropna(subset=["UNDERLYING_SECURITY_ID"])
        .assign(_uid=lambda x: pd.to_numeric(x["UNDERLYING_SECURITY_ID"], errors="coerce"))
        .dropna(subset=["_uid"])
        .groupby("symbol")["_uid"].first()
        .to_dict()
    )

    rows = []
    for _, row in cash.iterrows():
        symbol = row["symbol"]
        try:
            cash_id = str(int(float(row["SECURITY_ID"])))
            fno_id = str(int(float(underlier_ids.get(symbol, cash_id))))
        except (TypeError, ValueError, OverflowError):
            continue
        rows.append({
            "symbol": symbol,
            "security_id": cash_id,
            "fno_security_id": fno_id,
            "sector": "UNKNOWN",
        })

    rows.sort(key=lambda item: item["symbol"])
    if not rows:
        raise UniverseError(
            "Dhan master downloaded, but no NSE EQ cash stocks matched active OPTSTK underlyings."
        )
    return rows
