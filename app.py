"""HT NEXUS — stock-only BTST/SWING scanner. Read-only Dhan data."""
import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from market_data import DhanDataClient, generate_access_token, parse_universe
from strategy_engine import evaluate_symbol, market_regime, relative_strength
from backtest import run_backtest
from stock_options import scan_stock_option_chain
from nse_market_data import NSEMarketData, NSEDataError
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
    lookback=st.slider("Intraday history (days)",7,90,30)
    daily_lookback=st.slider("Daily history (calendar days)",250,1500,500,step=50)
    capital=st.number_input("Backtest capital (₹)",min_value=10000,value=100000,step=10000)
    risk=st.slider("Risk per trade (%)",0.25,1.0,0.5,step=0.25)/100
    nifty_id=st.text_input("Dhan NIFTY 50 Index Security ID",value="13",help="Dhan instrument master મુજબ IDX_I / INDEX ID ચકાસો.")
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

if strategy=="BTST" and not (datetime.strptime("15:15","%H:%M").time()<=datetime.now(IST).time().replace(tzinfo=None)<=datetime.strptime("15:29","%H:%M").time()):
    st.info("BTST ONE-CLICK window: 3:15 PM–3:29 PM IST. One scan internally checks the 3:00–3:14 shortlist and fresh 3:15+ confirmation, then fetches the matching stock CALL/PUT automatically.")

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
    try:
        client=_dhan_client()
        signals=[]; diagnostics=[]; progress=st.progress(0)
        with st.spinner("Scanning NSE stocks independently; NIFTY is context only..."):
            end=datetime.now(IST).replace(tzinfo=None)
            # Stock-only scan: skip index requests so they cannot delay or filter stock candidates.
            nifty_daily = pd.DataFrame()
            nifty_regime = {"regime":"STOCK_ONLY","reason":"NIFTY is not used as a filter or trade signal."}
            st.session_state["nifty_regime"] = nifty_regime
            st.session_state["nifty_signal"] = None
            st.session_state["nifty_daily"] = nifty_daily
            for i,item in enumerate(universe):
                try:
                    end=datetime.now(IST).replace(tzinfo=None)
                    intra=client.intraday(item["security_id"],"NSE_EQ","EQUITY",interval,end-timedelta(days=lookback),end)
                    daily=client.daily(item["security_id"],"NSE_EQ","EQUITY",datetime.now(IST).date()-timedelta(days=daily_lookback))
                    diagnostics.append({"Stock":item["symbol"],"Intraday candles":len(intra),"Daily candles":len(daily),"Status":"Data OK" if len(intra)>=220 and len(daily)>=205 else f"Insufficient history: need 220 intraday / 205 daily; got {len(intra)} / {len(daily)}"})
                    srs = None
                    sig=evaluate_symbol(item["symbol"],intra,daily,nifty_regime,strategy=strategy,sector_relative_strength=srs,enforce_btst_window=False)
                    if strategy == "BTST":
                        # One pass: derive the 3:00–3:14 shortlist from the same candles,
                        # then validate the same stock/direction on the latest 3:15+ candle.
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
                            pre_sig = evaluate_symbol(item["symbol"],pre_history,daily,nifty_regime,strategy="BTST",sector_relative_strength=None,enforce_btst_window=False)
                        if not pre_sig or not sig or pre_sig["side"] != sig["side"]:
                            sig = None
                        else:
                            try:
                                latest_ts = pd.Timestamp(intra.index[-1])
                                scan_end = datetime.now(IST)
                                if latest_ts.tzinfo is None:
                                    latest_ts = latest_ts.tz_localize(IST)
                                candle_age = (scan_end - latest_ts.to_pydatetime()).total_seconds() / 60
                            except Exception:
                                candle_age = 999.0
                            rank_ok = float(sig.get("rank_score",0)) >= float(pre_sig.get("rank_score",0)) - 15.0
                            fresh_ok = 0 <= candle_age <= int(interval) + 7
                            if not (rank_ok and fresh_ok and float(sig.get("score",0)) >= 85.0):
                                diagnostics.append({"Stock":item["symbol"],"Intraday candles":len(intra),"Daily candles":len(daily),
                                    "Status":"Rejected: 3:15+ confirmation failed (fresh candle="+str(fresh_ok)+", age="+str(round(candle_age,1))+"m, rank retained="+str(rank_ok)+")."})
                                sig = None
                            else:
                                sig["pre_confirmation_rank"] = float(pre_sig.get("rank_score",0))
                                sig["confirmation_candle_age_min"] = round(candle_age,1)
                    if sig and tv_screener is not None:
                        tv_check = confirm_with_tradingview(sig, tv_screener)
                        sig["tradingview_confirmation"] = tv_check
                        # When a screener file is supplied, require a matching symbol
                        # and at least two aligned screener checks; otherwise reject.
                        if not tv_check.get("matched") or not tv_check.get("aligned"):
                            diagnostics.append({"Stock":item["symbol"],"Intraday candles":len(intra),"Daily candles":len(daily),"Status":"Rejected: TradingView confirmation — "+tv_check.get("reason","No confirmation")})
                            sig = None
                        else:
                            sig["score"] = min(100.0, float(sig.get("score", 0)) + 5.0)
                            sig["reasons"] += "; TradingView: "+tv_check.get("reason","")
                    if sig:
                        sig["sector"]=item["sector"]
                        if tv_screener is None:
                            sig["tradingview_confirmation"] = {"matched": False, "aligned": False, "score": 0, "reason": "CSV not supplied; Dhan technical checks only."}
                        signals.append(sig)
                except Exception as e:
                    diagnostics.append({"Stock":item["symbol"],"Intraday candles":0,"Daily candles":0,"Status":str(e)[:500]})
                progress.progress((i+1)/max(len(universe),1))
        scan_finished_at = datetime.now(IST)
        st.session_state["stock_diagnostics"]=diagnostics
        st.session_state.pop("stock_option_result", None)
        st.session_state.pop("stock_option_error", None)
        phase = "NORMAL"
        if strategy == "BTST":
            from datetime import time as _time
            scan_clock = scan_started_at.time().replace(tzinfo=None)
            if not (_time(15,15) <= scan_clock <= _time(15,29) and scan_finished_at.time().replace(tzinfo=None) <= _time(15,29,59)):
                phase = "OUTSIDE_WINDOW"
                signals = []
                diagnostics.append({"Stock":"BTST","Intraday candles":0,"Daily candles":0,"Status":"Run the one-click BTST scan between 3:15 PM and 3:29 PM IST."})
            else:
                phase = "CONFIRMED" if signals else "NO_TRADE"
        st.session_state["btst_phase_status"] = phase
        st.session_state["stock_signals"] = signals
        # One-click flow: validate the selected stock's matching CALL/PUT automatically.
        may_validate_options = bool(signals) and (strategy != "BTST" or phase == "CONFIRMED")
        if may_validate_options:
            strongest = max(signals,key=lambda x:(float(x.get("rank_score",0)),float(x.get("score",0)),float(x.get("risk_reward",0))))
            underlying = next((x for x in universe if x["symbol"] == strongest["symbol"]), None)
            if underlying:
                try:
                    st.session_state["stock_option_result"] = scan_stock_option_chain(client,strongest["symbol"],underlying.get("fno_security_id",underlying["security_id"]),strongest["side"])
                except Exception as option_exc:
                    st.session_state["stock_option_error"] = str(option_exc)
        else:
            st.session_state["stock_option_result"] = None
    except Exception as e:
        st.error(f"Data/API error: {e}")

