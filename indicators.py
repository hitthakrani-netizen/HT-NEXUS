"""Pandas-based technical indicators and timeframe resampling for HT NEXUS PRO."""
from __future__ import annotations

import pandas as pd
import numpy as np

OHLCV = ["open", "high", "low", "close", "volume"]


def _validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("OHLCV DataFrame is empty.")
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    missing = [c for c in ("open", "high", "low", "close") if c not in out.columns]
    if missing:
        raise ValueError(f"Missing OHLC columns: {missing}")
    if "volume" not in out:
        out["volume"] = 0.0
    for col in OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["open", "high", "low", "close"]).sort_index()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0)
    return result


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, signal_line, line - signal_line


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].fillna(0)
    if isinstance(df.index, pd.DatetimeIndex):
        # Reset VWAP at each exchange-local trading date.
        dates = pd.Series(df.index.date, index=df.index)
        pv = typical * vol
        cum_pv = pv.groupby(dates).cumsum()
        cum_vol = vol.groupby(dates).cumsum().replace(0, np.nan)
        return cum_pv / cum_vol
    return (typical * vol).cumsum() / vol.cumsum().replace(0, np.nan)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    avg = volume.shift(1).rolling(period, min_periods=period).mean()
    return volume / avg.replace(0, np.nan)


def breakout_levels(df: pd.DataFrame, lookback: int = 20):
    """Prior-bar rolling levels; current candle is excluded to avoid look-ahead."""
    return (
        df["high"].shift(1).rolling(lookback, min_periods=lookback).max(),
        df["low"].shift(1).rolling(lookback, min_periods=lookback).min(),
    )


def add_indicators(df: pd.DataFrame, breakout_lookback: int = 20) -> pd.DataFrame:
    out = _validate_frame(df)
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi14"] = rsi(out["close"], 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(out["close"])
    out["vwap"] = vwap(out)
    out["atr14"] = atr(out, 14)
    out["volume_ratio"] = volume_ratio(out["volume"], 20)
    out["breakout_level"], out["breakdown_level"] = breakout_levels(out, breakout_lookback)
    return out


def resample_ohlcv(df: pd.DataFrame, timeframe: str = "15min") -> pd.DataFrame:
    """Resample intraday OHLCV; timestamps are treated as candle-start times."""
    out = _validate_frame(df)
    if not isinstance(out.index, pd.DatetimeIndex):
        raise TypeError("Resampling requires a DatetimeIndex.")
    result = out.resample(timeframe, label="right", closed="right", origin="start_day").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    })
    return result.dropna(subset=["open", "high", "low", "close"])


def multi_timeframe(df: pd.DataFrame, timeframes=("15min", "60min")) -> dict[str, pd.DataFrame]:
    """Return original and resampled OHLCV frames keyed by timeframe."""
    result = {"base": _validate_frame(df)}
    for timeframe in timeframes:
        result[str(timeframe)] = resample_ohlcv(df, str(timeframe))
    return result


# Friendly aliases for external integrations.
calculate_indicators = add_indicators
resample_timeframe = resample_ohlcv
