"""Read-only DhanHQ API v2 market-data adapter for HT NEXUS PRO.

No order/trading endpoints are implemented in this module.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

import pandas as pd
import requests

BASE_URL = "https://api.dhan.co/v2"
IST = "Asia/Kolkata"
VALID_INTRADAY_INTERVALS = {"1", "5", "15", "25", "60"}


class DhanAPIError(RuntimeError):
    """Raised when Dhan returns an API or transport error."""


def _credentials_from_sources(client_id: Optional[str], access_token: Optional[str]):
    """Explicit args > environment > Streamlit secrets; no secrets are embedded."""
    cid = client_id or os.getenv("DHAN_CLIENT_ID", "")
    token = access_token or os.getenv("DHAN_ACCESS_TOKEN", "")
    if not cid or not token:
        try:
            import streamlit as st  # optional outside Streamlit
            cid = cid or str(st.secrets.get("DHAN_CLIENT_ID", ""))
            token = token or str(st.secrets.get("DHAN_ACCESS_TOKEN", ""))
        except Exception:
            pass
    if not cid or not token:
        raise ValueError(
            "Dhan credentials missing. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN "
            "environment variables or Streamlit secrets."
        )
    return cid, token


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _normalise_candles(payload: dict) -> pd.DataFrame:
    """Convert Dhan's parallel-array chart response into OHLCV DataFrame."""
    if not isinstance(payload, dict):
        raise DhanAPIError("Unexpected candle response: expected a JSON object.")
    if payload.get("errorCode") or payload.get("errorType"):
        raise DhanAPIError(
            f"Dhan historical API error {payload.get('errorCode', '')}: "
            f"{payload.get('errorMessage', payload.get('errorType', 'unknown error'))}"
        )
    required = ("open", "high", "low", "close", "timestamp")
    if not all(k in payload for k in required):
        raise DhanAPIError(f"Historical response missing required fields: {required}")
    n = min(len(payload[k]) for k in required)
    if n == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    data = {k: list(payload[k])[:n] for k in required}
    data["volume"] = list(payload.get("volume", [0] * n))[:n]
    if len(data["volume"]) < n:
        data["volume"] += [0] * (n - len(data["volume"]))
    if "open_interest" in payload:
        data["open_interest"] = list(payload["open_interest"])[:n]
    df = pd.DataFrame(data)
    # Dhan chart timestamps are Unix epoch seconds; convert to exchange-local time.
    df["datetime"] = pd.to_datetime(df.pop("timestamp"), unit="s", utc=True).dt.tz_convert(IST)
    df = df.set_index("datetime").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df[~df.index.duplicated(keep="last")]


def parse_universe(text: str) -> list[dict]:
    """Parse app.py's SYMBOL,SECURITY_ID,SECTOR text-area format."""
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            sid = str(int(parts[1]))
        except (TypeError, ValueError):
            continue
        rows.append({
            "symbol": parts[0].upper(),
            "security_id": sid,
            "sector": parts[2].upper() if len(parts) > 2 and parts[2] else "UNKNOWN",
        })
    return rows