if "stock_signals" in st.session_state:
    signals=st.session_state["stock_signals"]
    st.subheader("Strongest NSE Stock BTST / SWING Signal")
    if signals:
        phase = st.session_state.get("btst_phase_status", "NORMAL")
        if strategy == "BTST" and phase == "CONFIRMED":
            st.success("ONE-CLICK BTST CONFIRMATION PASSED — 3:00–3:14 shortlist + fresh 3:15+ confirmation checked in one scan.")
        # Keep exactly one highest-scoring qualifying stock; never mix weaker signals.
        best=max(signals,key=lambda x:(float(x.get("rank_score",0)),float(x.get("score",0)),float(x.get("risk_reward",0))))
        df=pd.DataFrame([{"Stock":best["symbol"],"Strategy":best["strategy"],"Side":best["side"],"Entry":round(best["entry"],2),"Stop Loss":round(best["stop_loss"],2),"Target 1":round(best.get("target1",best["target"]),2),"Target 2":round(best.get("target2",best["target"]),2),"R:R":round(best["risk_reward"],2),"Score":best["score"],"Rank Score":best.get("rank_score",0),"Status":best.get("score_label","WATCHLIST"),"Sector":best.get("sector","")}])
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.write(best["reasons"])
        tvc = best.get("tradingview_confirmation", {})
        if tvc.get("matched") and tvc.get("aligned"):
            st.success(f"TradingView confirmation: PASS • {tvc.get('score',0)}/100")
            st.caption(tvc.get("reason",""))
        elif tv_screener is not None:
            st.error("TradingView confirmation was not passed. This candidate should not be treated as a trade.")
        else:
            st.info("TradingView CSV was not uploaded; this signal uses Dhan candle-based confirmation only.")
        if best.get("strategy") == "BTST" and best.get("btst_quality"):
            st.markdown("**BTST Closing Quality**")
            st.json(best["btst_quality"])
        if best.get("score_label") in ("STRONG","QUALIFIED"):
            st.success(f"Qualified setup: {best['symbol']} | Entry ₹{best['entry']:.2f} | SL ₹{best['stop_loss']:.2f} | Target ₹{best['target']:.2f} | R:R 1:{best['risk_reward']:.2f}")
        else:
            st.warning(f"Best WATCHLIST candidate only: {best['symbol']} | Score {best['score']}/100 — not a confirmed trade.")
        st.caption(f"Scanned {len(universe)} NSE Stock F&O underlyings; {len(signals)} passed stock confirmations.")
        option_result = st.session_state.get("stock_option_result")
        if option_result and option_result.get("best"):
            contract = option_result["best"]
            st.success(f"FINAL OPTION-CHECKED CANDIDATE: {best['symbol']} {contract['Type']} {contract['Strike']} • Expiry {contract['Expiry']} • LTP ₹{contract['LTP']:.2f} • Indicative buy ₹{contract['BUY (Ask)']:.2f} • Spread {contract['Spread %']:.2f}% • OI {contract['OI']:,} • Volume {contract['Volume']:,}")
            st.caption(f"Premium SL ₹{contract['SL']:.2f} • T1 ₹{contract['Target 1']:.2f} • T2 ₹{contract['Target 2']:.2f} • Delta {contract['Delta']:.3f} • Read-only; no order is placed.")
        elif st.session_state.get("stock_option_error"):
            st.warning("STOCK SETUP FOUND, BUT OPTION VALIDATION FAILED — NO FINAL OPTION TRADE. " + st.session_state["stock_option_error"])
        else:
            st.warning("STOCK SETUP FOUND, BUT NO LIQUID OPTION CONTRACT PASSED — NO FINAL TRADE.")
        st.divider()
        st.caption("Only BTST and Swing stock strategies are scanned. NIFTY is not a required stock filter. No auto orders.")
    else:
        diagnostics = st.session_state.get("stock_diagnostics", [])
        all_data_failed = bool(diagnostics) and all(
            int(d.get("Intraday candles", 0) or 0) == 0
            and int(d.get("Daily candles", 0) or 0) == 0
            and str(d.get("Status", "")).startswith(("Dhan rejected", "Network error", "Dhan HTTP", "Dhan API"))
            for d in diagnostics
        )
        all_insufficient = bool(diagnostics) and all(str(d.get("Status", "")).startswith("Insufficient history") for d in diagnostics)
        if all_data_failed:
            st.error("DATA ERROR — Dhan rejected the market-data requests for every stock. No strategy confirmation was possible. This is NOT a genuine NO TRADE result. Check the full per-stock error below before relying on this scan.")
        elif all_insufficient:
            st.warning("INSUFFICIENT DATA — one or more required candle histories are missing for every scanned stock. This is NOT a genuine NO TRADE result. Check the candle counts below.")
        else:
            st.warning("No stock passed the confirmation rules in this scan. This is a genuine NO TRADE result, not an index signal.")
    if st.session_state.get("stock_diagnostics"):
        with st.expander("Per-stock data diagnostics"):
            st.dataframe(pd.DataFrame(st.session_state["stock_diagnostics"]),use_container_width=True,hide_index=True)



