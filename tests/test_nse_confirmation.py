import pandas as pd

from nse_confirmation import confirm_stock_with_nse


def test_nse_confirms_long_with_active_volume_and_positive_price():
    snapshot = {
        "Most Active Underlying": pd.DataFrame([{"symbol": "RELIANCE", "pChange": 1.8}]),
        "Volume Gainers": pd.DataFrame([{"symbol": "RELIANCE", "pChange": 1.6, "volume": 1200000}]),
        "OI Spurts": pd.DataFrame([{"symbol": "RELIANCE", "changeInOi": 24000, "volume": 1000000}]),
    }
    result = confirm_stock_with_nse(snapshot, "RELIANCE", "LONG")
    assert result["available"] is True
    assert result["aligned"] is True
    assert result["score_bonus"] > 0
    assert "Positive OI build-up" in result["evidence"]


def test_nse_confirms_short_only_when_price_falls_and_oi_builds():
    snapshot = {
        "Most Active Underlying": [{"symbol": "TCS", "pChange": -1.2}],
        "Volume Gainers": [{"symbol": "TCS", "pChange": -1.4, "volume": 500000}],
        "OI Spurts": [{"symbol": "TCS", "changeInOI": 12000, "volume": 800000}],
    }
    result = confirm_stock_with_nse(snapshot, "TCS", "SHORT")
    assert result["aligned"] is True
    assert result["score_bonus"] > 0


def test_nse_rejects_price_direction_conflict():
    snapshot = {
        "Most Active Underlying": [{"symbol": "INFY", "pChange": -0.8}],
        "Volume Gainers": [{"symbol": "INFY", "pChange": -0.5}],
        "OI Spurts": [{"symbol": "INFY", "changeInOi": 10000, "volume": 900000}],
    }
    result = confirm_stock_with_nse(snapshot, "INFY", "LONG")
    assert result["aligned"] is False
    assert result["score_bonus"] == 0


def test_partial_nse_feed_is_neutral_not_a_scan_blocker():
    snapshot = {
        "Most Active Underlying": [{"symbol": "SBIN", "pChange": 1.0}],
        "Volume Gainers": pd.DataFrame(),
        "OI Spurts": None,
    }
    result = confirm_stock_with_nse(snapshot, "SBIN", "LONG")
    assert result["available"] is True
    assert result["aligned"] is False
    assert result["conflict"] is False
    assert result["score_bonus"] == 0


def test_nse_rejects_oi_unwinding_when_oi_row_is_present():
    snapshot = {
        "Most Active Underlying": [{"symbol": "ICICIBANK", "pChange": 1.0}],
        "Volume Gainers": [{"symbol": "ICICIBANK", "pChange": 1.2}],
        "OI Spurts": [{"symbol": "ICICIBANK", "changeInOI": -5000, "volume": 400000}],
    }
    result = confirm_stock_with_nse(snapshot, "ICICIBANK", "LONG")
    assert result["aligned"] is False
    assert "non-positive OI" in result["reason"]



def test_no_nse_feeds_is_neutral_and_does_not_block_dhan():
    result = confirm_stock_with_nse(
        {"Most Active Underlying": None, "Volume Gainers": pd.DataFrame(), "OI Spurts": None},
        "SBIN",
        "LONG",
    )
    assert result["available"] is False
    assert result["aligned"] is False
    assert result["conflict"] is False
    assert result["score_bonus"] == 0


def test_partial_nse_feed_with_opposite_price_direction_is_a_conflict():
    result = confirm_stock_with_nse(
        {"Most Active Underlying": [{"symbol": "SBIN", "pChange": -1.1}],
         "Volume Gainers": None, "OI Spurts": None},
        "SBIN",
        "LONG",
    )
    assert result["conflict"] is True
    assert result["aligned"] is False
    assert result["score_bonus"] == 0