class DhanDataClient:
    """Small synchronous, read-only wrapper around official DhanHQ v2 data endpoints."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: int = 25,
        session: Optional[requests.Session] = None,
        quote_pause: float = 1.05,
    ):
        self.client_id, self.access_token = _credentials_from_sources(client_id, access_token)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.quote_pause = max(0.0, float(quote_pause))
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        })
        self._last_quote_request = 0.0

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DhanAPIError(f"Network error calling {path}: {exc}") from exc
        if response.status_code in (401, 403):
            raise DhanAPIError(
                f"Dhan rejected {path} (HTTP {response.status_code}). Verify Client ID/token "
                "and confirm Data API subscription/permissions are active. Trading API access "
                "alone does not imply Data API access."
            )
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        if not response.ok:
            msg = body.get("errorMessage", body.get("message", body.get("raw", response.text[:300])))
            raise DhanAPIError(f"Dhan HTTP {response.status_code} on {path}: {msg}")
        if isinstance(body, dict) and body.get("status") == "failure":
            raise DhanAPIError(f"Dhan API failure on {path}: {body}")
        if isinstance(body, dict) and (body.get("errorCode") or body.get("errorType")):
            raise DhanAPIError(f"Dhan API error on {path}: {body}")
        return body

    @staticmethod
    def _market_payload(security_ids: Iterable[Any], exchange_segment: str) -> dict:
        ids = [int(x) for x in security_ids]
        if not ids:
            raise ValueError("At least one security ID is required.")
        if len(ids) > 1000:
            raise ValueError("Dhan marketfeed endpoints accept at most 1000 instruments per request.")
        return {exchange_segment: ids}

    def _marketfeed(self, endpoint: str, security_ids, exchange_segment="NSE_EQ") -> dict:
        # Market Quote endpoints have a documented limit of 1 request/second.
        wait = self.quote_pause - (time.monotonic() - self._last_quote_request)
        if wait > 0:
            time.sleep(wait)
        result = self._post(f"/marketfeed/{endpoint}", self._market_payload(security_ids, exchange_segment))
        self._last_quote_request = time.monotonic()
        return result

    def ltp(self, security_ids, exchange_segment="NSE_EQ") -> dict:
        """Return raw LTP marketfeed response for one or more security IDs."""
        return self._marketfeed("ltp", security_ids, exchange_segment)

    def ohlc(self, security_ids, exchange_segment="NSE_EQ") -> dict:
        """Return raw OHLC + LTP marketfeed response."""
        return self._marketfeed("ohlc", security_ids, exchange_segment)

    def quote(self, security_ids, exchange_segment="NSE_EQ") -> dict:
        """Return raw quote/depth response, including volume and market depth."""
        return self._marketfeed("quote", security_ids, exchange_segment)

    def _chart_request(self, path: str, payload: dict) -> pd.DataFrame:
        return _normalise_candles(self._post(path, payload))

    def intraday(
        self, security_id, exchange_segment="NSE_EQ", instrument="EQUITY",
        interval="5", from_datetime=None, to_datetime=None, oi=False,
    ) -> pd.DataFrame:
        """Fetch intraday candles; automatically split requests into <=90-day windows.

        Keeps the positional signature used by the supplied app.py:
        intraday(id, segment, instrument, interval, start_datetime, end_datetime).
        """
        interval = str(interval)
        if interval not in VALID_INTRADAY_INTERVALS:
            raise ValueError(f"Unsupported Dhan interval {interval}; use 1, 5, 15, 25 or 60.")
        end = pd.Timestamp(to_datetime or datetime.now()).to_pydatetime()
        start = pd.Timestamp(from_datetime or (end - timedelta(days=30))).to_pydatetime()
        if start >= end:
            raise ValueError("from_datetime must be earlier than to_datetime.")
        chunks = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=89), end)
            payload = {
                "securityId": str(security_id),
                "exchangeSegment": exchange_segment,
                "instrument": instrument,
                "interval": interval,
                "oi": bool(oi),
                "fromDate": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            }
            chunks.append(self._chart_request("/charts/intraday", payload))
            cursor = chunk_end
        if not chunks:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.concat(chunks).sort_index().loc[lambda x: ~x.index.duplicated(keep="last")]

    def daily(
        self, security_id, exchange_segment="NSE_EQ", instrument="EQUITY",
        from_date=None, to_date=None, oi=False,
    ) -> pd.DataFrame:
        """Fetch daily candles. Dhan's toDate is non-inclusive."""
        end = _as_date(to_date or date.today())
        start = _as_date(from_date or (end - timedelta(days=500)))
        if start >= end:
            raise ValueError("from_date must be earlier than to_date (Dhan toDate is exclusive).")
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "expiryCode": 0,
            "oi": bool(oi),
            "fromDate": start.isoformat(),
            "toDate": end.isoformat(),
        }
        return self._chart_request("/charts/historical", payload)

    # Explicit aliases for callers preferring descriptive method names.
    get_ltp = ltp
    get_ohlc = ohlc
    get_quote = quote
    intraday_historical = intraday
    daily_historical = daily
