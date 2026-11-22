"""HT NEXUS — stock-only BTST/SWING scanner. Read-only Dhan data."""
import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo
from market_data import DhanDataClient, generate_access_token, parse_universe
from strategy_engine import evaluate_symbol, diagnose_symbol, validate_btst_confirmation
from nse_confirmation import confirm_stock_with_nse
from backtest import run_backtest
from stock_options import scan_stock_option_chain
from nse_market_data import NSEMarketData
from tradingview_screener import parse_tradingview_csv, confirm_with_tradingview
from fno_universe import load_nse_stock_fno_universe, UniverseError

IST=ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="HT NEXUS", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
# Responsive, app-like presentation for Android/mobile browsers. Streamlit remains a web app.
st.markdown("""
<style>
:root { color-scheme: light dark; }
.block-container { max-width: 1180px; padding-top: 1.15rem; padding-bottom: 2.5rem; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMetric"] {
  border: 1px solid rgba(128,128,128,.22);
  border-radius: 14px;
  padding: 12px 14px;
  background: rgba(128,128,128,.055);
}
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  width: 100%; min-height: 46px; border-radius: 12px; font-weight: 650;
}
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
section[data-testid="stSidebar"] { min-width: 290px; }
@media (max-width: 768px) {
  .block-container { padding: .65rem .8rem 2rem; }
  h1 { font-size: 1.65rem !important; }
  h2 { font-size: 1.3rem !important; }
  h3 { font-size: 1.1rem !important; }
  [data-testid="stMetric"] { padding: 9px 10px; }
  .stButton > button, .stDownloadButton > button { min-height: 50px; }
  [data-testid="stHorizontalBlock"] { gap: .5rem; }
  [data-testid="stSidebar"] { width: min(88vw, 360px); }
}

[data-testid="stHeader"] { background:transparent; }
[data-testid="stAppViewContainer"] { background:linear-gradient(180deg,rgba(17,48,82,.045),transparent 320px); }
[data-testid="stMetric"] { border-radius:16px; box-shadow:0 5px 18px rgba(20,35,60,.055); }
[data-testid="stExpander"] { border-radius:15px; }
.stButton > button { min-height:48px; border-radius:13px; font-weight:750; box-shadow:0 4px 12px rgba(25,65,110,.10); }
@media (max-width:768px) {
.block-container { padding:.55rem .65rem 1.5rem; }
h1 { font-size:1.55rem !important; } h2 { font-size:1.25rem !important; } h3 { font-size:1.08rem !important; }
[data-testid="stMetric"] { padding:9px 10px; }
[data-testid="stMetricValue"] { font-size:1.35rem; }
[data-testid="stHorizontalBlock"] { gap:.45rem; }
.stButton > button { min-height:52px; }
}
</style>
""", unsafe_allow_html=True)
st.markdown("""<div style="padding:22px 18px;border-radius:22px;background:linear-gradient(130deg,#101d38,#18527a,#07858a);color:white;margin:4px 0 18px"><div style="font-size:.72rem;letter-spacing:.16em;font-weight:800">DHAN LIVE DATA • TECHNICAL SCANNER</div><div style="font-size:2rem;font-weight:900;margin-top:5px">⚡ HT NEXUS</div><div style="font-size:.9rem">STOCK BTST · SWING · LIVE MARKET DATA</div><div style="margin-top:12px;font-size:.75rem;font-weight:750">LIVE DATA　•　TWO TARGETS + SL　•　READ-ONLY SIGNALS</div></div>""",unsafe_allow_html=True)

def _secret_or_env(key: str) -> str:
    """Read optional Streamlit secret safely; fall back to environment."""
    try:
        value = st.secrets.get(key, "")
    except Exception:
        value = ""
    return str(value or os.getenv(key, ""))

