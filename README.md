# HT NEXUS

Read-only DhanHQ v2 NSE stock scanner with exactly two strategies:
- **BTST:** one button press between 15:16–15:29 IST scans each F&O stock once, internally checks 15:00–15:14 shortlist evidence and the completed 15:15+ confirmation candle, then automatically validates the matching stock CALL/PUT option chain. Incomplete base and 15-minute candles are excluded from confirmation.
- **SWING:** intended 1–3 trading-session holding window.

The app is a research/paper-trading scaffold. It does **not** place, modify, or cancel orders.

## Files
- `app.py` — responsive Streamlit dashboard, scan controls, one-best-signal view, backtest and paper journal.
- `market_data.py` — DhanHQ v2 read-only REST adapter for quotes and historical candles.
- `indicators.py` — EMA, RSI, MACD, VWAP, ATR, volume ratio and breakout levels.
- `strategy_engine.py` — stock-only BTST/SWING confirmation engine, multi-timeframe and false-breakout checks, risk levels.
- `nse_market_data.py` / `nse_confirmation.py` — best-effort NSE public stock snapshots and conservative stock-level corroboration.
- `stock_options.py` — Dhan stock option-chain liquidity checks and indicative premium risk levels.
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

## Stock-only scope and NSE confirmation

The stock scanner has exactly two strategies: BTST and SWING. NIFTY/index data is not fetched in the one-click stock scan, is not a required filter, and cannot create a separate trade signal.

During **ONE-CLICK SCAN — STOCK + CALL / PUT**, the app fetches three NSE public website stock snapshots: Most Active Underlying, Volume Gainers and OI Spurts. A stock candidate must match at least two independent NSE feeds, and every usable NSE price-change field must agree with the Dhan candle direction. If an OI-spurt row is present, non-positive OI change rejects the candidate. Positive OI change means new positioning; it does not by itself reveal whether buyers or sellers initiated it.

Dhan intraday/daily candles remain the primary price-action engine. Dhan's stock option chain supplies the actual CALL/PUT strike, expiry, LTP, bid/ask, spread, OI and volume. NSE public website snapshots are best-effort corroboration only: they can be delayed, rate-limited, revised or unavailable, and are **not** a licensed real-time feed. The app does not issue a final trade when fewer than two NSE feeds are usable or when the stock-level NSE confirmation fails. For guaranteed exchange-grade real-time NSE data, a separately licensed NSE Data & Analytics feed is required.

## NSE stock universe and instrument IDs

The app builds the eligible stock universe from Dhan's current detailed instrument master. It includes NSE cash-equity underlyings that have NSE OPTSTK contracts; index options and non-stock derivatives are excluded. The app refuses to fall back to a guessed or partial hard-coded list if the master cannot be downloaded or mapped. The cash-equity security ID from the master is used for Dhan candle and stock-option-chain requests.

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
- NSE public Most Active Underlying, Volume Gainers and OI Spurts must provide at least two independent stock-level confirmations with price direction aligned to the Dhan stock signal.
- NIFTY/index regime is not fetched or required in the stock scan.
- Only the highest-ranked stock candidate is displayed; a final trade additionally requires a liquid matching Dhan CALL/PUT contract. Otherwise the app says NO TRADE.

These are transparent starting parameters, not an assertion of accuracy. Backtest and tune only on training data; preserve a later untouched out-of-sample period.

## Backtesting limitations — read before relying on results
- The included backtest is a baseline engine, not a production-grade event simulator.
- Intraday lookback from the UI is limited to 90 calendar days per fetch; the scanner/backtest does not magically create long history.
- The backtest requires at least 210 daily candles and 250 intraday bars; it uses only data available before the signal date for the market-regime calculation.
- Same-bar stop and target touches are resolved conservatively as stop first.
- Slippage and brokerage are simplified configurable assumptions; taxes, exchange charges, STT, GST, stamp duty, bid/ask spread, liquidity/impact, circuit limits, corporate actions, survivorship bias, delisted stocks and rejected fills are not fully modeled.
- BTST backtests require the same stock and direction to qualify in both the 15:00–15:14 shortlist and the 15:15–15:29 confirmation windows. Opening gaps are filled at the observed session open when that price crosses the stop or target; remaining BTST positions exit at the next-session close. This is still an OHLC approximation.
- Multi-timeframe resampling and session boundaries should be reviewed against exchange trading calendars and candle timestamps.
- The historical OHLC backtest does not replay historical NSE website snapshots or historical Dhan option chains. It therefore tests the stock-candle rules, not the complete live NSE-confirmation + option-selection pipeline. Validate those layers separately with timestamped paper-trade logs.
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

The app scans the NSE stock F&O universe from Dhan's current instrument master when the user presses **ONE-CLICK SCAN — STOCK + CALL / PUT**. Before scoring candidates, it fetches NSE Most Active Underlying, Volume Gainers and OI Spurts snapshots; each final stock candidate must be corroborated by at least two feeds with price direction aligned to the Dhan candles. If NSE public data is unavailable or contradictory, the app withholds a final trade instead of silently falling back to Dhan-only signals. The NSE website does not always expose a source-update timestamp in these snapshots, so the app shows fetch time and explicitly labels the source as best-effort rather than claiming verified tick-level freshness. These NSE website endpoints are best-effort snapshots, not a licensed real-time feed. BTST is available from 3:16–3:29 PM IST so the 3:15 one-minute candle has time to close: one pass checks each stock's 3:00–3:14 PM shortlist evidence and completed 3:15+ confirmation, then automatically fetches the matching stock CALL (LONG) or PUT (SHORT) option chain. No separate CE/PE scan is required. Four parallel workers overlap network latency, a process-wide throttle stays below Dhan's 5 historical-data requests/second limit, and completed daily candles are cached for 12 hours. The app records total scan duration. NIFTY is not used as a stock filter or trade signal. SWING can be scanned outside the BTST window. The app is read-only and does not place orders.

Setup scores are technical confluence scores, not probabilities or guarantees. Backtest and paper-test before considering any live trade.


## Automatic Stock Options CE/PE

The single stock scan automatically requests the selected stock's active F&O expiry and option chain from DhanHQ using the NSE_EQ cash-equity underlying segment and mapped cash-equity security ID, observing the documented 3-second option-chain request interval. A LONG stock signal maps to CE/CALL; a SHORT stock signal maps to PE/PUT. Same-day and next-session expiry are excluded for BTST, and Swing requires at least six calendar days to expiry to cover its maximum three-session holding window. The app filters for live bid/ask depth, volume, open interest, spread and delta, then displays indicative buy (ask), premium stop, Target 1 and Target 2 in the same scan result. There is no separate stock selector or CE/PE scan button. If the stock has no active F&O contracts or no liquid matching contract passes, it displays no option trade rather than forcing a recommendation.

The dashboard includes a mobile-responsive visual layout. On Android Chrome, use the browser menu and choose **Add to Home screen** (or **Install app**, if offered) to create a home-screen shortcut. This remains a Streamlit web app, not a native APK. Option-chain values and formula-based targets are indicative, not guaranteed fills or tested profit forecasts. No orders are placed.


## Automated code validation
The pull request includes GitHub Actions checks for Python syntax and the repository's existing unit tests. A pre-existing literal-newline syntax error in `webhook_receiver/main.py` was corrected so the full-repository syntax check can cover that module as well.
