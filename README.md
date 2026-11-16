# HT NEXUS

Read-only DhanHQ v2 NSE stock scanner with exactly two strategies:
- **BTST:** one button press between 15:15–15:29 IST scans each F&O stock once, internally checks 15:00–15:14 shortlist evidence and fresh 15:15+ confirmation, then automatically validates the matching stock CALL/PUT option chain. Incomplete 15-minute resample candles are excluded from confirmation.
- **SWING:** intended 1–3 trading-session holding window.

The app is a research/paper-trading scaffold. It does **not** place, modify, or cancel orders.

## Files
- `app.py` — responsive Streamlit dashboard, scan controls, one-best-signal view, backtest and paper journal.
- `market_data.py` — DhanHQ v2 read-only REST adapter for quotes and historical candles.
- `indicators.py` — EMA, RSI, MACD, VWAP, ATR, volume ratio and breakout levels.
- `strategy_engine.py` — stock-only BTST/SWING confirmation engine, optional market context, multi-timeframe and false-breakout checks, risk levels.
- `backtest.py` — basic OHLC backtest with conservative stop-first same-bar handling.
- `README.md` — setup and validation.

## Requirements
Python 3.10+ recommended.

```bash
pip install streamlit pandas numpy requests
```

## Credentials — never commit tokens
Set environment variables locally:

**macOS/Linux**
```bash
export DHAN_CLIENT_ID="your_client_id"
export DHAN_ACCESS_TOKEN="your_access_token"
streamlit run app.py
```

**Windows PowerShell**
```powershell
$env:DHAN_CLIENT_ID="your_client_id"
$env:DHAN_ACCESS_TOKEN="your_access_token"
streamlit run app.py
```

For Streamlit hosting, use the platform's Secrets Manager and set:
```toml
DHAN_CLIENT_ID = "..."
DHAN_ACCESS_TOKEN = "..."
```
Do not paste credentials into source code, screenshots, GitHub, or chat. Add `.streamlit/secrets.toml` to `.gitignore` if you create it.

## NIFTY + stock confirmation

NIFTY/index data may be displayed as optional market context and relative-strength reference only. It is never a mandatory filter, never replaces a stock candidate, and never creates a separate index trade signal. Stock BTST/SWING eligibility is decided from the stock's own candles and confirmations. Confirm any configured index instrument ID against the current Dhan instrument master.

## Universe and instrument IDs
The sidebar accepts one row per stock:
```text
SYMBOL,SECURITY_ID,SECTOR
RELIANCE,2885,ENERGY
TCS,11536,IT
```
The sample list is only a starting point. Verify every security ID, exchange segment, instrument type and index ID against the current official Dhan instrument/security master before use. The dashboard accepts sector index proxy IDs in its sidebar, calculates 20-session NIFTY and sector returns, and compares each stock's 20-session return with its configured sector proxy. Sector proxies must be verified against Dhan's current instrument master; unconfigured sectors are not sector-filtered. Do not assume the example NIFTY ID without checking the current instrument master.

## Dhan API notes
This project uses DhanHQ API v2:
- `POST /marketfeed/ltp`, `/marketfeed/ohlc`, `/marketfeed/quote`
- `POST /charts/intraday`
- `POST /charts/historical`

Data API access/subscription and token permissions are separate from merely having a Dhan trading account. Confirm the current plan, access token validity, API rate limits and instrument master in Dhan's official portal/docs. Historical intraday requests are split into windows of at most 89 days by this client to stay within documented request-window limits.

## Strategy logic (initial research rules, not validated edge)
Long/short candidates require:
- EMA20/EMA50 alignment; daily EMA20/EMA50 alignment.
- RSI and MACD direction confirmation.
- Price relative to session VWAP.
- 20-bar breakout/breakdown with volume at least 1.5x 20-bar average.
- Resampled 15-minute confirmation.
- False-breakout guard: close beyond prior range and limited wick retracement.
- ATR-based stop at 1.5 ATR and target at 2.5 ATR (nominal 1:1.67 before costs); backtest default risk is 0.5% of equity per trade.
- NIFTY regime is context only and is not a mandatory stock-signal filter. BTST uses stock-level closing quality, not NIFTY direction.
- Only the highest-scoring eligible candidate is displayed; otherwise NO TRADE.