with st.sidebar:
    st.subheader("Configuration")
    client_id=st.text_input("Dhan Client ID", value=_secret_or_env("DHAN_CLIENT_ID"))
    auto_auth = bool(_secret_or_env("DHAN_PIN") and _secret_or_env("DHAN_TOTP_SECRET"))
    if auto_auth:
        st.success("Automatic Dhan login is configured (TOTP).")
        token = ""
    else:
        token=st.text_input("Dhan Access Token", value=_secret_or_env("DHAN_ACCESS_TOKEN"), type="password")
        st.info("For daily automatic login, configure DHAN_PIN and DHAN_TOTP_SECRET once in Streamlit Secrets.")
    strategy=st.selectbox("Strategy",["BTST","SWING"])
    interval=st.selectbox("Intraday candle interval (minutes)",["1","5","15"],index=0)
    lookback=st.slider("Intraday history (days)",20,90,20)
    daily_lookback=st.slider("Daily history (calendar days)",250,1500,500,step=50)
    capital=st.number_input("Backtest capital (₹)",min_value=10000,value=100000,step=10000)
    risk=st.slider("Risk per trade (%)",0.25,1.0,0.5,step=0.25)/100
    st.markdown("**NSE Stock F&O Universe**")
    st.caption("Dhanના current instrument master માંથી બધા NSE OPTSTK underlyings આપમેળે load થશે. Manual 10-stock limit નથી.")


def _dhan_client():
    """Reuse a session token and automatically generate a new one near expiry."""
    cid = str(client_id).strip()
    if not cid:
        raise ValueError("Set DHAN_CLIENT_ID in Streamlit Secrets.")
    pin = _secret_or_env("DHAN_PIN")
    totp_secret = _secret_or_env("DHAN_TOTP_SECRET")
    if pin and totp_secret:
        cached = st.session_state.get("dhan_auto_auth", {})
        expiry_text = cached.get("expiryTime", "")
        valid_until = None
        if expiry_text:
            try:
                valid_until = datetime.fromisoformat(str(expiry_text).replace("Z", "+00:00"))
                if valid_until.tzinfo is None:
                    valid_until = valid_until.replace(tzinfo=IST)
            except ValueError:
                valid_until = None
        if cached.get("client_id") != cid or not cached.get("accessToken") or not valid_until or valid_until <= datetime.now(IST) + timedelta(minutes=5):
            fresh = generate_access_token(cid, pin, totp_secret)
            st.session_state["dhan_auto_auth"] = {"client_id": cid, "accessToken": fresh["accessToken"], "expiryTime": fresh.get("expiryTime", "")}
            cached = st.session_state["dhan_auto_auth"]
        return DhanDataClient(cid, cached["accessToken"])
    if not token:
        raise ValueError("Configure DHAN_PIN and DHAN_TOTP_SECRET for automatic login, or provide a valid 24-hour DHAN_ACCESS_TOKEN.")
    return DhanDataClient(cid, token)


@st.cache_data(ttl=21600, show_spinner=False)
def _cached_fno_universe():
    return load_nse_stock_fno_universe()

@st.cache_data(ttl=43200, show_spinner=False)
def _cached_daily_candles(_client, client_id: str, security_id: str, from_date: str):
    """Cache completed daily history per Dhan account/stock; current day is excluded by Dhan toDate."""
    return _client.daily(security_id, "NSE_EQ", "EQUITY", datetime.strptime(from_date, "%Y-%m-%d").date())

try:
    with st.spinner("Dhan masterમાંથી બધા NSE Stock F&O શેર load થઈ રહ્યા છે..."):
        universe = _cached_fno_universe()
    universe_error = None
except Exception as universe_exc:
    universe = []
    universe_error = str(universe_exc)
    st.error(f"F&O universe unavailable: {universe_error}. જૂની 10-stock listથી scan ચલાવવામાં આવશે નહીં.")

c1,c2,c3=st.columns(3)
c1.metric("Strategy",strategy)
c2.metric("NSE Stock F&O",len(universe))
c3.metric("IST",datetime.now(IST).strftime("%d %b %Y, %H:%M:%S"))

