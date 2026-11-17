from nse_market_data import BASE, NSEDataError, NSEMarketData


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if "/market-data/most-active-contracts" in url:
            return FakeResponse({"page": "primed"})
        if url.endswith("/api/live-analysis-most-active-underlyings"):
            return FakeResponse({"data": [{"symbol": "RELIANCE", "pChange": 1.2}]})
        if url.endswith("/api/live-analysis-oi-spurts"):
            return FakeResponse({"data": [{"symbol": "RELIANCE", "changeInOI": 12000}]})
        return FakeResponse({"data": []})


def test_nse_client_uses_current_canonical_host_and_parses_stock_rows():
    session = FakeSession()
    client = NSEMarketData(session=session)
    active = client.most_active_underlying()
    oi = client.oi_spurts()
    assert BASE == "https://www.nseindia.in"
    assert active.iloc[0]["symbol"] == "RELIANCE"
    assert oi.iloc[0]["changeInOI"] == 12000
    assert len([call for call in session.calls if "/market-data/" in call[0]]) == 1


def test_nse_client_rejects_empty_stock_feed():
    class EmptySession(FakeSession):
        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params, timeout))
            if "/market-data/" in url:
                return FakeResponse({"page": "primed"})
            return FakeResponse({"data": []})

    client = NSEMarketData(session=EmptySession())
    try:
        client.volume_gainers()
        assert False, "expected NSEDataError"
    except NSEDataError:
        pass
