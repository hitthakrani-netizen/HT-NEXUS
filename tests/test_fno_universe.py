import pandas as pd
import pytest

import fno_universe
from fno_universe import UniverseError, load_nse_stock_fno_universe


def _cash(symbol, security_id, series="EQ", exchange="NSE"):
    return {
        "EXCH_ID": exchange, "SEGMENT": "E", "SECURITY_ID": security_id,
        "INSTRUMENT": "EQUITY", "UNDERLYING_SECURITY_ID": "",
        "UNDERLYING_SYMBOL": "", "SYMBOL_NAME": symbol, "SERIES": series,
    }


def _option(symbol, underlying_id, security_id, instrument="OPTSTK", exchange="NSE"):
    return {
        "EXCH_ID": exchange, "SEGMENT": "D", "SECURITY_ID": security_id,
        "INSTRUMENT": instrument, "UNDERLYING_SECURITY_ID": underlying_id,
        "UNDERLYING_SYMBOL": symbol, "SYMBOL_NAME": f"{symbol}-OPTION", "SERIES": "",
    }


def _mock_master(monkeypatch, rows):
    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")

    class Response:
        content = csv_bytes

        @staticmethod
        def raise_for_status():
            return None

    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    monkeypatch.setattr(fno_universe, "_retrying_session", lambda: Session())


def test_universe_joins_all_nse_stock_options_to_cash_equity_ids(monkeypatch):
    _mock_master(monkeypatch, [
        _cash("RELIANCE", "2885"),
        _cash("TCS", "11536"),
        _option("RELIANCE", "2885", "70001"),
        _option("RELIANCE", "2885", "70002"),
        _option("TCS", "11536", "70003"),
        _option("NIFTY", "13", "70004", instrument="OPTIDX"),
        _option("RELIANCE", "2885", "80001", exchange="BSE"),
    ])
    universe = load_nse_stock_fno_universe()
    assert [row["symbol"] for row in universe] == ["RELIANCE", "TCS"]
    by_symbol = {row["symbol"]: row for row in universe}
    assert by_symbol["RELIANCE"]["security_id"] == "2885"
    assert by_symbol["RELIANCE"]["fno_security_id"] == "2885"
    assert by_symbol["RELIANCE"]["option_contract_count"] == 2
    assert by_symbol["TCS"]["security_id"] == "11536"
    assert by_symbol["TCS"]["option_contract_count"] == 1


def test_universe_keeps_only_eq_cash_series_when_series_is_available(monkeypatch):
    _mock_master(monkeypatch, [
        _cash("RELIANCE", "2885", series="EQ"),
        _cash("RELIANCE", "9999", series="BE"),
        _option("RELIANCE", "2885", "70001"),
        _option("RELIANCE", "9999", "70002"),
    ])
    universe = load_nse_stock_fno_universe()
    assert len(universe) == 1
    assert universe[0]["security_id"] == "2885"


def test_universe_raises_when_no_nse_stock_options_exist(monkeypatch):
    _mock_master(monkeypatch, [
        _cash("RELIANCE", "2885"),
        _option("NIFTY", "13", "70004", instrument="OPTIDX"),
    ])
    with pytest.raises(UniverseError, match="zero NSE OPTSTK"):
        load_nse_stock_fno_universe()


def test_universe_raises_when_option_underlying_cannot_map_to_nse_eq(monkeypatch):
    _mock_master(monkeypatch, [
        _cash("RELIANCE", "2885"),
        _option("UNKNOWN", "99999", "70001"),
    ])
    with pytest.raises(UniverseError, match="none mapped to NSE EQ cash stocks"):
        load_nse_stock_fno_universe()


def test_universe_raises_when_master_schema_changes(monkeypatch):
    csv_bytes = pd.DataFrame([{"EXCH_ID": "NSE", "SECURITY_ID": "2885"}]).to_csv(index=False).encode()

    class Response:
        content = csv_bytes

        @staticmethod
        def raise_for_status():
            return None

    class Session:
        @staticmethod
        def get(*args, **kwargs):
            return Response()

    monkeypatch.setattr(fno_universe, "_retrying_session", lambda: Session())
    with pytest.raises(UniverseError, match="schema changed"):
        load_nse_stock_fno_universe()