st.divider()
st.subheader("🏛️ NSE OFFICIAL MARKET DATA — MARKET INTELLIGENCE")
st.caption("NSE websiteના Most Active Contracts, Most Active Underlying, OI Spurts, Volume Gainers અને FII/DII reports. આ data ઉપલબ્ધ હોય ત્યારે જ બતાવવામાં આવશે; blocked/late dataને live signal માનવામાં નહીં આવે.")
if st.button("🌐 Fetch NSE Market Data", key="fetch_nse_market_data"):
    try:
        nse = NSEMarketData()
        nse_snapshot = {}
        errors = {}
        for key, fn in [
            ("Most Active Contracts", nse.most_active_contracts),
            ("Most Active Underlying", nse.most_active_underlying),
            ("OI Spurts", nse.oi_spurts),
            ("Volume Gainers", nse.volume_gainers),
            ("FII / DII", nse.fii_dii),
            ("All Indices / India VIX", nse.all_indices),
            ("NIFTY 50 Breadth", nse.equity_index),
        ]:
            try:
                nse_snapshot[key] = fn()
            except Exception as ex:
                errors[key] = str(ex)
        st.session_state["nse_market_snapshot"] = nse_snapshot
        st.session_state["nse_market_errors"] = errors
        st.session_state["nse_market_fetched_at"] = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
    except Exception as ex:
        st.error(f"NSE Market Data fetch failed: {ex}")
