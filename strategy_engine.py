"""Stock-only BTST/SWING signal engine; no index regime filter or order placement."""
from __future__ import annotations
from datetime import datetime, time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from indicators import add_indicators, resample_ohlcv

IST = ZoneInfo("Asia/Kolkata")
BTST_START, BTST_END = time(15, 16), time(15, 29)
MIN_QUALITY_SCORE = 80.0
MIN_CONFIRMATIONS = 4


def validate_btst_confirmation(pre_signal, confirmation_signal, candle_age_min, interval_minutes):
    """Validate BTST's two-stage same-stock confirmation and report every blocker.

    The final-stage quality floor deliberately reuses MIN_QUALITY_SCORE from
    evaluate_symbol. A separate hard-coded 85-point gate here would silently
    override the documented 80-point weighted-confluence threshold.
    """
    if not isinstance(pre_signal, dict):
        return False, "3:00–3:14 shortlist did not qualify."
    if not isinstance(confirmation_signal, dict):
        return False, "completed 3:15+ stock confirmation did not qualify."

    failures = []
    pre_side = str(pre_signal.get("side", "")).upper()
    final_side = str(confirmation_signal.get("side", "")).upper()
    if pre_side not in {"LONG", "SHORT"} or final_side != pre_side:
        failures.append("direction changed between shortlist and final confirmation")

    try:
        pre_rank = float(pre_signal.get("rank_score", 0) or 0)
        final_rank = float(confirmation_signal.get("rank_score", 0) or 0)
        rank_ok = final_rank >= pre_rank - 15.0
    except (TypeError, ValueError):
        pre_rank, final_rank, rank_ok = 0.0, 0.0, False
    if not rank_ok:
        failures.append("final rank fell more than 15 points below shortlist rank")

    try:
        score = float(confirmation_signal.get("score", 0) or 0)
        score_ok = score >= MIN_QUALITY_SCORE
    except (TypeError, ValueError):
        score, score_ok = 0.0, False
    if not score_ok:
        failures.append(f"final score {score:.1f} is below {MIN_QUALITY_SCORE:.1f}")

    try:
        age = float(candle_age_min)
        max_age = int(interval_minutes) + 7
        fresh_ok = 0 <= age <= max_age
    except (TypeError, ValueError):
        age, max_age, fresh_ok = 999.0, int(interval_minutes) + 7, False
    if not fresh_ok:
        failures.append(f"confirmation candle age {age:.1f}m is outside 0–{max_age}m")

    if failures:
        return False, "; ".join(failures)
    return True, (
        f"same direction; score {score:.1f}/{MIN_QUALITY_SCORE:.1f}+; "
        f"rank {final_rank:.1f} vs shortlist {pre_rank:.1f}; candle age {age:.1f}m"
    )

CONFIRMATION_WEIGHTS = {
    "RSI + MACD": ("momentum", 10.0),
    "VWAP": ("vwap", 10.0),
    "Relative volume >= 1.5x": ("volume", 20.0),
    "Strong candle body/close": ("strong_candle", 10.0),
    "Breakout / breakdown": ("breakout", 15.0),
    "15m trend + volume": ("mtf", 5.0),
}


def _score_confirmation_checks(checks: dict) -> tuple[dict, float, int, bool]:
    """Score six confirmations without making every indicator a hard gate.

    The daily EMA/RSI direction and intraday EMA trend remain mandatory in
    evaluate_symbol. A candidate then needs at least four of six confirmations,
    a score of 80/100, at least one volume/breakout anchor, and at least one
    momentum/VWAP confirmation. This preserves strong multi-factor evidence
    while avoiding the previous accidental requirement that all six pass.
    """
    points = {
        label: weight if bool(checks.get(key, False)) else 0.0
        for label, (key, weight) in CONFIRMATION_WEIGHTS.items()
    }
    passed = sum(bool(checks.get(key, False)) for key, _ in CONFIRMATION_WEIGHTS.values())
    score = 30.0 + sum(points.values())  # daily trend + intraday EMA trend
    anchor_ok = bool(checks.get("volume") or checks.get("breakout"))
    direction_ok = bool(checks.get("momentum") or checks.get("vwap"))
    qualified = (
        passed >= MIN_CONFIRMATIONS
        and score >= MIN_QUALITY_SCORE
        and anchor_ok
        and direction_ok
    )
    return points, score, passed, qualified


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
        points, score, confirmations, confluence_qualified = _score_confirmation_checks(checks)
        if not confluence_qualified:
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
        if score < MIN_QUALITY_SCORE:
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
            points, confluence_score, confluence_count, confluence_ok = _score_confirmation_checks(checks)
            flags.update({
                "Intraday EMA trend": bool(checks["trend"]),
                "RSI + MACD": bool(checks["momentum"]),
                "VWAP direction": bool(checks["vwap"]),
                "Relative volume >= 1.5x": bool(checks["volume"]),
                "Strong candle body/close": bool(checks["strong_candle"]),
                "Breakout / breakdown": bool(checks["breakout"]),
                "Completed 15m trend + volume": bool(checks["mtf"]),
                "Weighted confluence >= 80 and 4/6": bool(confluence_ok),
                "Volume or breakout anchor": bool(checks["volume"] or checks["breakout"]),
                "Momentum or VWAP confirmation": bool(checks["momentum"] or checks["vwap"]),
                "ATR / entry valid": bool(
                    np.isfinite(float(checks["last"].atr14))
                    and float(checks["last"].atr14) > 0
                    and np.isfinite(float(checks["last"].close))
                    and float(checks["last"].close) > 0
                ),
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
            "confluence_score": round(confluence_score, 1) if checks is not None else 0.0,
            "confluence_count": confluence_count if checks is not None else 0,
            "confluence_ok": confluence_ok if checks is not None else False,
        }

    best_side = max(side_results, key=lambda side: side_results[side]["passed"])
    best = side_results[best_side]
    blocker_text = ", ".join(best["failures"]) if best["failures"] else "none found"
    return {
        "LONG checks": f'{side_results["LONG"]["passed"]}/{side_results["LONG"]["total"]} PASS',
        "LONG confluence": f'{side_results["LONG"]["confluence_score"]}/100; {side_results["LONG"]["confluence_count"]}/6; {"QUALIFIED" if side_results["LONG"]["confluence_ok"] else "NOT QUALIFIED"}',
        "LONG failed filters": ", ".join(side_results["LONG"]["failures"]) or "None",
        "SHORT checks": f'{side_results["SHORT"]["passed"]}/{side_results["SHORT"]["total"]} PASS',
        "SHORT confluence": f'{side_results["SHORT"]["confluence_score"]}/100; {side_results["SHORT"]["confluence_count"]}/6; {"QUALIFIED" if side_results["SHORT"]["confluence_ok"] else "NOT QUALIFIED"}',
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
