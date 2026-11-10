"""NIFTY-filtered NSE stock BTST/SWING signal engine. No orders are placed."""
from __future__ import annotations
from datetime import datetime, time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from indicators import add_indicators, resample_ohlcv

IST = ZoneInfo("Asia/Kolkata")
BTST_START, BTST_END = time(15, 15), time(15, 29)
MIN_QUALITY_SCORE = 70.0


def market_regime(nifty_daily: pd.DataFrame) -> dict:
    try:
        x = add_indicators(nifty_daily)
        if len(x) < 205:
            return {"regime": "NEUTRAL", "reason": "Insufficient NIFTY history."}
        z = x.iloc[-1]
        if z.close > z.ema20 > z.ema50 > z.ema200:
            return {"regime": "BULLISH", "reason": "NIFTY daily trend bullish."}
        if z.close < z.ema20 < z.ema50 < z.ema200:
            return {"regime": "BEARISH", "reason": "NIFTY daily trend bearish."}
        return {"regime": "NEUTRAL", "reason": "NIFTY trend mixed."}
    except Exception as exc:
        return {"regime": "NEUTRAL", "reason": f"Regime unavailable: {exc}"}


def relative_strength(stock_daily: pd.DataFrame, benchmark_daily: pd.DataFrame, lookback: int = 20):
    if len(stock_daily) <= lookback or len(benchmark_daily) <= lookback:
        return None
    s, b = stock_daily.close.dropna(), benchmark_daily.close.dropna()
    return float(s.iloc[-1] / s.iloc[-lookback-1] - b.iloc[-1] / b.iloc[-lookback-1])


def _intraday_checks(intraday: pd.DataFrame, side: str):
    x = add_indicators(intraday)
    if len(x) < 220:
        return None
    last = x.iloc[-1]
    rng = max(float(last.high - last.low), 1e-9)
    if side == "LONG":
        breakout = bool(last.close > last.breakout_level and last.close >= last.breakout_level * 1.001
                        and (last.high-last.close)/rng <= .55 and last.close > last.open)
        trend = bool(last.close > last.ema20 and last.ema20 > last.ema50)
        momentum = bool(last.rsi14 >= 50 and last.macd > last.macd_signal)
        vwap_ok = bool(last.close > last.vwap)
    else:
        breakout = bool(last.close < last.breakdown_level and last.close <= last.breakdown_level * .999
                        and (last.close-last.low)/rng <= .55 and last.close < last.open)
        trend = bool(last.close < last.ema20 and last.ema20 < last.ema50)
        momentum = bool(last.rsi14 <= 50 and last.macd < last.macd_signal)
        vwap_ok = bool(last.close < last.vwap)
    volume = bool(last.volume_ratio >= 1.15)
    fifteen = add_indicators(resample_ohlcv(intraday, "15min"))
    mtf = False
    if len(fifteen) >= 55:
        m = fifteen.iloc[-1]
        mtf = bool(m.close > m.ema20 if side == "LONG" else m.close < m.ema20)
    return {"last": last, "trend": trend, "momentum": momentum, "vwap": vwap_ok,
            "volume": volume, "breakout": breakout, "mtf": mtf, "fifteen": fifteen}