These are transparent starting parameters, not an assertion of accuracy. Backtest and tune only on training data; preserve a later untouched out-of-sample period.

## Backtesting limitations — read before relying on results
- The included backtest is a baseline engine, not a production-grade event simulator.
- Intraday lookback from the UI is limited to 90 calendar days per fetch; the scanner/backtest does not magically create long history.
- The backtest requires at least 210 daily candles and 250 intraday bars; it uses only data available before the signal date for the market-regime calculation.
- Same-bar stop and target touches are resolved conservatively as stop first.
- Slippage and brokerage are simplified configurable assumptions; taxes, exchange charges, STT, GST, stamp duty, bid/ask spread, liquidity/impact, circuit limits, corporate actions, survivorship bias, delisted stocks and rejected fills are not fully modeled.
- BTST backtests require the same stock and direction to qualify in both the 15:00–15:14 shortlist and the 15:15–15:29 confirmation windows. Opening gaps are filled at the observed session open when that price crosses the stop or target; remaining BTST positions exit at the next-session close. This is still an OHLC approximation.
- Multi-timeframe resampling and session boundaries should be reviewed against exchange trading calendars and candle timestamps.
- Do not compare parameter sets on the same data used to choose them. Use walk-forward and out-of-sample validation, then paper trade.

## Run
```bash
streamlit run app.py
```
Open the displayed URL on desktop or phone. For phone access on the same Wi-Fi, run Streamlit with `--server.address 0.0.0.0` and visit the computer's LAN IP and Streamlit port.

## Security / Git
Recommended `.gitignore`:
```gitignore
.streamlit/secrets.toml
.env
__pycache__/
*.pyc
*.csv
```
Never commit API credentials. Rotate the token immediately if exposed.

## Important
This software is for research/education only, not investment advice. No return, win rate, or accuracy is guaranteed. Check current Dhan API documentation before deployment because endpoints, permissions, pricing and limits can change.


## Free TradingView plan and free scanner operation

The scanner does not require Render or a paid TradingView webhook plan. It uses DhanHQ market data directly from the Streamlit Community Cloud app. TradingView Free alerts are not automatically delivered to this app; no unsupported scraping, account-password sharing, or unofficial TradingView API is used.

The app scans the full configured NSE stock F&O universe when the user presses **ONE-CLICK SCAN — STOCK + CALL / PUT**. BTST is available from 3:15–3:29 PM IST: one pass checks each stock's 3:00–3:14 PM shortlist evidence and fresh 3:15+ confirmation using the same candles, then fetches the matching stock CALL (LONG) or PUT (SHORT) option chain automatically. No separate CE/PE scan is required. NIFTY is not used as a stock filter or trade signal. SWING can be scanned outside the BTST window. The app is read-only and does not place orders.

NIFTY 50 default Dhan ID 13 in segment IDX_I is supported by Dhan instrument references; verify the ID if Dhan returns a data error. Setup scores are technical confluence scores, not probabilities or guarantees. Backtest and paper-test before considering any live trade.


## Automatic Stock Options CE/PE

The single stock scan automatically requests the selected stock's active F&O expiry and option chain from DhanHQ using the NSE_FNO underlying segment. A LONG stock signal maps to CE/CALL; a SHORT stock signal maps to PE/PUT. The app filters for live bid/ask depth, volume, open interest, spread and delta, then displays indicative buy (ask), premium stop, Target 1 and Target 2 in the same scan result. There is no separate stock selector or CE/PE scan button. If the stock has no active F&O contracts or no liquid matching contract passes, it displays no option trade rather than forcing a recommendation.

The dashboard includes a mobile-responsive visual layout. On Android Chrome, use the browser menu and choose **Add to Home screen** (or **Install app**, if offered) to create a home-screen shortcut. This remains a Streamlit web app, not a native APK. Option-chain values and formula-based targets are indicative, not guaranteed fills or tested profit forecasts. No orders are placed.


## Automated code validation
The pull request includes GitHub Actions checks for Python syntax and the repository's existing unit tests. A pre-existing literal-newline syntax error in `webhook_receiver/main.py` was corrected so the full-repository syntax check can cover that module as well.
