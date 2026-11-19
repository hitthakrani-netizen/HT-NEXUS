import pandas as pd

from indicators import resample_ohlcv
from strategy_engine import _completed_15m_bars, diagnose_symbol


def _ohlcv(index):
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 100.0,
            "volume": 1000,
        },
        index=index,
    )


def test_15m_resampling_is_anchored_to_nse_0915_session_open():
    index = pd.date_range(
        "2026-11-18 09:15",
        "2026-11-18 15:15",
        freq="1min",
        tz="Asia/Kolkata",
    )
    frame = _ohlcv(index)
    fifteen = resample_ohlcv(frame, "15min")

    assert fifteen.loc[pd.Timestamp("2026-11-18 09:15", tz="Asia/Kolkata"), "volume"] == 15000
    assert fifteen.loc[pd.Timestamp("2026-11-18 15:00", tz="Asia/Kolkata"), "volume"] == 15000
    # The 15:15-15:30 bucket has only its first one-minute candle at this point.
    assert fifteen.loc[pd.Timestamp("2026-11-18 15:15", tz="Asia/Kolkata"), "volume"] == 1000


def test_completed_15m_confirmation_excludes_the_still_forming_bucket():
    index = pd.date_range(
        "2026-11-18 09:15",
        "2026-11-18 15:15",
        freq="1min",
        tz="Asia/Kolkata",
    )
    completed = _completed_15m_bars(_ohlcv(index))

    assert completed.index[-1] == pd.Timestamp("2026-11-18 15:00", tz="Asia/Kolkata")
    assert pd.Timestamp("2026-11-18 15:15", tz="Asia/Kolkata") not in completed.index
    assert completed.iloc[-1]["volume"] == 15000


def test_completed_15m_confirmation_waits_for_full_interval():
    index = pd.date_range(
        "2026-11-18 15:00",
        "2026-11-18 15:13",
        freq="1min",
        tz="Asia/Kolkata",
    )
    assert _completed_15m_bars(_ohlcv(index)).empty


def test_diagnostics_name_the_failed_long_and_short_filters():
    intra_index = pd.date_range(
        "2026-11-18 09:15",
        periods=300,
        freq="1min",
        tz="Asia/Kolkata",
    )
    daily_index = pd.date_range(
        "2025-01-01",
        periods=220,
        freq="B",
        tz="Asia/Kolkata",
    )
    result = diagnose_symbol(_ohlcv(intra_index), _ohlcv(daily_index), strategy="SWING")

    assert "Daily EMA alignment" in result["LONG failed filters"]
    assert "Daily EMA alignment" in result["SHORT failed filters"]
    assert "Intraday EMA trend" in result["LONG failed filters"]
    assert "Best-side diagnostic" in result