if strategy=="BTST" and not (datetime.strptime("15:16","%H:%M").time()<=datetime.now(IST).time().replace(tzinfo=None)<=datetime.strptime("15:29","%H:%M").time()):
    st.info("BTST ONE-CLICK window: 3:16 PM–3:29 PM IST. Press after the 3:15 one-minute candle closes; the scanner checks the 3:00–3:14 shortlist plus the completed 3:15+ confirmation, then fetches the matching CALL/PUT automatically.")

if st.button("🔌 Test Dhan API Connection"):
    try:
        profile=_dhan_client().profile()
        st.success("Dhan API authentication successful.")
        st.write({"Client ID matches":str(profile.get("dhanClientId"))==str(client_id).strip(),
                  "Token validity":profile.get("tokenValidity","Not returned"),
                  "Data API plan":profile.get("dataPlan","Not returned"),
                  "Data validity":profile.get("dataValidity","Not returned")})
    except Exception as e:
        st.error(f"Dhan connection test failed: {e}")

st.markdown("### TradingView Stock Screener Confirmation")
st.caption("TradingViewમાં Stock Screenerનું CSV Export અહીં upload કરો. Official live screener API/scraping પર આધાર નથી; CSV data માત્ર extra confirmation છે.")
tv_file = st.file_uploader("TradingView Screener CSV (optional)", type=["csv"], key="tv_stock_screener_csv")
tv_screener = None
if tv_file is not None:
    try:
        tv_screener = parse_tradingview_csv(tv_file.getvalue())
        st.success(f"TradingView CSV loaded: {len(tv_screener)} symbols. Uploaded screener must agree with the stock signal.")
    except Exception as tv_error:
        st.error(f"TradingView CSV error: {tv_error}")
        tv_screener = {}

