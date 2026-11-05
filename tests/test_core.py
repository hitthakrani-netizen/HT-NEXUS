import pandas as pd
import pytest

from indicators import add_indicators, resample_ohlcv
from market_data import _normalise_candles, parse_universe
from strategy_engine import evaluate_symbol


def sample_ohlcv(rows=260, freq="5min"):
    idx = pd.date_range("2026-01-05 09:15", periods=rows, freq=freq, tz="Asia/Kolkata")
    close = pd.Series(range(rows), index=idx, dtype=float) + 100
    return pd.DataFrame({
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000,
    }, index=idx)


def test_parse_universe_ignores_invalid_rows_and_normalises():
    rows = parse_universe("reliance,2885,energy\nBAD,not-a-number,bank\n# comment\nTCS,11536,IT")
    assert rows == [
        {"symbol": "RELIANCE", "security_id": "2885", "sector": "ENERGY"},
        {"symbol": "TCS", "security_id": "11536", "sector": "IT"},
    ]


def test_normalise_candles_converts_epoch_and_numeric_ohlcv():
    payload = {
        "open": [100], "high": [102], "low": [99], "close": [101],
        "volume": [500], "timestamp": [1767604500],
    }
    result = _normalise_candles(payload)
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]
    assert result.iloc[0]["close"] == 101
    assert str(result.index.tz) == "Asia/Kolkata"


def test_normalise_candles_rejects_missing_fields():
    with pytest.raises(Exception, match="missing required fields"):
        _normalise_candles({"open": [], "close": []})


def test_indicators_and_resampling_produce_expected_columns():
    frame = sample_ohlcv()
    enriched = add_indicators(frame)
    for col in ("ema20", "ema50", "ema200", "rsi14", "macd", "vwap", "atr14", "volume_ratio", "breakout_level"):
        assert col in enriched.columns
    fifteen = resample_ohlcv(frame, "15min")
    assert not fifteen.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(fifteen.columns)


def test_strategy_rejects_unsupported_strategy():
    frame = sample_ohlcv()
    with pytest.raises(ValueError, match="Only BTST and SWING"):
        evaluate_symbol("TEST", frame, frame, {"regime": "STOCK_ONLY"}, strategy="OPTIONS")


def test_empty_market_data_is_not_silently_accepted():
    with pytest.raises(ValueError, match="OHLCV DataFrame is empty"):
        add_indicators(pd.DataFrame())
