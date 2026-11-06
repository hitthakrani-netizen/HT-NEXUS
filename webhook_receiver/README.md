# HT NEXUS TradingView Webhook Receiver

This service receives TradingView JSON alerts over HTTPS and stores them in PostgreSQL.
It does not place orders and does not treat an indicator alert as a confirmed trade.

## Deploy on Render

1. Create a Render account and connect the GitHub account that can access this repository.
2. Create a PostgreSQL database with durable storage. Copy its **Internal Database URL** if the web service is in the same Render region; otherwise use the External URL.
3. In Render, create a Blueprint from this repository and select the root `render.yaml`, or create a Web Service manually using the commands in `render.yaml`.
4. Add these environment variables to the web service:
   - `DATABASE_URL`: the PostgreSQL connection URL
   - `TRADINGVIEW_WEBHOOK_SECRET`: a long random secret (keep it private)
5. Deploy. Confirm `/health` returns healthy.

## TradingView alert message

After the exact alert formats from the three indicators are confirmed, use a JSON message containing the shared secret and the indicator fields. Example format only:

```json
{
  "secret": "YOUR_PRIVATE_SECRET",
  "source": "indicator-name",
  "symbol": "{{ticker}}",
  "signal": "BUY",
  "timestamp": "{{time}}"
}
```

Webhook URL: `https://YOUR-RENDER-SERVICE.onrender.com/webhook/tradingview`

Do not put Dhan credentials in TradingView alerts. Do not share the webhook secret in screenshots or chat.

## Endpoints

- `GET /`: service status
- `GET /health`: database health check
- `POST /webhook/tradingview`: authenticated JSON alert receiver
- `GET /alerts?limit=50`: latest stored alerts (protect this endpoint before exposing it for dashboard use)

## Important

Render Free web services sleep after 15 minutes without inbound traffic and their local filesystem is ephemeral. For live-market alert reliability, use an always-on paid web service and a durable PostgreSQL database. A free Render PostgreSQL database expires after 30 days, so it is only suitable for temporary testing.