if st.button("🚀 ONE-CLICK SCAN — STOCK + CALL / PUT",type="primary", disabled=not universe):
    scan_started_at = datetime.now(IST)
    # Never leave yesterday's/previous-strategy signal visible if this scan errors or returns NO TRADE.
    for stale_key in ("stock_signals", "stock_signals_strategy", "stock_option_result",
                      "stock_option_error", "scan_duration_sec", "btst_phase_status",
                      "nse_scan_snapshot", "nse_scan_errors", "nse_scan_fetched_at",
                      "nse_scan_available"):
        st.session_state.pop(stale_key, None)
    try:
        client=_dhan_client()
        signals=[]; diagnostics=[]; progress=st.progress(0)
        with st.spinner("Fetching NSE stock activity, volume and OI confirmation, then scanning Dhan candles..."):
            # NSE public website snapshots are corroboration only, not a licensed real-time feed.
            nse = NSEMarketData()
            nse_snapshot = {}
            nse_errors = {}
            for key, fn in [
                ("Most Active Underlying", nse.most_active_underlying),
                ("Volume Gainers", nse.volume_gainers),
                ("OI Spurts", nse.oi_spurts),
            ]:
                try:
                    nse_snapshot[key] = fn()
                except Exception as nse_exc:
                    nse_errors[key] = str(nse_exc)
            nse_sources_ready = sum(
                isinstance(frame, pd.DataFrame) and not frame.empty
                for frame in nse_snapshot.values()
            )
            st.session_state["nse_scan_snapshot"] = nse_snapshot
            st.session_state["nse_scan_errors"] = nse_errors
            st.session_state["nse_scan_fetched_at"] = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
            st.session_state["nse_scan_available"] = nse_sources_ready >= 2
            stock_context = {"regime":"STOCK_ONLY","reason":"NIFTY/index is not fetched or used as a stock filter."}
            daily_start = datetime.now(IST).date() - timedelta(days=daily_lookback)

            def scan_one_stock(item):
                """Fetch one stock's candles and evaluate it in an isolated HTTP session."""
                intra = pd.DataFrame()
                daily = pd.DataFrame()
                try:
                    worker_client = DhanDataClient(client.client_id, client.access_token)
                    request_end = datetime.now(IST).replace(tzinfo=None)
                    intra = worker_client.intraday(
                        item["security_id"], "NSE_EQ", "EQUITY", interval,
                        request_end - timedelta(days=lookback), request_end
                    )
                    # Dhan intraday timestamps label candle starts. Exclude the still-forming
                    # bar so EMA/VWAP/volume and the 3:15 confirmation use closed candles only.
                    bar_delta = pd.Timedelta(minutes=int(interval))
                    completed_cutoff = pd.Timestamp(datetime.now(IST)) - bar_delta
                    intra = intra.loc[intra.index <= completed_cutoff]
                    daily = _cached_daily_candles(
                        worker_client, client.client_id, item["security_id"], daily_start.isoformat()
                    )
                    diagnostic = {
                        "Stock": item["symbol"], "Intraday candles": len(intra), "Daily candles": len(daily),
                        "Status": "Data OK" if len(intra) >= 220 and len(daily) >= 205
                        else f"Insufficient history: need 220 intraday / 205 daily; got {len(intra)} / {len(daily)}"
                    }
                    sig = evaluate_symbol(
                        item["symbol"], intra, daily, stock_context,
                        strategy=strategy, sector_relative_strength=None, enforce_btst_window=False
                    )
                    if strategy == "BTST":
                        # Derive both stages from the same fetched candle series.
                        from datetime import time as _time
                        scan_day = scan_started_at.date()
                        today_mask = intra.index.date == scan_day
                        pre_mask = today_mask & (intra.index.time >= _time(15,0)) & (intra.index.time < _time(15,15))
                        post_mask = today_mask & (intra.index.time >= _time(15,15))
                        pre_rows = intra.loc[pre_mask]
                        post_rows = intra.loc[post_mask]
                        pre_sig = None
                        if not pre_rows.empty and not post_rows.empty:
                            pre_history = intra.loc[intra.index <= pre_rows.index[-1]]
                            pre_sig = evaluate_symbol(
                                item["symbol"], pre_history, daily, stock_context,
                                strategy="BTST", sector_relative_strength=None, enforce_btst_window=False
                            )
                        try:
                            latest_ts = pd.Timestamp(intra.index[-1])
                            scan_end = datetime.now(IST)
                            if latest_ts.tzinfo is None:
                                latest_ts = latest_ts.tz_localize(IST)
                            candle_close_ts = latest_ts + pd.Timedelta(minutes=int(interval))
                            candle_age = (scan_end - candle_close_ts.to_pydatetime()).total_seconds() / 60
                        except Exception:
                            candle_age = 999.0
                        btst_ok, btst_reason = validate_btst_confirmation(
                            pre_sig, sig, candle_age, int(interval)
                        )
                        if not btst_ok:
                            diagnostic["Status"] = "Rejected: BTST confirmation — " + btst_reason
                            sig = None
                        else:
                            sig["pre_confirmation_rank"] = float(pre_sig.get("rank_score",0))
                            sig["confirmation_candle_age_min"] = round(candle_age,1)
                    if sig:
                        nse_check = confirm_stock_with_nse(nse_snapshot, item["symbol"], sig["side"])
                        sig["nse_confirmation"] = nse_check
                        # NSE public website feeds are supplementary corroboration, not a
                        # hard dependency. Dhan candles remain the primary live stock data.
                        # Reject only when usable NSE evidence explicitly contradicts direction
                        # or shows non-positive OI on a matched OI-spurt row.
                        if nse_check.get("conflict"):
                            diagnostic["Status"] = "Rejected: NSE evidence conflicts — " + nse_check.get("reason", "NSE evidence conflicts with Dhan.")
                            sig = None
                        else:
                            bonus = float(nse_check.get("score_bonus", 0) or 0) if nse_check.get("aligned") else 0.0
                            sig["score"] = min(100.0, float(sig.get("score", 0)) + bonus)
                            sig["rank_score"] = min(100.0, float(sig.get("rank_score", 0)) + bonus)
                            label = "NSE confirmation" if nse_check.get("aligned") else "NSE corroboration unavailable/insufficient; Dhan technical checks used"
                            sig["reasons"] += "; " + label + ": " + nse_check.get("reason", "")
                    if sig and tv_screener is not None:
                        tv_check = confirm_with_tradingview(sig, tv_screener)
                        sig["tradingview_confirmation"] = tv_check
                        if not tv_check.get("matched") or not tv_check.get("aligned"):
                            diagnostic["Status"] = "Rejected: TradingView confirmation — " + tv_check.get("reason","No confirmation")
                            sig = None
                        else:
                            sig["score"] = min(100.0, float(sig.get("score",0)) + 5.0)
                            sig["reasons"] += "; TradingView: " + tv_check.get("reason","")
                    if sig is None and diagnostic["Status"] == "Data OK":
                        details = diagnose_symbol(intra, daily, strategy=strategy)
                        diagnostic.update(details)
                        diagnostic["Status"] = (
                            "No setup: " + details.get(
                                "Best-side diagnostic",
                                "one or more stock confirmation checks did not pass."
                            )
                        )
                    if sig:
                        sig["sector"] = item["sector"]
                        if tv_screener is None:
                            sig["tradingview_confirmation"] = {
                                "matched": False, "aligned": False, "score": 0,
                                "reason": "CSV not supplied; NSE public stock snapshot + Dhan candle confirmation used."
                            }
                    return diagnostic, sig
                except Exception as exc:
                    return {
                        "Stock": item.get("symbol","UNKNOWN"),
                        "Intraday candles": len(intra),
                        "Daily candles": len(daily),
                        "Status": f"SCAN ERROR: {type(exc).__name__}: {str(exc)[:450]}"
                    }, None

            # Four isolated workers overlap network latency; market_data.py globally
            # throttles chart requests to stay below Dhan's 5 requests/second Data API cap.
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="htnexus-scan") as pool:
                futures = [pool.submit(scan_one_stock, item) for item in universe]
                for i, future in enumerate(as_completed(futures), start=1):
                    diagnostic, sig = future.result()
                    diagnostics.append(diagnostic)
                    if sig:
                        signals.append(sig)
                    progress.progress(i / max(len(universe),1))
        scan_finished_at = datetime.now(IST)
        if strategy == "BTST" and signals:
            fresh_signals = []
            for candidate in signals:
                try:
                    candidate_ts = pd.Timestamp(candidate.get("signal_time"))
                    if candidate_ts.tzinfo is None:
                        candidate_ts = candidate_ts.tz_localize(IST)
                    candidate_close_ts = candidate_ts + pd.Timedelta(minutes=int(interval))
                    final_age = (scan_finished_at - candidate_close_ts.to_pydatetime()).total_seconds() / 60
                except Exception:
                    final_age = 999.0
                if 0 <= final_age <= int(interval) + 7:
                    candidate["confirmation_candle_age_min"] = round(final_age,1)
                    fresh_signals.append(candidate)
                else:
                    diagnostics.append({"Stock":candidate.get("symbol","BTST"),"Intraday candles":0,"Daily candles":0,
                        "Status":"Rejected after full scan: confirmation candle became stale ("+str(round(final_age,1))+" minutes old)."})
            signals = fresh_signals
        st.session_state["stock_diagnostics"]=diagnostics
        st.session_state.pop("stock_option_result", None)
        st.session_state.pop("stock_option_error", None)
        phase = "NORMAL"
        if strategy == "BTST":
            from datetime import time as _time
            scan_clock = scan_started_at.time().replace(tzinfo=None)
            if not (_time(15,16) <= scan_clock <= _time(15,29) and scan_finished_at.time().replace(tzinfo=None) <= _time(15,29,59)):
                phase = "OUTSIDE_WINDOW"
                signals = []
                diagnostics.append({"Stock":"BTST","Intraday candles":0,"Daily candles":0,"Status":"Run the one-click BTST scan between 3:16 PM and 3:29 PM IST, after the 3:15 candle has closed."})
            else:
                phase = "CONFIRMED" if signals else "NO_TRADE"
        st.session_state["btst_phase_status"] = phase
        st.session_state["stock_signals"] = signals
        st.session_state["stock_signals_strategy"] = strategy
        # One-click flow: validate the selected stock's matching CALL/PUT automatically.
        may_validate_options = bool(signals) and (strategy != "BTST" or phase == "CONFIRMED")
        if may_validate_options:
            strongest = max(signals,key=lambda x:(float(x.get("rank_score",0)),float(x.get("score",0)),float(x.get("risk_reward",0))))
            underlying = next((x for x in universe if x["symbol"] == strongest["symbol"]), None)
            if underlying:
                try:
                    st.session_state["stock_option_result"] = scan_stock_option_chain(client,strongest["symbol"],underlying.get("fno_security_id",underlying["security_id"]),strongest["side"],strategy=strategy)
                except Exception as option_exc:
                    st.session_state["stock_option_error"] = str(option_exc)
        else:
            st.session_state["stock_option_result"] = None
        st.session_state["scan_duration_sec"] = round((datetime.now(IST) - scan_started_at).total_seconds(), 1)
    except Exception as e:
        st.error(f"Data/API error: {e}")

