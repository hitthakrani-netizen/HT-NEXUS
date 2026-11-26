from datetime import datetime
from zoneinfo import ZoneInfo

from live_market_data import extract_dhan_nse_eq_quotes, apply_live_quote_to_signal

IST = ZoneInfo("Asia/Kolkata")


def _payload(last_trade_time="25/11/2026 10:17:00", **updates):
    row = {
        "last_price": 100.2,
        "volume": 120000,
        "last_trade_time": last_trade_time,
        "net_change": 0.2,
        "ohlc": {"close": 100.0},
    }
    row.update(updates)
    return {"status": "success", "data": {"NSE_EQ": {"2885": row}}}


def test_extracts_fresh_nse_eq_quote_and_volume():
    now = datetime(2026, 11, 25, 10, 17, 30, tzinfo=IST)
    quotes, errors = extract_dhan_nse_eq_quotes(
        _payload(), [{"symbol": "RELIANCE", "security_id": "2885"}], now=now
    )
    assert errors == {}
    assert quotes["RELIANCE"]["last_price"] == 100.2
    assert quotes["RELIANCE"]["volume"] == 120000
    assert quotes["RELIANCE"]["quote_age_seconds"] == 30.0


def test_rejects_stale_or_placeholder_quote_timestamp():
    now = datetime(2026, 11, 25, 10, 17, 30, tzinfo=IST)
    payload = _payload(last_trade_time="25/11/2026 10:00:00")
    quotes, errors = extract_dhan_nse_eq_quotes(
        payload, [{"symbol": "RELIANCE", "security_id": "2885"}], now=now
    )
    assert quotes == {}
    assert "stale" in errors["RELIANCE"]


def test_rejects_zero_volume_and_missing_security_quote():
    now = datetime(2026, 11, 25, 10, 17, 30, tzinfo=IST)
    payload = _payload(volume=0)
    quotes, errors = extract_dhan_nse_eq_quotes(
        payload,
        [{"symbol": "RELIANCE", "security_id": "2885"}, {"symbol": "TCS", "security_id": "11536"}],
        now=now,
    )
    assert quotes == {}
    assert "zero day volume" in errors["RELIANCE"]
    assert "No NSE_EQ quote" in errors["TCS"]


def test_refreshes_entry_and_risk_levels_from_fresh_live_ltp():
    now = datetime(2026, 11, 25, 10, 17, 30, tzinfo=IST)
    quotes, _ = extract_dhan_nse_eq_quotes(
        _payload(), [{"symbol": "RELIANCE", "security_id": "2885"}], now=now
    )
    signal = {"side": "LONG", "entry": 100.0, "atr": 2.0, "reasons": "trend PASS"}
    passed, reason = apply_live_quote_to_signal(signal, quotes["RELIANCE"], now=now)
    assert passed is True
    assert "refreshed" in reason
    assert signal["entry"] == 100.2
    assert signal["stop_loss"] == 97.2
    assert signal["target"] == 105.2
    assert signal["live_quote"]["volume"] == 120000


def test_rejects_live_price_that_has_run_too_far_from_signal_candle():
    now = datetime(2026, 11, 25, 10, 17, 30, tzinfo=IST)
    quotes, _ = extract_dhan_nse_eq_quotes(
        _payload(last_price=102.0), [{"symbol": "RELIANCE", "security_id": "2885"}], now=now
    )
    signal = {"side": "LONG", "entry": 100.0, "atr": 2.0, "reasons": "trend PASS"}
    passed, reason = apply_live_quote_to_signal(signal, quotes["RELIANCE"], now=now)
    assert passed is False
    assert "ATR" in reason
