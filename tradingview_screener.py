"""Optional TradingView Stock Screener CSV confirmation for HT NEXUS.

TradingView screener data is user-supplied via CSV export; this module does not
scrape TradingView or claim an undocumented live API. It is confirmation only.
"""
from __future__ import annotations

from io import BytesIO
import re
import pandas as pd


def _column_map(columns):
    normalized = {}
    for col in columns:
        key = re.sub(r"[^a-z0-9]+", "", str(col).strip().lower())
        normalized[key] = col
    aliases = {
        "symbol": ("symbol", "ticker", "name"),
        "rating": ("technicalrating", "rating", "recommendation", "technical"),
        "rsi": ("rsi", "rsi14"),
        "relative_volume": ("relativevolume", "relvolume", "volumevsaverage", "relativevol"),
        "price": ("price", "close", "last"),
        "ema20": ("ema20", "exponentialmovingaverage20"),
    }
    result = {}
    for target, keys in aliases.items():
        for key in keys:
            if key in normalized:
                result[target] = normalized[key]
                break
    if "symbol" not in result:
        raise ValueError("CSVમાં Symbol / Ticker column મળ્યો નથી.")
    return result


def parse_tradingview_csv(file_bytes: bytes) -> dict:
    """Parse TradingView export and return symbol-keyed normalized rows."""
    if not file_bytes:
        return {}
    try:
        frame = pd.read_csv(BytesIO(file_bytes))
    except Exception as exc:
        raise ValueError(f"TradingView CSV વાંચી શકાયો નથી: {exc}") from exc
    if frame.empty:
        raise ValueError("TradingView CSV ખાલી છે.")
    columns = _column_map(frame.columns)
    output = {}
    for _, row in frame.iterrows():
        raw_symbol = str(row.get(columns["symbol"], "")).strip().upper()
        symbol = raw_symbol.split(":")[-1].replace(".NS", "")
        if not symbol or symbol in {"NAN", "NONE"}:
            continue
        item = {"symbol": symbol}
        for key in ("rating", "rsi", "relative_volume", "price", "ema20"):
            col = columns.get(key)
            if col is None:
                item[key] = None
                continue
            value = row.get(col)
            if pd.isna(value):
                item[key] = None
            elif key == "rating":
                item[key] = str(value).strip().upper()
            else:
                try:
                    item[key] = float(str(value).replace(",", "").replace("%", "").strip())
                except (TypeError, ValueError):
                    item[key] = None
        output[symbol] = item
    if not output:
        raise ValueError("CSVમાં કોઈ માન્ય stock symbol મળ્યો નથી.")
    return output


def confirm_with_tradingview(signal: dict, screener: dict) -> dict:
    """Return a transparent directional screener confirmation for one stock."""
    symbol = str(signal.get("symbol", "")).upper()
    row = (screener or {}).get(symbol)
    side = str(signal.get("side", "")).upper()
    if not row:
        return {"matched": False, "aligned": False, "score": 0, "reason": "TradingView CSVમાં આ stock નથી."}

    bullish = side == "LONG"
    checks = {}
    rating = str(row.get("rating") or "").upper()
    if rating:
        checks["Technical Rating"] = (
            any(x in rating for x in ("STRONG BUY", "BUY")) if bullish
            else any(x in rating for x in ("STRONG SELL", "SELL"))
        )
    rsi = row.get("rsi")
    if rsi is not None:
        checks["RSI"] = (50 <= rsi <= 70) if bullish else (30 <= rsi <= 50)
    relvol = row.get("relative_volume")
    if relvol is not None:
        checks["Relative Volume"] = relvol >= 1.20
    price, ema20 = row.get("price"), row.get("ema20")
    if price is not None and ema20 is not None:
        checks["Price vs EMA20"] = price > ema20 if bullish else price < ema20

    if not checks:
        return {"matched": True, "aligned": False, "score": 0, "reason": "CSVમાં Technical Rating / RSI / Relative Volume / Price-EMA20 જેવી ઉપયોગી fields નથી."}
    passed = sum(bool(v) for v in checks.values())
    score = round(100 * passed / len(checks), 1)
    aligned = passed >= 2 and score >= 60
    reason = "; ".join(f"{k}: {'PASS' if v else 'FAIL'}" for k, v in checks.items())
    return {"matched": True, "aligned": aligned, "score": score, "reason": reason, "checks": checks}
