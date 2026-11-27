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



def _backtest_frames(day_layout):
    """Build enough prior daily history plus a controlled intraday exit path."""
    tz = "Asia/Kolkata"
    daily_index = pd.date_range("2024-01-01", periods=300, freq="B", tz=tz)
    daily = _flat_ohlcv(daily_index)
    pieces = []
    for day, bars in day_layout:
        pieces.extend(pd.date_range(f"{day} 09:15", periods=bars, freq="1min", tz=tz))
    intra = _flat_ohlcv(pd.DatetimeIndex(pieces))
    return intra, daily


def _one_entry(monkeypatch):
    def fake_signal_at(intra, daily, regime, strategy, i):
        return ("LONG", 100.0, 2.0) if i == 220 else None
    monkeypatch.setattr("backtest._signal_at", fake_signal_at)


def test_backtest_uses_stop_first_when_stop_and_target_touch_same_bar(monkeypatch):
    _one_entry(monkeypatch)
    intra, daily = _backtest_frames([("2026-01-05", 223)])
    intra.iloc[221, intra.columns.get_loc("high")] = 106.0
    intra.iloc[221, intra.columns.get_loc("low")] = 96.0

    result = run_backtest("TEST", intra, daily, strategy="SWING")
    trade = result["trades"].iloc[0]
    assert trade["exit_reason"] == "STOP (stop-first same-bar rule)"
    assert trade["exit"] == 97.0


def test_backtest_fills_gap_stop_at_adverse_session_open(monkeypatch):
    _one_entry(monkeypatch)
    intra, daily = _backtest_frames([("2026-01-05", 223)])
    intra.iloc[221, intra.columns.get_loc("open")] = 95.0
    intra.iloc[221, intra.columns.get_loc("high")] = 100.0
    intra.iloc[221, intra.columns.get_loc("low")] = 94.0
    intra.iloc[221, intra.columns.get_loc("close")] = 96.0

    result = run_backtest("TEST", intra, daily, strategy="SWING")
    trade = result["trades"].iloc[0]
    assert trade["exit_reason"] == "GAP STOP (filled at adverse session open)"
    assert trade["exit"] == 95.0


def test_btst_backtest_exits_on_first_bar_of_next_session(monkeypatch):
    _one_entry(monkeypatch)
    intra, daily = _backtest_frames([
        ("2026-01-05", 221),
        ("2026-01-06", 2),
    ])

    result = run_backtest("TEST", intra, daily, strategy="BTST")
    trade = result["trades"].iloc[0]
    assert trade["exit_reason"] == "BTST next-session exit"
    assert trade["exit_time"].date().isoformat() == "2026-01-06"


def test_swing_backtest_applies_three_session_time_exit(monkeypatch):
    _one_entry(monkeypatch)
    intra, daily = _backtest_frames([
        ("2026-01-05", 221),
        ("2026-01-06", 1),
        ("2026-01-07", 1),
        ("2026-01-08", 1),
        ("2026-01-09", 1),
    ])

    result = run_backtest(
        "TEST", intra, daily, strategy="SWING", max_holding_sessions=3
    )
    trade = result["trades"].iloc[0]
    assert trade["exit_reason"] == "TIME EXIT"
    assert trade["exit_time"].date().isoformat() == "2026-01-08"
