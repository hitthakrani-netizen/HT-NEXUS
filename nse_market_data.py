"""NSE public market-data reader for HT NEXUS.

Read-only integration. NSE web endpoints are public website endpoints, not a
contractual real-time feed; they may change, throttle, or be unavailable.
Never treat a missing/blocked response as zero or as a trading signal.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
import pandas as pd
import requests

BASE = "https://www.nseindia.com"
ENDPOINTS = {
    "most_active_contracts": "/api/live-analysis-most-active-contracts",
    "most_active_underlying": "/api/live-analysis-most-active-underlyings",
    "oi_spurts": "/api/live-analysis-oi-spurts",
    "volume_gainers": "/api/live-analysis-volume-gainers",
    "fii_dii": "/api/fiidiiTradeReact",
}

class NSEDataError(RuntimeError):
    pass


class NSEMarketData:
    """Best-effort reader for NSE's own public market-data website endpoints."""

    def __init__(self, timeout: int = 15, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE + "/market-data/most-active-contracts",
            "Connection": "keep-alive",
        })
        self._primed = False

    def _prime(self) -> None:
        if self._primed:
            return
        try:
            response = self.session.get(BASE + "/market-data/most-active-contracts", timeout=self.timeout)
            response.raise_for_status()
            self._primed = True
        except requests.RequestException as exc:
            raise NSEDataError(f"NSE website session could not be initialized: {exc}") from exc

    def _get(self, key: str, params: dict | None = None) -> Any:
        if key not in ENDPOINTS:
            raise ValueError("Unknown NSE data endpoint.")
        self._prime()
        try:
            response = self.session.get(BASE + ENDPOINTS[key], params=params, timeout=self.timeout)
            if response.status_code in (401, 403, 429):
                raise NSEDataError(f"NSE temporarily blocked or rate-limited this request (HTTP {response.status_code}).")
            response.raise_for_status()
            body = response.json()
        except NSEDataError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise NSEDataError(f"NSE {key} data unavailable: {exc}") from exc
        if not isinstance(body, (dict, list)):
            raise NSEDataError(f"NSE {key} returned an unexpected response.")
        return body

    @staticmethod
    def _records(body: Any) -> list[dict]:
        if isinstance(body, list):
            return [x for x in body if isinstance(x, dict)]
        if isinstance(body, dict):
            # NSE responses have used different wrapper keys across pages.
            for value in body.values():
                if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                    return value
            for key in ("data", "records", "rows", "result"):
                value = body.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    def most_active_contracts(self, symbol: str | None = None) -> pd.DataFrame:
        body = self._get("most_active_contracts")
        rows = self._records(body)
        df = pd.DataFrame(rows)
        if df.empty:
            raise NSEDataError("NSE Most Active Contracts returned no rows.")
        if symbol and "symbol" in df.columns:
            df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
        return df

    def most_active_underlying(self) -> pd.DataFrame:
        body = self._get("most_active_underlying")
        df = pd.DataFrame(self._records(body))
        if df.empty:
            raise NSEDataError("NSE Most Active Underlying returned no rows.")
        return df

    def oi_spurts(self) -> pd.DataFrame:
        body = self._get("oi_spurts")
        df = pd.DataFrame(self._records(body))
        if df.empty:
            raise NSEDataError("NSE OI Spurts returned no rows.")
        return df

    def volume_gainers(self) -> pd.DataFrame:
        body = self._get("volume_gainers")
        df = pd.DataFrame(self._records(body))
        if df.empty:
            raise NSEDataError("NSE Volume Gainers returned no rows.")
        return df

    def fii_dii(self) -> pd.DataFrame:
        body = self._get("fii_dii")
        df = pd.DataFrame(self._records(body))
        if df.empty:
            raise NSEDataError("NSE FII/DII data returned no rows.")
        return df
