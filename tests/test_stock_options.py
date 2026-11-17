from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock_options import scan_stock_option_chain


IST = ZoneInfo("Asia/Kolkata")


def _leg(delta=0.55):
    return {
        "top_bid_price": 100.0,
        "top_ask_price": 102.0,
        "last_price": 101.0,
        "volume": 5000,
        "oi": 25000,
        "top_bid_quantity": 1200,
        "top_ask_quantity": 900,
        "greeks": {"delta": delta},
        "previous_oi": 20000,
        "previous_volume": 4000,
        "previous_close_price": 96.0,
        "security_id": 98765,
    }


class FakeDhan:
    def __init__(self, chain, expiries=None):
        self.chain = chain
        self.expiries = expiries
        self.expiry_calls = []
        self.chain_calls = []

    def option_expiries(self, security_id, segment):
        self.expiry_calls.append((security_id, segment))
        return self.expiries or [(datetime.now(IST).date() + timedelta(days=7)).isoformat()]

    def option_chain(self, security_id, expiry, segment):
        self.chain_calls.append((security_id, expiry, segment))
        return self.chain


def test_stock_option_chain_uses_equity_underlying_and_selects_call(monkeypatch):
    monkeypatch.setattr("stock_options.time.sleep", lambda _: None)
    client = FakeDhan({"data": {"last_price": 1000, "oc": {
        "1000": {"ce": _leg(), "pe": _leg(-0.55)}
    }}})
    result = scan_stock_option_chain(client, "RELIANCE", 2885, "LONG")
    assert client.expiry_calls[0] == (2885, "NSE_EQ")
    assert client.chain_calls[0][0] == 2885
    assert client.chain_calls[0][2] == "NSE_EQ"
    assert result["best"]["Type"] == "CALL"
    assert result["best"]["Strike"] == 1000.0
    assert result["best"]["BUY (Ask)"] == 102.0
    assert result["best"]["OI"] == 25000
    assert result["best"]["Volume"] == 5000


def test_stock_option_chain_selects_put_for_short_bias(monkeypatch):
    monkeypatch.setattr("stock_options.time.sleep", lambda _: None)
    client = FakeDhan({"data": {"last_price": 1000, "oc": {
        "1000": {"ce": _leg(), "pe": _leg(-0.55)}
    }}})
    result = scan_stock_option_chain(client, "RELIANCE", 2885, "SHORT")
    assert result["best"]["Type"] == "PUT"


def test_stock_option_chain_returns_no_best_when_liquidity_fails(monkeypatch):
    monkeypatch.setattr("stock_options.time.sleep", lambda _: None)
    illiquid = _leg()
    illiquid.update({"top_bid_quantity": 0, "volume": 0})
    client = FakeDhan({"data": {"last_price": 1000, "oc": {
        "1000": {"ce": illiquid, "pe": illiquid}
    }}})
    result = scan_stock_option_chain(client, "RELIANCE", 2885, "LONG")
    assert result["best"] is None



def test_btst_skips_expiry_today(monkeypatch):
    monkeypatch.setattr("stock_options.time.sleep", lambda _: None)
    today = datetime.now(IST).date()
    client = FakeDhan(
        {"data": {"last_price": 1000, "oc": {"1000": {"ce": _leg(), "pe": _leg(-0.55)}}}},
        expiries=[today.isoformat(), (today + timedelta(days=1)).isoformat(), (today + timedelta(days=7)).isoformat()],
    )
    result = scan_stock_option_chain(client, "RELIANCE", 2885, "LONG", strategy="BTST")
    assert result["expiry"] == (today + timedelta(days=7)).isoformat()
    assert client.chain_calls[0][1] == result["expiry"]


def test_swing_skips_expiry_inside_three_session_buffer(monkeypatch):
    monkeypatch.setattr("stock_options.time.sleep", lambda _: None)
    today = datetime.now(IST).date()
    client = FakeDhan(
        {"data": {"last_price": 1000, "oc": {"1000": {"ce": _leg(), "pe": _leg(-0.55)}}}},
        expiries=[
            (today + timedelta(days=3)).isoformat(),
            (today + timedelta(days=7)).isoformat(),
        ],
    )
    result = scan_stock_option_chain(client, "RELIANCE", 2885, "LONG", strategy="SWING")
    assert result["expiry"] == (today + timedelta(days=7)).isoformat()


def test_swing_refuses_near_expiry_when_no_safe_contract_exists():
    today = datetime.now(IST).date()
    client = FakeDhan(
        {"data": {"last_price": 1000, "oc": {"1000": {"ce": _leg(), "pe": _leg(-0.55)}}}},
        expiries=[(today + timedelta(days=2)).isoformat()],
    )
    try:
        scan_stock_option_chain(client, "RELIANCE", 2885, "LONG", strategy="SWING")
        assert False, "expected no safe expiry"
    except ValueError as exc:
        assert "no stock-option expiry remains" in str(exc)
