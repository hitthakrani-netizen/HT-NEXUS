"""Stock-only BTST/SWING signal engine; no index regime filter or order placement."""
from __future__ import annotations
from datetime import datetime, time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from indicators import add_indicators, resample_ohlcv

IST = ZoneInfo("Asia/Kolkata")
BTST_START, BTST_END = time(15, 16), time(15, 29)
MIN_QUALITY_SCORE = 85.0


def _completed_15m_bars(intraday: pd.DataFrame) -> pd.DataFrame:
    """Return only fully closed 15-minute bars aligned to the NSE 09:15 session open.

    Dhan intraday timestamps label candle starts. A base candle is complete at
    its start timestamp plus the observed base interval; a 15m bar is usable
    only when its own start plus 15 minutes is no later than that boundary.
    """
    if intraday is None or intraday.empty:
        return pd.DataFrame()
    if not isinstance(intraday.index, pd.DatetimeIndex):
        raise TypeError("15-minute confirmation requires a DatetimeIndex.")
    source = intraday.sort_index()
    fifteen = resample_ohlcv(source, "15min")
    steps = source.index.to_series().diff().dropna()
    steps = steps[(steps > pd.Timedelta(0)) & (steps <= pd.Timedelta(minutes=30))]
    base_step = steps.median() if not steps.empty else pd.Timedelta(minutes=1)
    if pd.isna(base_step) or base_step <= pd.Timedelta(0):
        base_step = pd.Timedelta(minutes=1)
    completed_until = source.index[-1] + base_step
    completed_mask = (fifteen.index + pd.Timedelta(minutes=15)) <= completed_until
    return fifteen.loc[completed_mask]


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
    volume = bool(last.volume_ratio >= 1.50)
    body_pct = abs(float(last.close) - float(last.open)) / rng
    close_location = (float(last.close) - float(last.low)) / rng
    strong_candle = bool(body_pct >= 0.60 and (close_location >= 0.80 if side == "LONG" else close_location <= 0.20))
    fifteen = _completed_15m_bars(intraday)
    fifteen = add_indicators(fifteen) if len(fifteen) else fifteen
    mtf = False
    if len(fifteen) >= 55:
        m = fifteen.iloc[-1]
        mtf_trend = bool(m.close > m.ema20 if side == "LONG" else m.close < m.ema20)
        mtf_volume = bool(pd.notna(m.volume_ratio) and m.volume_ratio >= 1.20)
        mtf = mtf_trend and mtf_volume
    return {"last": last, "trend": trend, "momentum": momentum, "vwap": vwap_ok,
            "volume": volume, "strong_candle": strong_candle, "body_pct": body_pct,
            "close_location": close_location, "breakout": breakout, "mtf": mtf, "fifteen": fifteen}


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
            "Daily trend": 20.0,
            "Intraday EMA trend": 10.0,
            "RSI + MACD": 10.0 if checks["momentum"] else 0.0,
            "VWAP": 10.0 if checks["vwap"] else 0.0,
            "Relative volume >= 1.5x": 20.0 if checks["volume"] else 0.0,
            "Strong candle body/close": 10.0 if checks["strong_candle"] else 0.0,
            "Breakout / breakdown": 15.0 if checks["breakout"] else 0.0,
            "15m trend + volume": 5.0 if checks["mtf"] else 0.0,
        }
        score = sum(points.values())
        required_checks = ("momentum", "vwap", "volume", "strong_candle", "breakout", "mtf")
        confirmations = sum(bool(checks[k]) for k in required_checks)
        if not all(bool(checks[k]) for k in required_checks):
            continue
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
            # Use an actual 30-minute time window, independent of the selected candle interval.
            cutoff_30m = session.index[-1] - pd.Timedelta(minutes=30)
            last_30 = session.loc[session.index > cutoff_30m]
            prior_bars = session.loc[session.index <= cutoff_30m]
            last_30_volume = float(last_30.volume.sum())
            typical_30_volume = float(prior_bars.volume.mean() * len(last_30)) if not prior_bars.empty and len(last_30) else 0.0
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
        if score < MIN_QUALITY_SCORE or confirmations < len(required_checks):
            continue
        if sector_relative_strength is not None:
            aligned = sector_relative_strength > 0 if side == "LONG" else sector_relative_strength < 0
            if aligned:
                score = min(100.0, score + 5.0)
        # Rank otherwise-qualified stocks by relative strength of their evidence.
        directional_close = checks["close_location"] if side == "LONG" else 1.0 - checks["close_location"]
        volume_strength = min(max((float(last.volume_ratio) - 1.5) / 2.0, 0.0), 1.0)
        breakout_level = float(last.breakout_level if side == "LONG" else last.breakdown_level)
        breakout_margin_pct = abs(float(last.close) / breakout_level - 1.0) * 100 if breakout_level > 0 else 0.0
        breakout_strength = min(breakout_margin_pct / 0.75, 1.0)
        vwap_extension = abs(float(last.close) / float(last.vwap) - 1.0) if pd.notna(last.vwap) and float(last.vwap) > 0 else 1.0
        vwap_strength = max(0.0, 1.0 - vwap_extension / 0.04)
        rank_score = 35.0 * volume_strength + 20.0 * min(checks["body_pct"], 1.0) + 20.0 * directional_close + 15.0 * breakout_strength + 10.0 * vwap_strength
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
            "target3": target, "risk_reward": reward/risk, "score": round(score,1), "rank_score": round(rank_score,1),
            "score_label": "STRONG" if score >= 85 else "QUALIFIED",
            "reasons": reasons + f"; Candle body: {checks['body_pct']:.0%}; close location: {checks['close_location']:.0%}; relative volume: {float(last.volume_ratio):.2f}x", "atr": atr_value, "checks": checks, "btst_quality": btst_quality,
            "sector_relative_strength": sector_relative_strength,
            "signal_time": str(intraday.index[-1]),
        })
    return max(candidates, key=lambda s: (s.get("rank_score", 0), s["score"], s["risk_reward"])) if candidates else None