if st.session_state.get("nse_market_snapshot") is not None:
    snap = st.session_state["nse_market_snapshot"]
    st.caption(f"Last fetch attempt: {st.session_state.get('nse_market_fetched_at','—')}")
    if snap:
        indices = snap.get("All Indices / India VIX")
        if indices is not None and not indices.empty:
            name_col = next((x for x in indices.columns if str(x).lower() in ("index","indexname","index_name")), None)
            if name_col:
                vix_rows = indices[indices[name_col].astype(str).str.upper().str.contains("INDIA VIX", na=False)]
                if not vix_rows.empty:
                    st.markdown("**India VIX snapshot**")
                    st.dataframe(vix_rows, use_container_width=True, hide_index=True)
        breadth = snap.get("NIFTY 50 Breadth")
        if breadth is not None and not breadth.empty:
            st.markdown("**NIFTY 50 constituents / market breadth**")
            st.dataframe(breadth.head(50), use_container_width=True, hide_index=True)
        for section in ("Most Active Contracts","Most Active Underlying","OI Spurts","Volume Gainers","FII / DII","All Indices / India VIX"):
            frame = snap.get(section)
            if frame is not None and not frame.empty:
                with st.expander(f"{section} ({len(frame)} rows)", expanded=(section=="Most Active Contracts")):
                    view = frame.copy()
                    # Prioritise NIFTY rows in the derivatives table, while retaining all returned rows.
                    if section == "Most Active Contracts":
                        symbol_col = next((x for x in view.columns if str(x).lower() in ("symbol","underlying","ticker")), None)
                        if symbol_col:
                            nifty_rows = view[view[symbol_col].astype(str).str.upper().eq("NIFTY")]
                            if not nifty_rows.empty:
                                st.markdown("**NIFTY contracts**")
                                st.dataframe(nifty_rows.head(30), use_container_width=True, hide_index=True)
                                st.markdown("**Other most-active contracts**")
                                view = view[~view.index.isin(nifty_rows.index)]
                    st.dataframe(view.head(50), use_container_width=True, hide_index=True)
    else:
        st.warning("NSEમાંથી કોઈ વિભાગનો usable data મળ્યો નથી. આ NO TRADE નથી; NSE data unavailable છે.")
    errs = st.session_state.get("nse_market_errors", {})
    if errs:
        with st.expander("NSE data availability / errors"):
            for section, error in errs.items():
                st.warning(f"{section}: {error}")
st.caption("NSE market data may be delayed, rate-limited or changed by NSE. OI/volume/turnover do not identify buyer vs seller by themselves. Cross-check with Dhan live option chain before a trade.")

st.divider()
st.subheader("📊 AUTOMATIC STOCK OPTION RESULT")
st.caption("આ વિભાગ ઉપરના ONE-CLICK SCAN માંથી જ data બતાવે છે. અહીં અલગ stock selection કે CE/PE scan button નથી.")
auto_option = st.session_state.get("stock_option_result")
if isinstance(auto_option, dict):
    st.metric(f"{auto_option['symbol']} Spot", f"₹{auto_option['spot']:.2f}")
    st.caption(f"Expiry: {auto_option['expiry']} • Stock direction: {auto_option['bias'] or 'No confirmed stock signal'}")
    flow_cols = st.columns(2)
    with flow_cols[0]:
        st.metric("CALL / CE premium change", f"{auto_option['call_flow']['premium_change']:+.2f}%")
        st.caption(f"OI change: {auto_option['call_flow']['oi_change']:,} • Volume: {auto_option['call_flow']['volume']:,}")
    with flow_cols[1]:
        st.metric("PUT / PE premium change", f"{auto_option['put_flow']['premium_change']:+.2f}%")
        st.caption(f"OI change: {auto_option['put_flow']['oi_change']:,} • Volume: {auto_option['put_flow']['volume']:,}")
    if auto_option.get("best"):
        contract = auto_option["best"]
        st.success(f"RECOMMENDED {contract['Type']} — {auto_option['symbol']} {contract['Strike']} • Expiry {contract['Expiry']} • LTP ₹{contract['LTP']:.2f} • Buy Ask ₹{contract['BUY (Ask)']:.2f}")
        st.caption(f"Premium SL ₹{contract['SL']:.2f} • Target 1 ₹{contract['Target 1']:.2f} • Target 2 ₹{contract['Target 2']:.2f} • Spread {contract['Spread %']:.2f}% • OI {contract['OI']:,} • Volume {contract['Volume']:,} • Delta {contract['Delta']:.3f}")
    else:
        st.warning("Stock setup passed, but its matching CALL/PUT did not pass liquidity filters. No option trade.")
elif st.session_state.get("stock_option_error"):
    st.warning("Automatic option-chain check failed: " + str(st.session_state["stock_option_error"]))
else:
    st.caption("એક જ ONE-CLICK SCAN ચલાવો; qualified stock મળશે તો તેનું matching CALL/PUT અહીં આપમેળે દેખાશે.")

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