if "scan_duration_sec" in st.session_state:
    st.caption(f"Last one-click scan completed in {st.session_state['scan_duration_sec']:.1f} seconds (includes automatic option-chain check when a stock qualifies).")

if "stock_signals" in st.session_state and st.session_state.get("stock_signals_strategy") == strategy:
    signals=st.session_state["stock_signals"]
    st.subheader("Strongest NSE Stock BTST / SWING Signal")
    if signals:
        phase = st.session_state.get("btst_phase_status", "NORMAL")
        if strategy == "BTST" and phase == "CONFIRMED":
            st.info("BTST stock setup passed the 3:00–3:14 shortlist and fresh 3:15+ confirmation; Dhan option validation is required. NSE public data is supplementary corroboration when available.")
        # Keep exactly one highest-scoring qualifying stock; never mix weaker signals.
        best=max(signals,key=lambda x:(float(x.get("rank_score",0)),float(x.get("score",0)),float(x.get("risk_reward",0))))
        df=pd.DataFrame([{"Stock":best["symbol"],"Strategy":best["strategy"],"Side":best["side"],"Entry":round(best["entry"],2),"Stop Loss":round(best["stop_loss"],2),"Target 1":round(best.get("target1",best["target"]),2),"Target 2":round(best.get("target2",best["target"]),2),"R:R":round(best["risk_reward"],2),"Score":best["score"],"Rank Score":best.get("rank_score",0),"Status":best.get("score_label","WATCHLIST"),"Sector":best.get("sector","")}])
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.write(best["reasons"])
        nsec = best.get("nse_confirmation", {})
        if nsec.get("aligned"):
            st.success("NSE stock corroboration: PASS • " + ", ".join(nsec.get("evidence", [])))
            st.caption(nsec.get("reason", ""))
        elif nsec.get("conflict"):
            st.error("NSE data conflicts with the Dhan stock direction. Candidate rejected. " + nsec.get("reason", ""))
        else:
            st.info("NSE public snapshot was unavailable or did not independently match this stock. Dhan candle checks remain primary; no NSE bonus was added. " + nsec.get("reason", ""))
        tvc = best.get("tradingview_confirmation", {})
        if tvc.get("matched") and tvc.get("aligned"):
            st.success(f"TradingView confirmation: PASS • {tvc.get('score',0)}/100")
            st.caption(tvc.get("reason",""))
        elif tv_screener is not None:
            st.error("TradingView confirmation was not passed. This candidate should not be treated as a trade.")
        else:
            st.info("TradingView CSV was not uploaded; NSE public stock snapshot + Dhan candle confirmation were used.")
        if best.get("strategy") == "BTST" and best.get("btst_quality"):
            st.markdown("**BTST Closing Quality**")
            st.json(best["btst_quality"])
        if best.get("score_label") in ("STRONG","QUALIFIED"):
            st.info(f"Stock setup passed technical checks: {best['symbol']} | Entry ₹{best['entry']:.2f} | SL ₹{best['stop_loss']:.2f} | Target ₹{best['target']:.2f}. Final trade still requires a liquid option contract.")
        else:
            st.warning(f"Best WATCHLIST candidate only: {best['symbol']} | Score {best['score']}/100 — not a confirmed trade.")
        st.caption(f"Scanned {len(universe)} NSE Stock F&O underlyings; {len(signals)} passed stock confirmations.")
        option_result = st.session_state.get("stock_option_result")
        if option_result and option_result.get("best"):
            contract = option_result["best"]
            st.success(f"FINAL OPTION-CHECKED CANDIDATE: {best['symbol']} {contract['Type']} {contract['Strike']} • Expiry {contract['Expiry']} • LTP ₹{contract['LTP']:.2f} • Indicative buy ₹{contract['BUY (Ask)']:.2f} • Spread {contract['Spread %']:.2f}% • OI {contract['OI']:,} • Volume {contract['Volume']:,}")
            st.caption(f"Premium SL ₹{contract['SL']:.2f} • T1 ₹{contract['Target 1']:.2f} • T2 ₹{contract['Target 2']:.2f} • Delta {contract['Delta']:.3f} • Read-only; no order is placed.")
        elif st.session_state.get("stock_option_error"):
            st.error("NO TRADE — stock setup passed, but Dhan option validation failed. " + st.session_state["stock_option_error"])
        else:
            st.error("NO TRADE — no liquid CALL/PUT option contract passed the Dhan validation rules.")
        st.divider()
        st.caption("Only BTST and Swing stock strategies are scanned. NIFTY is not a required stock filter. No auto orders.")
    else:
        diagnostics = st.session_state.get("stock_diagnostics", [])
        scan_errors = [
            d for d in diagnostics
            if str(d.get("Status", "")).startswith(
                ("SCAN ERROR:", "Dhan rejected", "Network error", "Dhan HTTP", "Dhan API")
            )
        ]
        all_data_failed = bool(diagnostics) and len(scan_errors) == len(diagnostics)
        all_insufficient = bool(diagnostics) and all(str(d.get("Status", "")).startswith("Insufficient history") for d in diagnostics)
        if all_data_failed:
            st.error("DATA / SCAN ERROR — every stock worker failed before a complete technical evaluation. This is NOT a genuine NO TRADE result. Check the per-stock error details below.")
        elif scan_errors:
            st.warning(f"INCOMPLETE SCAN — {len(scan_errors)} of {len(diagnostics)} stock workers had data/API/processing errors. This result is NOT a verified NO TRADE; review the per-stock errors below.")
        elif all_insufficient:
            st.warning("INSUFFICIENT DATA — required candle histories were missing for every scanned stock. This is NOT a genuine NO TRADE result. Check the candle counts below.")
        else:
            st.warning("No stock passed the confirmation rules in this scan. This is a genuine NO TRADE result, not an index signal.")
    if st.session_state.get("stock_diagnostics"):
        with st.expander("Per-stock data diagnostics"):
            st.dataframe(pd.DataFrame(st.session_state["stock_diagnostics"]),use_container_width=True,hide_index=True)