def diagnose_symbol(intraday, daily, strategy="BTST"):
    """Explain the strongest-side blockers when a stock does not qualify.

    This is diagnostic-only: it never changes signal eligibility or lowers a
    confirmation threshold.
    """
    strategy = str(strategy).upper()
    if strategy not in {"BTST", "SWING"}:
        raise ValueError("Only BTST and SWING strategies are supported.")
    if intraday is None or daily is None:
        return {"Best-side diagnostic": "Market-data frame missing."}
    if len(intraday) < 220 or len(daily) < 205:
        return {
            "Best-side diagnostic": (
                f"Insufficient history: need 220 intraday / 205 daily; "
                f"received {len(intraday)} / {len(daily)}."
            )
        }

    d = add_indicators(daily)
    day = d.iloc[-1]
    side_results = {}
    for side in ("LONG", "SHORT"):
        if side == "LONG":
            daily_trend = bool(day.close > day.ema20 > day.ema50 > day.ema200)
            daily_rsi = bool(50 <= day.rsi14 <= 75)
        else:
            daily_trend = bool(day.close < day.ema20 < day.ema50 < day.ema200)
            daily_rsi = bool(25 <= day.rsi14 <= 50)
        checks = _intraday_checks(intraday, side)
        flags = {
            "Daily EMA alignment": daily_trend,
            "Daily RSI range": daily_rsi,
        }
        if checks is None:
            flags["Intraday history/indicators"] = False
        else:
            flags.update({
                "Intraday EMA trend": bool(checks["trend"]),
                "RSI + MACD": bool(checks["momentum"]),
                "VWAP direction": bool(checks["vwap"]),
                "Relative volume >= 1.5x": bool(checks["volume"]),
                "Strong candle body/close": bool(checks["strong_candle"]),
                "Breakout / breakdown": bool(checks["breakout"]),
                "Completed 15m trend + volume": bool(checks["mtf"]),
            })
            if strategy == "BTST":
                session = intraday.loc[
                    [idx.date() == intraday.index[-1].date() for idx in intraday.index]
                ]
                if session.empty:
                    close_quality = False
                else:
                    session_high = float(session.high.max())
                    session_low = float(session.low.min())
                    session_close = float(session.close.iloc[-1])
                    session_range = max(session_high - session_low, 1e-9)
                    close_location = (session_close - session_low) / session_range
                    vwap_value = float(checks["last"].vwap)
                    vwap_extension = (
                        abs((session_close / vwap_value - 1) * 100)
                        if pd.notna(vwap_value) and vwap_value > 0 else 999.0
                    )
                    directional_close = close_location if side == "LONG" else 1.0 - close_location
                    close_quality = bool(directional_close >= 0.50 and vwap_extension <= 4.0)
                flags["BTST closing quality"] = close_quality

        failures = [name for name, passed in flags.items() if not passed]
        passed_count = len(flags) - len(failures)
        side_results[side] = {
            "passed": passed_count,
            "total": len(flags),
            "failures": failures,
        }

    best_side = max(side_results, key=lambda side: side_results[side]["passed"])
    best = side_results[best_side]
    blocker_text = ", ".join(best["failures"]) if best["failures"] else "none found"
    return {
        "LONG checks": f'{side_results["LONG"]["passed"]}/{side_results["LONG"]["total"]} PASS',
        "LONG failed filters": ", ".join(side_results["LONG"]["failures"]) or "None",
        "SHORT checks": f'{side_results["SHORT"]["passed"]}/{side_results["SHORT"]["total"]} PASS',
        "SHORT failed filters": ", ".join(side_results["SHORT"]["failures"]) or "None",
        "Best-side diagnostic": (
            f"{best_side}: {best['passed']}/{best['total']} checks pass; "
            f"remaining blockers: {blocker_text}"
        ),
    }


def best_signal(candidates):
    """Select one strongest stock candidate using the same rank as the app."""
    valid = [x for x in (candidates or []) if isinstance(x, dict)]
    return max(
        valid,
        key=lambda x: (
            float(x.get("rank_score", 0) or 0),
            float(x.get("score", 0) or 0),
            float(x.get("risk_reward", 0) or 0),
        ),
    ) if valid else None
