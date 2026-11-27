"""Fresh NSE_EQ quote validation using Dhan's real-time marketfeed snapshot.

Dhan documents /marketfeed/quote as real-time at request time. This module
validates each NSE cash-equity quote's last-trade timestamp and day volume,
then refreshes a technical signal's entry/SL/targets from that quote. It does
not place orders and does not access or manage credentials.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_MAX_QUOTE_AGE_SECONDS = 180
MAX_CANDLE_DEVIATION_ATR = 0.35


def _now_ist(value=None) -> datetime:
    current = value or datetime.now(IST)
    stamp = pd.Timestamp(current)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.to_pydatetime()


def _parse_trade_time(value) -> datetime:
    if value is None or not str(value).strip():
        raise ValueError("Dhan quote has no last_trade_time.")
    if isinstance(value, (int, float)):
        stamp = pd.to_datetime(value, unit="s", utc=True).tz_convert(IST)
        return stamp.to_pydatetime()
    text = str(value).strip()
    parsed = None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=IST)
            break
        except ValueError:
            continue
    if parsed is None:
        stamp = pd.Timestamp(text)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(IST)
        else:
            stamp = stamp.tz_convert(IST)
        parsed = stamp.to_pydatetime()
    return parsed


def extract_dhan_nse_eq_quotes(
    payload: dict,
    universe: list[dict],
    now=None,
    max_age_seconds: int = DEFAULT_MAX_QUOTE_AGE_SECONDS,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Return fresh, usable NSE_EQ quotes keyed by symbol and per-symbol rejects.

    The caller should request the entire F&O cash-equity universe in one
    Dhan /marketfeed/quote call. Missing/stale/no-volume instruments are
    excluded rather than being assigned fabricated prices.
    """
    if not isinstance(payload, dict) or payload.get("status") == "failure":
        raise ValueError("Dhan NSE_EQ marketfeed returned an invalid/failure response.")
    data = payload.get("data")
    segment = data.get("NSE_EQ") if isinstance(data, dict) else None
    if not isinstance(segment, dict):
        raise ValueError("Dhan NSE_EQ marketfeed response is missing data.NSE_EQ.")

    current = _now_ist(now)
    quotes: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for item in universe or []:
        symbol = str(item.get("symbol", "UNKNOWN")).upper()
        security_id = str(item.get("security_id", "")).strip()
        raw = segment.get(security_id)
        if not isinstance(raw, dict):
            errors[symbol] = "DATA GAP: no NSE_EQ quote returned for this security ID."
            continue
        try:
            ltp = float(raw.get("last_price", 0) or 0)
            volume = int(float(raw.get("volume", 0) or 0))
            ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), dict) else {}
            previous_close = float(ohlc.get("close", 0) or 0)
            trade_time = _parse_trade_time(raw.get("last_trade_time"))
            age = (current - trade_time).total_seconds()
            if not isfinite(ltp) or ltp <= 0:
                raise ValueError("NSE_EQ quote LTP is missing or non-positive.")
            if not isfinite(previous_close) or previous_close <= 0:
                raise ValueError("NSE_EQ quote previous close is missing or non-positive.")
            if volume <= 0:
                raise ValueError("INACTIVE: quote has zero day volume.")
            if age < -5:
                raise ValueError(f"NSE_EQ quote timestamp is in the future ({age:.1f}s).")
            if age > max_age_seconds:
                raise ValueError(
                    f"INACTIVE: last trade is stale ({age:.0f}s; maximum {max_age_seconds}s)."
                )
            net_change = float(raw.get("net_change", ltp - previous_close) or 0)
            quotes[symbol] = {
                "security_id": security_id,
                "last_price": ltp,
                "previous_close": previous_close,
                "net_change": net_change,
                "net_change_pct": (ltp / previous_close - 1.0) * 100.0,
                "volume": volume,
                "last_trade_time": trade_time.isoformat(),
                "quote_age_seconds": round(max(0.0, age), 1),
            }
        except (TypeError, ValueError, OverflowError) as exc:
            reason = str(exc)
            errors[symbol] = reason if reason.startswith("INACTIVE:") else f"DATA ERROR: {reason}"
    return quotes, errors


def apply_live_quote_to_signal(
    signal: dict,
    quote: dict,
    now=None,
    max_age_seconds: int = DEFAULT_MAX_QUOTE_AGE_SECONDS,
    max_candle_deviation_atr: float = MAX_CANDLE_DEVIATION_ATR,
) -> tuple[bool, str]:
    """Revalidate a candle signal against a fresh NSE_EQ LTP and refresh levels."""
    if not isinstance(signal, dict) or not isinstance(quote, dict):
        return False, "Signal or live NSE_EQ quote is missing."
    try:
        current = _now_ist(now)
        trade_time = _parse_trade_time(quote.get("last_trade_time"))
        age = (current - trade_time).total_seconds()
        if age < -5 or age > max_age_seconds:
            return False, f"Live NSE_EQ quote became stale ({age:.0f}s; max {max_age_seconds}s)."
        side = str(signal.get("side", "")).upper()
        if side not in {"LONG", "SHORT"}:
            return False, "Signal direction is invalid."
        live_price = float(quote.get("last_price", 0) or 0)
        candle_price = float(signal.get("entry", 0) or 0)
        atr = float(signal.get("atr", 0) or 0)
        volume = int(quote.get("volume", 0) or 0)
        if not all(isfinite(x) and x > 0 for x in (live_price, candle_price, atr)):
            return False, "Live price, candle close, or ATR is invalid."
        if volume <= 0:
            return False, "Live NSE_EQ quote has no traded volume."
        deviation_atr = abs(live_price - candle_price) / atr
        if deviation_atr > max_candle_deviation_atr:
            return False, (
                f"Live LTP differs from completed-candle close by {deviation_atr:.2f} ATR "
                f"(maximum {max_candle_deviation_atr:.2f}); refusing a stale/chased entry."
            )
        risk_distance = 1.5 * atr
        target1_distance = risk_distance
        target2_distance = 1.5 * risk_distance
        target3_distance = 2.5 * atr
        if side == "LONG":
            stop = live_price - risk_distance
            target1 = live_price + target1_distance
            target2 = live_price + target2_distance
            target3 = live_price + target3_distance
        else:
            stop = live_price + risk_distance
            target1 = live_price - target1_distance
            target2 = live_price - target2_distance
            target3 = live_price - target3_distance
        signal["entry"] = round(live_price, 2)
        signal["stop_loss"] = round(stop, 2)
        signal["target1"] = round(target1, 2)
        signal["target2"] = round(target2, 2)
        signal["target3"] = round(target3, 2)
        signal["target"] = round(target3, 2)
        signal["live_quote"] = {
            **quote,
            "quote_age_seconds": round(max(0.0, age), 1),
            "candle_deviation_atr": round(deviation_atr, 3),
        }
        signal["reasons"] = (
            str(signal.get("reasons", ""))
            + f"; Fresh Dhan NSE_EQ quote PASS: LTP {live_price:.2f}, "
              f"age {max(0.0, age):.0f}s, day volume {volume:,}"
        ).strip("; ")
        return True, "Fresh NSE_EQ LTP confirmed; entry and risk levels refreshed."
    except (TypeError, ValueError, OverflowError) as exc:
        return False, f"Live NSE_EQ quote validation failed: {exc}"