st.divider()
st.subheader("🏛️ NSE STOCK MARKET CONFIRMATION")
st.caption("Each one-click scan fetches NSE Most Active Underlying, Volume Gainers and OI Spurts automatically. These are best-effort NSE public website snapshots, not a licensed real-time feed; source data may be delayed or rate-limited.")
if st.session_state.get("nse_scan_fetched_at"):
    st.caption("NSE snapshot fetched: " + st.session_state["nse_scan_fetched_at"])
    snap = st.session_state.get("nse_scan_snapshot", {})
    for section in ("Most Active Underlying", "Volume Gainers", "OI Spurts"):
        frame = snap.get(section)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            with st.expander(f"{section} ({len(frame)} rows)"):
                st.dataframe(frame.head(50), use_container_width=True, hide_index=True)
    errs = st.session_state.get("nse_scan_errors", {})
    if errs:
        with st.expander("NSE data availability / errors"):
            for section, error in errs.items():
                st.warning(f"{section}: {error}")
else:
    st.info("NSE stock confirmation is fetched automatically when ONE-CLICK SCAN is pressed.")
st.caption("NSE price direction is used only as independent stock-level corroboration. Positive OI change indicates new positioning, not whether buyers or sellers initiated it. Dhan candles determine the stock setup; Dhan option chain determines the actual CALL/PUT contract. NIFTY/index data is not a required stock filter.")

