"""Two-strategy (BTST/SWING) signal engine for HT NEXUS PRO.

Signals only. This module never places or manages orders.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

from indicators import add_indicators, resample_ohlcv

IST = ZoneInfo("Asia/Kolkata")
BTST_START = time(15, 15)
BTST_END = time(15, 29)


def _last_valid(df: pd.DataFrame, column: str):
    s = df[column].dropna()
    return float(s.iloc[-1]) if not s.empty else np.nan


def market_regime(nifty_daily: pd.DataFrame) -> dict:
    """Classify NIFTY using daily EMA20/50/200 and 20-session momentum."""
    try:
        x = add_indicators(nifty_daily)
        if len(x) < 205:
            return {"regime": "NEUTRAL", "reason": "Need at least 205 daily candles for regime confirmation."}
        last = x.iloc[-1]
        bullish = last.close > last.ema20 > last.ema50 > last.ema200
        bearish = last.close < last.ema20 < last.ema50 < last.ema200
        momentum = float(last.close / x.close.iloc[-21] - 1)
        if bullish and momentum > 0:
            label, reason = "BULLISH", "NIFTY is above rising EMA20/50/200 with positive 20-session momentum."
        elif bearish and momentum < 0:
            label, reason = "BEARISH", "NIFTY is below EMA20/50/200 with negative 20-session momentum."
        else:
            label, reason = "NEUTRAL", "NIFTY trend and momentum are mixed; directional exposure is restricted."
        return {"regime": label, "reason": reason, "momentum_20d": momentum}
    except Exception as exc:
        return {"regime": "NEUTRAL", "reason": f"Regime unavailable: {exc}"}


def relative_strength(stock_daily: pd.DataFrame, benchmark_daily: pd.DataFrame, lookback: int = 20):
    """Stock total return minus benchmark total return over aligned recent sessions."""
    if len(stock_daily) <= lookback or len(benchmark_daily) <= lookback:
        return None
    s = stock_daily["close"].dropna()
    b = benchmark_daily["close"].dropna()
    sr = float(s.iloc[-1] / s.iloc[-lookback - 1] - 1)
    br = float(b.iloc[-1] / b.iloc[-lookback - 1] - 1)
    return sr - br


def _daily_confirmation(daily: pd.DataFrame, side: str) -> bool:
    d = add_indicators(daily)
    if len(d) < 205:
        return False
    x = d.iloc[-1]
    if side == "LONG":
        return bool(x.close > x.ema20 > x.ema50 and x.rsi14 >= 50)
    return bool(x.close < x.ema20 < x.ema50 and x.rsi14 <= 50)


def _intraday_checks(intraday: pd.DataFrame, side: str):
    x = add_indicators(intraday)
    if len(x) < 220:
        return None
    last, prev = x.iloc[-1], x.iloc[-2]
    long_break = last.close > last.breakout_level
    short_break = last.close < last.breakdown_level
    # False-break guard: close must clear the prior level by a small buffer,
    # wick retracement cannot exceed 45% of candle range, and close near extreme.
    rng = max(float(last.high - last.low), 1e-9)
    if side == "LONG":
        wick_fraction = (last.high - last.close) / rng
        false_break_ok = bool(long_break and last.close >= last.breakout_level * 1.001
                              and wick_fraction <= 0.45 and last.close > last.open)
        trend_ok = bool(last.close > last.ema20 > last.ema50)
        momentum_ok = bool(last.rsi14 >= 52 and last.macd > last.macd_signal)
        vwap_ok = bool(last.close > last.vwap)
    else:
        wick_fraction = (last.close - last.low) / rng
        false_break_ok = bool(short_break and last.close <= last.breakdown_level * 0.999
                              and wick_fraction <= 0.45 and last.close < last.open)
        trend_ok = bool(last.close < last.ema20 < last.ema50)
        momentum_ok = bool(last.rsi14 <= 48 and last.macd < last.macd_signal)
        vwap_ok = bool(last.close < last.vwap)
    volume_ok = bool(last.volume_ratio >= 1.2)
    # 15-minute close confirmation prevents acting on an unfinished aggregate.
    fifteen = add_indicators(resample_ohlcv(intraday, "15min"))
    if len(fifteen) < 55:
        mtf_ok = False
    else:
        m = fifteen.iloc[-1]
        mtf_ok = bool(m.close > m.ema20 if side == "LONG" else m.close < m.ema20)
    return {
        "frame": x, "last": last, "trend": trend_ok, "momentum": momentum_ok,
        "vwap": vwap_ok, "volume": volume_ok, "breakout": false_break_ok,
        "mtf": mtf_ok,
    }


def evaluate_symbol(
    symbol: str,
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    regime: dict,
    strategy: str = "BTST",
    sector_relative_strength=None,
) -> dict | None:
    """Return a single eligible symbol signal dict, or None.

    Compatible with app.py's evaluate_symbol(symbol, intra, daily, regime,
    strategy=..., sector_relative_strength=...) call.
    """
    strategy = str(strategy).upper()
    if strategy not in {"BTST", "SWING"}:
        raise ValueError("Only BTST and SWING strategies are supported.")
    now = datetime.now(IST)
    if strategy == "BTST" and not (BTST_START <= now.time().replace(tzinfo=None) <= BTST_END):
        return None
    if intraday is None or daily is None or len(intraday) < 220 or len(daily) < 205:
        return None

    # Score both directions; regime must not oppose the signal.
    candidates = []
    for side in ("LONG", "SHORT"):
        # Stock-only logic: never gate a stock setup using NIFTY/index direction.
        if not _daily_confirmation(daily, side):
            continue
        checks = _intraday_checks(intraday, side)
        if not checks:
            continue
        passed = [checks[k] for k in ("trend", "momentum", "vwap", "volume", "breakout", "mtf")]
        if not all(passed):
            continue
        if sector_relative_strength is not None:
            # Relative strength is signed: positive supports long; negative supports short.
            if side == "LONG" and sector_relative_strength <= 0:
                continue
            if side == "SHORT" and sector_relative_strength >= 0:
                continue

        last = checks["last"]
        atr_value = float(last.atr14)
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        entry = float(last.close)
        stop_distance = 1.5 * atr_value
        target_distance = 2.5 * atr_value
        stop = entry - stop_distance if side == "LONG" else entry + stop_distance
        target = entry + target_distance if side == "LONG" else entry - target_distance
        rr = target_distance / stop_distance
        score = 7.0
        if sector_relative_strength is not None:
            score += min(1.0, abs(float(sector_relative_strength)) * 10)
        reasons = (
            f"Daily trend confirmed; intraday EMA trend, RSI/MACD, VWAP, "
            f"15-minute confirmation, relative volume and false-breakout guard passed. "
            f"Signal is based on this stock's own price/volume data; no NIFTY filter."
        )
        candidates.append({
            "symbol": symbol, "side": side, "strategy": strategy,
            "entry": entry, "stop_loss": stop, "target": target,
            "risk_reward": rr, "score": round(float(score), 2),
            "reasons": reasons, "atr": atr_value,
            "sector_relative_strength": sector_relative_strength,
            "signal_time": str(intraday.index[-1]),
        })
    return max(candidates, key=lambda s: s["score"]) if candidates else None


def best_signal(candidates):
    """Return one top-ranked confirmed signal, or None (UI renders NO TRADE)."""
    valid = [x for x in (candidates or []) if isinstance(x, dict)]
    if not valid:
        return None
    return sorted(valid, key=lambda x: (x.get("score", 0), x.get("risk_reward", 0)), reverse=True)[0]