def evaluate_symbol(symbol, intraday, daily, regime, strategy="BTST", sector_relative_strength=None, enforce_btst_window=False):
    """Weighted confluence: daily direction + intraday trend, then rank by evidence.
    Score is a setup-quality index, NOT a probability of profit.
    """
    strategy = str(strategy).upper()
    if strategy not in {"BTST", "SWING"}:
        raise ValueError("Only BTST and SWING strategies are supported.")
    now = datetime.now(IST)
    if strategy == "BTST" and enforce_btst_window and not (BTST_START <= now.time().replace(tzinfo=None) <= BTST_END):
        return None
    if intraday is None or daily is None or len(intraday) < 100 or len(daily) < 205:
        return None
    d = add_indicators(daily)
    day = d.iloc[-1]
    candidates = []
    for side in ("LONG", "SHORT"):
        daily_trend = bool(day.close > day.ema20 > day.ema50 > day.ema200) if side == "LONG" else bool(day.close < day.ema20 < day.ema50 < day.ema200)
        if not daily_trend:
            continue
        # NIFTY regime is context only, never a mandatory stock-signal filter.
        # This keeps stock BTST/SWING signals independent of index direction.
        if (side == "LONG" and not 50 <= day.rsi14 <= 75) or (side == "SHORT" and not 25 <= day.rsi14 <= 50):
            continue
        checks = _intraday_checks(intraday, side)
        if not checks or not checks["trend"]:
            continue
        last = checks["last"]
        # Fixed, transparent weights; missing/weak confirmations lower score rather than
        # silently disqualifying every otherwise valid trend setup.
        points = {
            "Daily trend": 25.0,
            "Intraday EMA trend": 15.0,
            "RSI + MACD": 15.0 if checks["momentum"] else 0.0,
            "VWAP": 10.0 if checks["vwap"] else 0.0,
            "Relative volume": 15.0 if checks["volume"] else 0.0,
            "Breakout / breakdown": 10.0 if checks["breakout"] else 0.0,
            "15m confirmation": 10.0 if checks["mtf"] else 0.0,
        }
        score = sum(points.values())
        confirmations = sum(bool(checks[k]) for k in ("momentum", "vwap", "volume", "breakout", "mtf"))
        btst_quality = {}
        if strategy == "BTST":
            # End-of-day strength using only candles available at scan time.
            session = intraday.loc[[idx.date() == intraday.index[-1].date() for idx in intraday.index]]
            if session.empty:
                continue
            session_high, session_low = float(session.high.max()), float(session.low.min())
            session_close = float(session.close.iloc[-1])
            day_range = max(session_high - session_low, 1e-9)
            close_location = (session_close - session_low) / day_range
            previous_close = float(daily.close.iloc[-1]) if len(daily) else 0.0
            day_return_pct = ((session_close / previous_close) - 1) * 100 if previous_close > 0 else 0.0
            last_30 = session.tail(6)
            prior_bars = session.iloc[:-6]
            last_30_volume = float(last_30.volume.sum())
            typical_30_volume = float(prior_bars.volume.mean() * len(last_30)) if not prior_bars.empty else 0.0
            late_volume_ratio = last_30_volume / typical_30_volume if typical_30_volume > 0 else 0.0
            vwap_extension_pct = abs((session_close / float(last.vwap) - 1) * 100) if pd.notna(last.vwap) and float(last.vwap) > 0 else 999.0
            btst_quality = {"Close location": round(close_location,3), "Day change %": round(day_return_pct,2),
                            "Last 30m volume ratio": round(late_volume_ratio,2), "VWAP extension %": round(vwap_extension_pct,2)}
            if side == "LONG":
                score += 10 if close_location >= .80 else 5 if close_location >= .65 else 0
                score += 5 if late_volume_ratio >= 1.20 else 0
                score -= 5 if vwap_extension_pct > 2.5 else 0
                if close_location < .50 or vwap_extension_pct > 4.0:
                    continue
            else:
                weakness_location = 1.0 - close_location
                score += 10 if weakness_location >= .80 else 5 if weakness_location >= .65 else 0
                score += 5 if late_volume_ratio >= 1.20 else 0
                score -= 5 if vwap_extension_pct > 2.5 else 0
                if weakness_location < .50 or vwap_extension_pct > 4.0:
                    continue
        score = min(100.0, score)
        if score < MIN_QUALITY_SCORE or confirmations < 2:
            continue
        if sector_relative_strength is not None:
            aligned = sector_relative_strength > 0 if side == "LONG" else sector_relative_strength < 0
            if aligned:
                score = min(100.0, score + 5.0)
        atr_value = float(last.atr14)
        entry = float(last.close)
        if not np.isfinite(atr_value) or atr_value <= 0 or not np.isfinite(entry) or entry <= 0:
            continue
        risk = 1.5 * atr_value
        reward = 2.5 * atr_value
        stop = entry-risk if side == "LONG" else entry+risk
        target = entry+reward if side == "LONG" else entry-reward
        mtf = checks["fifteen"].iloc[-1] if len(checks["fifteen"]) else None
        reasons = "; ".join(f"{k}: {'PASS' if v else 'not confirmed'}" for k,v in points.items())
        candidates.append({
            "symbol": symbol, "side": side, "strategy": strategy, "entry": entry,
            "stop_loss": stop, "target": target, "target1": entry+(risk if side=="LONG" else -risk),
            "target2": entry+(1.5*risk if side=="LONG" else -1.5*risk),
            "target3": target, "risk_reward": reward/risk, "score": round(score,1),
            "score_label": "STRONG" if score >= 85 else "QUALIFIED",
            "reasons": reasons, "atr": atr_value, "checks": checks, "btst_quality": btst_quality,
            "sector_relative_strength": sector_relative_strength,
            "signal_time": str(intraday.index[-1]),
        })
    return max(candidates, key=lambda s: (s["score"], s["risk_reward"])) if candidates else None


def best_signal(candidates):
    valid = [x for x in (candidates or []) if isinstance(x, dict)]
    return sorted(valid, key=lambda x: (x.get("score", 0), x.get("risk_reward", 0)), reverse=True)[0] if valid else None
