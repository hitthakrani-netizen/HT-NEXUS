import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="HT NEXUS TradingView Webhook Receiver", version="1.0.0")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
WEBHOOK_SECRET = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()


def db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL, connect_timeout=5)


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tradingview_alerts (
                id BIGSERIAL PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                source TEXT,
                symbol TEXT,
                signal TEXT,
                event_time TEXT,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                raw_payload JSONB NOT NULL
            )
        """)


@app.on_event("startup")
def startup():
    if DATABASE_URL:
        init_db()


@app.get("/")
def root():
    return {"service": "HT NEXUS webhook receiver", "status": "ok"}


@app.get("/health")
def health():
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database is not configured")
    try:
        with db_connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "healthy", "database": "connected"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


def first_value(payload: dict[str, Any], keys: tuple[str, ...]):
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


@app.post("/webhook/tradingview")
async def tradingview_webhook(request: Request):
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected a JSON alert message")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object required")

    supplied_secret = str(payload.pop("secret", ""))
    # Compare secrets without a timing-sensitive normal string comparison.
    import hmac
    if not hmac.compare_digest(supplied_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    source = first_value(payload, ("source", "indicator", "strategy", "alert_name"))
    symbol = first_value(payload, ("symbol", "ticker", "s"))
    signal = first_value(payload, ("signal", "side", "action", "alert"))
    event_time = first_value(payload, ("timestamp", "time", "t"))

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    dedupe_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database is not configured")

    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                INSERT INTO tradingview_alerts
                    (dedupe_key, source, symbol, signal, event_time, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING id
                """,
                (dedupe_key, source, symbol, signal, event_time, canonical),
            ).fetchone()
        return JSONResponse({
            "accepted": True,
            "duplicate": row is None,
            "alert_id": row[0] if row else None,
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        raise HTTPException(status_code=503, detail="Alert could not be stored; retry later")


@app.get("/alerts")
def list_alerts(limit: int = 50):
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Database is not configured")
    limit = max(1, min(limit, 200))
    try:
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source, symbol, signal, event_time, received_at, raw_payload
                FROM tradingview_alerts
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0], "source": r[1], "symbol": r[2], "signal": r[3],
                "event_time": r[4], "received_at": r[5].isoformat(),
                "payload": r[6],
            }
            for r in rows
        ]
    except Exception:
        raise HTTPException(status_code=503, detail="Alerts could not be read")
