
"""
HT NEXUS PRO
Central configuration
BTST + SWING | Signal Only | No Auto Orders
"""

import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo


APP_NAME = "HT NEXUS PRO"
APP_SUBTITLE = "BTST • SWING • OPTIONS INTELLIGENCE"

TIMEZONE = ZoneInfo("Asia/Kolkata")

# NSE market timings (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# BTST scanning window: 3:15 PM to 3:29 PM
BTST_SCAN_START = time(15, 15)
BTST_SCAN_END = time(15, 29)

# Swing holding period
SWING_MIN_HOLD_DAYS = 1
SWING_MAX_HOLD_DAYS = 3

# Scanner safety limits
MAX_SIGNALS = 1
MIN_SIGNAL_SCORE = 80
MAX_DATA_AGE_SECONDS = 10
MAX_SPREAD_PERCENT = 1.50

# Never enable automatic order placement
AUTO_ORDER_ENABLED = False


def _read_secret(key: str, default: str = "") -> str:
    """Read credentials from environment or Streamlit secrets."""

    value = os.getenv(key)

    if value:
        return value.strip()

    try:
        import streamlit as st
        value = st.secrets.get(key, default)
        return str(value).strip()
    except Exception:
        return default


@dataclass(frozen=True)
class DhanSettings:
    client_id: str
    access_token: str

    @property
    def credentials_available(self) -> bool:
        return bool(self.client_id and self.access_token)


def load_dhan_settings() -> DhanSettings:
    return DhanSettings(
        client_id=_read_secret("DHAN_CLIENT_ID"),
        access_token=_read_secret("DHAN_ACCESS_TOKEN"),
    )


def get_app_status() -> dict:
    dhan = load_dhan_settings()

    return {
        "app_name": APP_NAME,
        "credentials_available": dhan.credentials_available,
        "auto_order_enabled": AUTO_ORDER_ENABLED,
        "timezone": "Asia/Kolkata",
        "max_signals": MAX_SIGNALS,
    }
