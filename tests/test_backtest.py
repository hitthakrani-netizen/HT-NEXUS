import pandas as pd
import pytest

from backtest import run_backtest


def _flat_ohlcv(index):
    return pd.DataFrame({
        "open": 100.0,
        "high": 100.2,
        "low": 99.8,
        "close": 100.0,
        "volume": 1000,
    }, index=index)


def test_backtest_reports_zero_trades_for_non_directional_history():
    intra_index = pd.date_range("2026-01-05 09:15", periods=300, freq="5min", tz="Asia/Kolkata")
    daily_index = pd.date_range("2025-01-01", periods=220, freq="B", tz="Asia/Kolkata")
    result = run_backtest(
        "TEST", _flat_ohlcv(intra_index), _flat_ohlcv(daily_index), strategy="SWING"
    )
    assert result["summary"]["total_trades"] == 0
    assert result["summary"]["win_rate_pct"] == 0.0
    assert result["trades"].empty


def test_backtest_rejects_missing_history():
    with pytest.raises(ValueError, match="Both intraday and daily"):
        run_backtest("TEST", pd.DataFrame(), pd.DataFrame(), strategy="BTST")
