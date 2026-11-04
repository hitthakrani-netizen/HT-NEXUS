"""
HT NEXUS PRO - Dhan API market-data client
Signal-only connection. This module contains no order-placement functions.
Requires dhanhq==2.3.0 and config.py.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from config import load_dhan_settings

logger = logging.getLogger("ht_nexus_pro.dhan")


class DhanConfigurationError(RuntimeError):
    """Dhan credentials or SDK configuration is missing."""


class DhanConnectionError(RuntimeError):
    """The Dhan market-data connection could not be started."""


@dataclass
class FeedStatus:
    connected: bool = False
    last_message_at: Optional[float] = None
    messages_received: int = 0
    last_error: Optional[str] = None

    @property
    def data_age_seconds(self) -> Optional[float]:
        if self.last_message_at is None:
            return None
        return max(0.0, time.time() - self.last_message_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "messages_received": self.messages_received,
            "data_age_seconds": self.data_age_seconds,
            "last_error": self.last_error,
        }


class DhanFeedClient:
    """
    Wrapper around Dhan's official Python SDK MarketFeed.

    Instrument tuples use:
        (exchange_segment, security_id, subscription_type)

    Example after constructing the client:
        feed.sdk.NSE, "1333", feed.sdk.Ticker

    This client receives market data only. It never places, modifies,
    cancels, or executes orders.
    """

    def __init__(self) -> None:
        settings = load_dhan_settings()
        if not settings.credentials_available:
            raise DhanConfigurationError(
                "Dhan credentials are missing. Add DHAN_CLIENT_ID and "
                "DHAN_ACCESS_TOKEN in Streamlit Secrets or environment variables."
            )

        try:
            from dhanhq import DhanContext, MarketFeed
        except ImportError as exc:
            raise DhanConfigurationError(
                "Dhan SDK is not installed. Install requirements.txt first."
            ) from exc

        self.sdk = MarketFeed
        self._context = DhanContext(settings.client_id, settings.access_token)
        self._feed: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_packet: Optional[Any] = None
        self._packet_lock = threading.Lock()
        self.status = FeedStatus()

    @staticmethod
    def validate_instruments(instruments: Iterable[tuple]) -> list[tuple]:
        clean: list[tuple] = []
        for item in instruments:
            if not isinstance(item, (tuple, list)) or len(item) != 3:
                raise ValueError(
                    "Each instrument must be "
                    "(exchange_segment, security_id, subscription_type)."
                )
            segment, security_id, subscription_type = item
            if not str(segment).strip() or not str(security_id).strip():
                raise ValueError("Exchange segment and security ID are required.")
            clean.append((segment, str(security_id), subscription_type))

        if not clean:
            raise ValueError("At least one instrument is required.")
        if len(clean) > 5000:
            raise ValueError("Maximum 5000 instruments are allowed per connection.")
        return clean

    def start(self, instruments: Iterable[tuple], version: str = "v2") -> None:
        """Initialize and start the SDK feed on a background thread."""
        if self._thread and self._thread.is_alive():
            raise DhanConnectionError("Market feed is already running.")

        clean = self.validate_instruments(instruments)
        try:
            self._feed = self.sdk(self._context, clean, version)
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._receive_loop,
                name="ht-nexus-dhan-feed",
                daemon=True,
            )
            self._thread.start()
        except Exception as exc:
            self.status.connected = False
            self.status.last_error = str(exc)
            logger.exception("Unable to initialize Dhan market feed")
            raise DhanConnectionError(str(exc)) from exc

    def _receive_loop(self) -> None:
        try:
            self._feed.run_forever()
            self.status.connected = True

            while not self._stop_event.is_set():
                packet = self._feed.get_data()
                if packet is not None:
                    with self._packet_lock:
                        self._latest_packet = packet
                    self.status.last_message_at = time.time()
                    self.status.messages_received += 1
                    self.status.connected = True
                    self.status.last_error = None
        except Exception as exc:
            self.status.last_error = str(exc)
            self.status.connected = False
            logger.exception("Dhan market feed receive loop failed")
        finally:
            self.status.connected = False

    def get_latest_packet(self) -> Optional[Any]:
        """Return the latest packet received from the SDK."""
        with self._packet_lock:
            return self._latest_packet

    def get_status(self) -> dict[str, Any]:
        """Return connection health and last-data age."""
        return self.status.as_dict()

    def close(self) -> None:
        """Stop and close the market-data WebSocket."""
        self._stop_event.set()
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception as exc:
                logger.warning("Dhan feed close warning: %s", exc)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

        self.status.connected = False

    def __enter__(self) -> "DhanFeedClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
