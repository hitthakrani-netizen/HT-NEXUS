"""Historical BTST/SWING backtester for HT NEXUS PRO.

Conservative OHLC simulation: if stop and target are both touched in one bar,
the stop is assumed to fill first. Costs are configurable estimates, not a
complete Indian-market statutory charge model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import add_indicators, resample_ohlcv
from strategy_engine import market_regime


def _cost_rate(brokerage_bps: float, slippage_bps: float) -> float:
    return max(0.0, float(brokerage_bps) + float(slippage_bps)) / 10000.0


def _signal_at(intra: pd.DataFrame, daily: pd.DataFrame, regime: dict, strategy: str, i: int):
    """Causal, simplified signal check using data through bar i only."""
    history = intra.iloc[:i + 1]
    if len(history) < 220 or len(daily) < 205:
        return None
    if strategy == "BTST":
        local_time = history.index[-1].time()
        from datetime import time as _time
        if not (_time(15, 15) <= local_time.replace(tzinfo=None) <= _time(15, 29)):
            return None
    x = add_indicators(history)
    last = x.iloc[-1]
    # Daily frame supplied to the caller is filtered to sessions <= current bar date.
    d = add_indicators(daily)
    if len(d) < 205:
        return None
    side = None
    if last.close > last.ema20 > last.ema50 and last.rsi14 >= 52 and last.macd > last.macd_signal:
        side = "LONG"
    elif last.close < last.ema20 < last.ema50 and last.rsi14 <= 48 and last.macd < last.macd_signal:
        side = "SHORT"
    if side is None:
        return None
    if side == "LONG" and regime["regime"] == "BEARISH":
        return None
    if side == "SHORT" and regime["regime"] == "BULLISH":
        return None
    if strategy == "BTST" and regime["regime"] == "NEUTRAL":
        return None
    daily_last = d.iloc[-1]
    daily_ok = (daily_last.close > daily_last.ema20 > daily_last.ema50 > daily_last.ema200
                if side == "LONG" else
                daily_last.close < daily_last.ema20 < daily_last.ema50 < daily_last.ema200)
    if not daily_ok or not np.isfinite(last.atr14) or last.atr14 <= 0:
        return None
    if not (last.volume_ratio >= 1.5 and
            (last.close > last.vwap if side == "LONG" else last.close < last.vwap)):
        return None
    if side == "LONG":
        wick = (last.high - last.close) / max(last.high - last.low, 1e-9)
        if not (last.close > last.breakout_level * 1.001 and wick <= .45 and last.close > last.open):
            return None
    else:
        wick = (last.close - last.low) / max(last.high - last.low, 1e-9)
        if not (last.close < last.breakdown_level * .999 and wick <= .45 and last.close < last.open):
            return None
    fifteen = add_indicators(resample_ohlcv(history, "15min"))
    if len(fifteen) < 55:
        return None
    m = fifteen.iloc[-1]
    if not (m.close > m.ema20 if side == "LONG" else m.close < m.ema20):
        return None
    return side, float(last.close), float(last.atr14)


def run_backtest(
    symbol: str,
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    strategy: str = "BTST",
    capital: float = 100000,
    risk_per_trade: float = 0.01,
    brokerage_bps: float = 2.0,
    slippage_bps: float = 5.0,
    max_holding_sessions: int = 3,
) -> dict:
    """Run an OHLC-only long/short backtest and return {'summary', 'trades'}.

    `risk_per_trade` is the fraction of current equity risked at the initial stop.
    For compatibility with the provided app.py, the first six positional
    parameters retain its existing call signature.
    """
    strategy = str(strategy).upper()
    if strategy not in {"BTST", "SWING"}:
        raise ValueError("Only BTST and SWING strategies are supported.")
    if capital <= 0 or not 0 < risk_per_trade <= .05:
        raise ValueError("capital must be positive and risk_per_trade must be in (0, 5%].")
    if intraday is None or daily is None or intraday.empty or daily.empty:
        raise ValueError("Both intraday and daily historical frames are required.")
    intra = intraday.copy().sort_index()
    day = daily.copy().sort_index()
    if not isinstance(intra.index, pd.DatetimeIndex) or not isinstance(day.index, pd.DatetimeIndex):
        raise TypeError("Backtest data must use DatetimeIndex.")
    for frame in (intra, day):
        frame.columns = [str(c).lower() for c in frame.columns]
    for c in ("open", "high", "low", "close"):
        if c not in intra or c not in day:
            raise ValueError(f"Missing required OHLC column: {c}")
    if "volume" not in intra:
        intra["volume"] = 0
    if "volume" not in day:
        day["volume"] = 0

    fee = _cost_rate(brokerage_bps, slippage_bps)
    equity = float(capital)
    peak = equity
    max_drawdown = 0.0
    trades = []
    in_trade = False
    pos = None
    cooldown_until = -1

    # Precompute daily indicator frame, but only use rows available as of each bar.
    for i in range(220, len(intra) - 1):
        ts = intra.index[i]
        if in_trade:
            bar = intra.iloc[i]
            side = pos["side"]
            stop_hit = bar.low <= pos["stop"] if side == "LONG" else bar.high >= pos["stop"]
            target_hit = bar.high >= pos["target"] if side == "LONG" else bar.low <= pos["target"]
            exit_price = None
            reason = None
            if stop_hit:
                exit_price, reason = pos["stop"], "STOP (stop-first same-bar rule)"
            elif target_hit:
                exit_price, reason = pos["target"], "TARGET"
            else:
                elapsed_calendar = (ts.date() - pos["entry_time"].date()).days
                if strategy == "BTST" and ts.date() > pos["entry_time"].date():
                    exit_price, reason = float(bar.close), "BTST next-session exit"
                elif strategy == "SWING":
                    # Count observed exchange sessions, not calendar days (weekends/holidays excluded).
                    sessions_held = len({
                        d for d in intra.index[pos["entry_i"]:i + 1].date
                    }) - 1
                    if sessions_held >= max(1, min(3, int(max_holding_sessions))):
                        exit_price, reason = float(bar.close), "TIME EXIT"
            if exit_price is not None:
                gross = ((exit_price - pos["entry"]) / pos["entry"] if side == "LONG"
                         else (pos["entry"] - exit_price) / pos["entry"])
                net = gross - fee * 2
                pnl = pos["qty"] * pos["entry"] * net
                equity += pnl
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0)
                trades.append({
                    "symbol": symbol, "strategy": strategy, "side": side,
                    "entry_time": pos["entry_time"], "exit_time": ts,
                    "entry": pos["entry"], "exit": exit_price, "stop_loss": pos["stop"],
                    "target": pos["target"], "quantity": pos["qty"],
                    "gross_return_pct": gross * 100, "net_return_pct": net * 100,
                    "pnl": pnl, "exit_reason": reason, "equity": equity,
                    "result": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
                })
                in_trade = False
                pos = None
                cooldown_until = i + 1
                continue
            continue

        if i <= cooldown_until:
            continue
        current_day = ts.date()
        daily_available = day.loc[[idx.date() <= current_day for idx in day.index]]
        if len(daily_available) < 205:
            continue
        # Avoid using today's final daily candle before its close.
        daily_available = daily_available.loc[[idx.date() < current_day for idx in daily_available.index]]
        if len(daily_available) < 205:
            continue
        regime = market_regime(daily_available)
        sig = _signal_at(intra, daily_available, regime, strategy, i)
        if sig is None:
            continue
        side, entry, atr_value = sig
        stop_distance = 1.5 * atr_value
        target_distance = 2.5 * atr_value
        stop = entry - stop_distance if side == "LONG" else entry + stop_distance
        target = entry + target_distance if side == "LONG" else entry - target_distance
        risk_cash = equity * float(risk_per_trade)
        qty_by_risk = int(risk_cash / stop_distance) if stop_distance > 0 else 0
        qty_by_capital = int(equity / entry) if entry > 0 else 0
        qty = min(qty_by_risk, qty_by_capital)
        if qty <= 0:
            continue
        pos = {"side": side, "entry": entry, "stop": stop, "target": target,
               "qty": qty, "entry_time": ts, "entry_i": i}
        in_trade = True

    # Mark an open trade to final close, explicitly reported as end-of-data exit.
    if in_trade and pos:
        ts = intra.index[-1]
        exit_price = float(intra.iloc[-1].close)
        gross = ((exit_price - pos["entry"]) / pos["entry"] if pos["side"] == "LONG"
                 else (pos["entry"] - exit_price) / pos["entry"])
        net = gross - fee * 2
        pnl = pos["qty"] * pos["entry"] * net
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0)
        trades.append({
            "symbol": symbol, "strategy": strategy, "side": pos["side"],
            "entry_time": pos["entry_time"], "exit_time": ts, "entry": pos["entry"],
            "exit": exit_price, "stop_loss": pos["stop"], "target": pos["target"],
            "quantity": pos["qty"], "gross_return_pct": gross * 100,
            "net_return_pct": net * 100, "pnl": pnl,
            "exit_reason": "END OF DATA (open position marked to close)",
            "equity": equity, "result": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
        })

    trade_df = pd.DataFrame(trades)
    total = len(trade_df)
    wins = int((trade_df["pnl"] > 0).sum()) if total else 0
    summary = {
        "symbol": symbol, "strategy": strategy, "initial_capital": float(capital),
        "ending_equity": round(float(equity), 2),
        "net_pnl": round(float(equity - capital), 2),
        "total_trades": total,
        "win_rate_pct": round(wins / total * 100, 2) if total else 0.0,
        "average_return_pct": round(float(trade_df["net_return_pct"].mean()), 4) if total else 0.0,
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "profit_factor": (
            round(float(trade_df.loc[trade_df.pnl > 0, "pnl"].sum() /
                         abs(trade_df.loc[trade_df.pnl < 0, "pnl"].sum())), 3)
            if total and (trade_df.pnl < 0).any() else None
        ),
        "brokerage_bps_per_side": float(brokerage_bps),
        "slippage_bps_per_side": float(slippage_bps),
        "cost_model_note": "Brokerage/slippage estimates only; taxes, exchange charges, STT, GST, stamp duty, spread and impact are not fully modeled.",
    }
    return {"summary": summary, "trades": trade_df}