with st.expander("Historical Backtesting"):
    st.caption("Single-stock historical test; historical performance does not guarantee future results.")
    bt_symbol=st.selectbox("Backtest stock",[x["symbol"] for x in universe] or ["RELIANCE"])
    bt_lookback=st.selectbox("Intraday history (calendar days)",[30,60,90],index=2)
    if st.button("Run Backtest"):
        try:
            item=next(x for x in universe if x["symbol"]==bt_symbol)
            client=_dhan_client(); end=datetime.now(IST).replace(tzinfo=None)
            with st.spinner(f"Downloading {bt_symbol} candles..."):
                intra=client.intraday(item["security_id"],"NSE_EQ","EQUITY",interval,end-timedelta(days=bt_lookback),end)
                daily=client.daily(item["security_id"],"NSE_EQ","EQUITY",datetime.now(IST).date()-timedelta(days=daily_lookback))
            a,b,c=st.columns(3); a.metric("Intraday candles",len(intra)); b.metric("Daily candles",len(daily)); c.metric("Interval",f"{interval} min")
            if not intra.empty: st.caption(f"Intraday: {intra.index.min()} → {intra.index.max()}")
            if not daily.empty: st.caption(f"Daily: {daily.index.min()} → {daily.index.max()}")
            if intra.empty or daily.empty: st.error("Historical data is empty. Verify Security ID and Dhan Data API access.")
            else:
                result=run_backtest(bt_symbol,intra,daily,strategy,capital,risk)
                st.json(result["summary"])
                if result["summary"]["total_trades"]==0: st.warning("No historical entries met the current stock-only rules. Review candle counts and filters; zero trades is not a performance result.")
                if not result["trades"].empty:
                    st.dataframe(result["trades"],use_container_width=True)
                    st.download_button("Download trades CSV",result["trades"].to_csv(index=False),file_name=f"{bt_symbol}_{strategy}_backtest.csv",mime="text/csv")
        except Exception as e: st.error(f"Backtest error: {e}")

with st.expander("Paper Trading Journal"):
    with st.form("paper_form"):
        sym=st.text_input("Stock symbol"); side=st.selectbox("Side",["LONG","SHORT"])
        entry=st.number_input("Entry",min_value=0.0,format="%.2f"); stop=st.number_input("Stop Loss",min_value=0.0,format="%.2f"); target=st.number_input("Target",min_value=0.0,format="%.2f")
        submitted=st.form_submit_button("Add paper trade")
    if submitted and sym and entry>0 and stop>0 and target>0:
        st.session_state.setdefault("paper_trades",[]).append({"time":datetime.now(IST).isoformat(),"symbol":sym.upper(),"side":side,"entry":entry,"stop":stop,"target":target,"status":"OPEN"})
    if st.session_state.get("paper_trades"): st.dataframe(pd.DataFrame(st.session_state["paper_trades"]),use_container_width=True)
st.divider()
st.caption("Read-only analytics. No automatic order placement. Signals are not guaranteed; validate on paper before live use.")
