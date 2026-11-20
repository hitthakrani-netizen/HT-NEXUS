import pandas as pd

from strategy_engine import (
    MIN_QUALITY_SCORE,
    _score_confirmation_checks,
    evaluate_symbol,
)


def test_four_strong_confluences_can_qualify_without_all_six():
    checks = {
        "momentum": True,
        "vwap": True,
        "volume": True,
        "strong_candle": False,
        "breakout": True,
        "mtf": False,
    }

    points, score, count, qualified = _score_confirmation_checks(checks)

    assert score == 85.0
    assert count == 4
    assert score >= MIN_QUALITY_SCORE
    assert points["Strong candle body/close"] == 0.0
    assert points["15m trend + volume"] == 0.0
    assert qualified is True


def test_four_checks_below_quality_floor_are_rejected():
    checks = {
        "momentum": True,
        "vwap": False,
        "volume": True,
        "strong_candle": True,
        "breakout": False,
        "mtf": True,
    }

    _, score, count, qualified = _score_confirmation_checks(checks)

    assert count == 4
    assert score == 75.0
    assert qualified is False


def test_high_score_without_momentum_or_vwap_is_rejected():
    checks = {
        "momentum": False,
        "vwap": False,
        "volume": True,
        "strong_candle": True,
        "breakout": True,
        "mtf": True,
    }

    _, score, count, qualified = _score_confirmation_checks(checks)

    assert score == 80.0
    assert count == 4
    assert qualified is False


def test_stock_signal_does_not_require_bullish_nifty_regime(monkeypatch):
    index = pd.date_range(
        "2026-11-20 09:15",
        periods=220,
        freq="1min",
        tz="Asia/Kolkata",
    )
    intraday = pd.DataFrame(
        {
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 2000,
        },
        index=index,
    )
    daily_index = pd.date_range(
        "2025-12-01",
        periods=205,
        freq="B",
        tz="Asia/Kolkata",
    )
    daily = pd.DataFrame(
        {
            "open": 299.0,
            "high": 302.0,
            "low": 298.0,
            "close": 300.0,
            "volume": 100000,
            "ema20": 290.0,
            "ema50": 280.0,
            "ema200": 270.0,
            "rsi14": 60.0,
        },
        index=daily_index,
    )

    last = pd.Series(
        {
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "vwap": 99.0,
            "volume_ratio": 1.8,
            "breakout_level": 99.0,
            "breakdown_level": 97.0,
            "atr14": 2.0,
        }
    )
    fake_checks = {
        "last": last,
        "trend": True,
        "momentum": True,
        "vwap": True,
        "volume": True,
        "strong_candle": False,
        "body_pct": 0.55,
        "close_location": 0.75,
        "breakout": True,
        "mtf": False,
        "fifteen": pd.DataFrame(),
    }
    monkeypatch.setattr("strategy_engine.add_indicators", lambda frame: frame)
    monkeypatch.setattr("strategy_engine._intraday_checks", lambda frame, side: fake_checks)

    signal = evaluate_symbol(
        "TEST",
        intraday,
        daily,
        {"regime": "BEARISH", "reason": "Index is weak"},
        strategy="SWING",
    )

    assert signal is not None
    assert signal["side"] == "LONG"
    assert signal["score"] == 85.0
