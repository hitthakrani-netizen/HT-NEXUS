"""HT NEXUS — stock-only BTST/SWING scanner. Read-only Dhan data."""
import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from market_data import DhanDataClient, generate_access_token, parse_universe
from strategy_engine import evaluate_symbol, market_regime, relative_strength
from backtest import run_backtest

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
</style>
""", unsafe_allow_html=True)
st.title("📈 HT NEXUS")
st.caption("NIFTY CALL/PUT BTST + NSE STOCK BTST/SWING • DhanHQ live data • Signals only — no auto orders")

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
    interval=st.selectbox("Intraday candle interval (minutes)",["5","15","1"],index=0)
    lookback=st.slider("Intraday history (days)",7,90,30)
    daily_lookback=st.slider("Daily history (calendar days)",250,1500,500,step=50)
    capital=st.number_input("Backtest capital (₹)",min_value=10000,value=100000,step=10000)
    risk=st.slider("Risk per trade (%)",0.25,2.0,1.0,step=0.25)/100
    nifty_id=st.text_input("Dhan NIFTY 50 Index Security ID",value="13",help="Dhan instrument master મુજબ IDX_I / INDEX ID ચકાસો.")
    st.markdown("**NSE stock universe — SYMBOL,SECURITY_ID,SECTOR**")
    universe_text=st.text_area("NSE stocks",value="RELIANCE,2885,ENERGY\nTCS,11536,IT\nINFY,1594,IT\nHDFCBANK,1333,BANK\nICICIBANK,4963,BANK\nSBIN,3045,BANK\nLT,11483,INDUSTRIALS\nITC,1660,FMCG\nAXISBANK,5900,BANK\nBHARTIARTL,10604,TELECOM",height=220)
    st.caption("Verify each Security ID with Dhan's current NSE_EQ instrument master.")


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


universe=parse_universe(universe_text)
c1,c2,c3=st.columns(3)
c1.metric("Strategy",strategy)
c2.metric("NSE stocks",len(universe))
c3.metric("IST",datetime.now(IST).strftime("%d %b %Y, %H:%M:%S"))

if strategy=="BTST" and not (datetime.strptime("15:15","%H:%M").time()<=datetime.now(IST).time().replace(tzinfo=None)<=datetime.strptime("15:29","%H:%M").time()):
    st.info("BTST live scan window: 3:15 PM–3:29 PM IST. Historical Backtesting can run anytime.")

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

if st.button("📈 Scan STOCKS — BTST / SWING",type="primary"):
    try:
        client=_dhan_client()
        signals=[]; diagnostics=[]; progress=st.progress(0)
        with st.spinner("Fetching NIFTY trend and scanning NSE stocks..."):
            end=datetime.now(IST).replace(tzinfo=None)
            nifty_daily=pd.DataFrame()
            nifty_regime={"regime":"NEUTRAL","reason":"NIFTY data not available; stock signals will not receive index confirmation."}
            nifty_signal=None
            try:
                nifty_daily=client.daily(nifty_id,"IDX_I","INDEX",datetime.now(IST).date()-timedelta(days=daily_lookback))
                nifty_intraday=client.intraday(nifty_id,"IDX_I","INDEX",interval,end-timedelta(days=lookback),end)
                nifty_regime=market_regime(nifty_daily)
                nifty_signal=evaluate_symbol("NIFTY",nifty_intraday,nifty_daily,nifty_regime,strategy=strategy,enforce_btst_window=False)
            except Exception as nifty_error:
                st.warning(f"NIFTY data unavailable: {nifty_error}. Stock scan will continue without index confirmation.")
            st.session_state["nifty_regime"]=nifty_regime
            st.session_state["nifty_signal"]=nifty_signal
            st.session_state["nifty_daily"]=nifty_daily
            for i,item in enumerate(universe):
                try:
                    end=datetime.now(IST).replace(tzinfo=None)
                    intra=client.intraday(item["security_id"],"NSE_EQ","EQUITY",interval,end-timedelta(days=lookback),end)
                    daily=client.daily(item["security_id"],"NSE_EQ","EQUITY",datetime.now(IST).date()-timedelta(days=daily_lookback))
                    diagnostics.append({"Stock":item["symbol"],"Intraday candles":len(intra),"Daily candles":len(daily),"Status":"Data OK" if len(intra)>=220 and len(daily)>=205 else "Insufficient history"})
                    srs=relative_strength(daily,nifty_daily) if not nifty_daily.empty else None
                    sig=evaluate_symbol(item["symbol"],intra,daily,nifty_regime,strategy=strategy,sector_relative_strength=srs,enforce_btst_window=False)
                    if sig:
                        sig["sector"]=item["sector"]
                        signals.append(sig)
                except Exception as e:
                    diagnostics.append({"Stock":item["symbol"],"Intraday candles":0,"Daily candles":0,"Status":str(e)[:500]})
                progress.progress((i+1)/max(len(universe),1))
        st.session_state["stock_signals"]=signals
        st.session_state["stock_diagnostics"]=diagnostics
    except Exception as e:
        st.error(f"Data/API error: {e}")

if "stock_signals" in st.session_state:
    regime=st.session_state.get("nifty_regime",{"regime":"UNKNOWN","reason":"NIFTY data unavailable"})
    st.subheader("NIFTY 50 Market Confirmation")
    st.metric("NIFTY Regime",regime.get("regime","UNKNOWN"))
    st.caption(regime.get("reason",""))
    ns=st.session_state.get("nifty_signal")
    if ns:
        st.success(f"NIFTY {ns['side']} setup • Score {ns['score']}/100 • Entry {ns['entry']:.2f} • SL {ns['stop_loss']:.2f} • Target {ns['target']:.2f}")
    else:
        st.info("NIFTY પર હાલ બધા confirmation મળ્યા નથી — index પર NO TRADE.")
    signals=st.session_state["stock_signals"]
    st.subheader("Strongest NSE Stock BTST / SWING Signal")
    if signals:
        # Keep exactly one highest-scoring qualifying stock; never mix weaker signals.
        best=max(signals,key=lambda x:(float(x.get("score",0)),float(x.get("risk_reward",0))))
        df=pd.DataFrame([{"Stock":best["symbol"],"Strategy":best["strategy"],"Side":best["side"],"Entry":round(best["entry"],2),"Stop Loss":round(best["stop_loss"],2),"Target":round(best["target"],2),"R:R":round(best["risk_reward"],2),"Score":best["score"],"Status":best.get("score_label","WATCHLIST"),"Sector":best.get("sector","")}])
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.write(best["reasons"])
        if best.get("score_label") in ("STRONG","QUALIFIED"):
            st.success(f"Qualified setup: {best['symbol']} | Entry ₹{best['entry']:.2f} | SL ₹{best['stop_loss']:.2f} | Target ₹{best['target']:.2f} | R:R 1:{best['risk_reward']:.2f}")
        else:
            st.warning(f"Best WATCHLIST candidate only: {best['symbol']} | Score {best['score']}/100 — not a confirmed trade.")
        st.caption(f"Selected as the strongest among {len(signals)} qualifying stock signals.")
        st.divider()
        st.caption("Stock and NIFTY BTST / SWING signals only. No option contract selection or order placement.")
    else:
        diagnostics = st.session_state.get("stock_diagnostics", [])
        all_data_failed = bool(diagnostics) and all(
            int(d.get("Intraday candles", 0) or 0) == 0
            and int(d.get("Daily candles", 0) or 0) == 0
            and str(d.get("Status", "")).startswith(("Dhan rejected", "Network error", "Dhan HTTP", "Dhan API"))
            for d in diagnostics
        )
        if all_data_failed:
            st.error("DATA ERROR — Dhan rejected the market-data requests for every stock. No strategy confirmation was possible. This is NOT a genuine NO TRADE result. Check the full per-stock error below before relying on this scan.")
        else:
            st.warning("No stock passed the confirmation rules in this scan. This is a genuine NO TRADE result, not an index signal.")
    if st.session_state.get("stock_diagnostics"):
        with st.expander("Per-stock data diagnostics"):
            st.dataframe(pd.DataFrame(st.session_state["stock_diagnostics"]),use_container_width=True,hide_index=True)



st.divider()
st.subheader("🧭 NIFTY OPTIONS — BTST CALL / PUT")
st.caption("NIFTY trend અને live option-chain liquidity પરથી CALL/PUT ઉમેદવાર પસંદ કરે છે. માત્ર screening; auto order નથી.")
if st.button("🔎 Scan NIFTY CALL & PUT", type="primary", key="scan_nifty_options"):
    try:
        client = _dhan_client()
        with st.spinner("NIFTY trend, expiry અને option chain તપાસી રહ્યા છીએ..."):
            end = datetime.now(IST).replace(tzinfo=None)
            nd = client.daily(nifty_id, "IDX_I", "INDEX", datetime.now(IST).date()-timedelta(days=daily_lookback))
            ni = client.intraday(nifty_id, "IDX_I", "INDEX", interval, end-timedelta(days=lookback), end)
            regime = market_regime(nd)
            from indicators import add_indicators
            ix = add_indicators(ni)
            direction = "NEUTRAL"
            if len(ix) >= 60:
                z = ix.iloc[-1]
                if z.close > z.ema20 > z.ema50 and z.rsi14 >= 50 and z.macd > z.macd_signal:
                    direction = "BULLISH"
                elif z.close < z.ema20 < z.ema50 and z.rsi14 <= 50 and z.macd < z.macd_signal:
                    direction = "BEARISH"
            if regime.get("regime") == direction:
                direction = "STRONG " + direction
            elif regime.get("regime") in ("BULLISH", "BEARISH") and direction in ("BULLISH", "BEARISH") and regime.get("regime") != direction:
                direction = "MIXED (daily/intraday conflict)"
            expiries = client.option_expiries(nifty_id, "IDX_I")
            today = datetime.now(IST).date()
            valid_expiries = []
            for value in expiries:
                try:
                    dt = datetime.strptime(str(value), "%Y-%m-%d").date()
                    if dt >= today:
                        valid_expiries.append(dt)
                except ValueError:
                    pass
            valid_expiries.sort()
            if not valid_expiries:
                raise ValueError("Dhan returned no current/future NIFTY expiry.")
            expiry = valid_expiries[0].isoformat()
            chain = client.option_chain(nifty_id, expiry, "IDX_I")
            data = chain.get("data", {}) if isinstance(chain, dict) else {}
            spot = float(data.get("last_price", 0) or 0)
            oc = data.get("oc", {})
            rows = []
            if not isinstance(oc, dict) or spot <= 0:
                raise ValueError("Dhan option-chain response has no valid spot/strike data.")
            for strike_text, pair in oc.items():
                try:
                    strike = float(strike_text)
                except (TypeError, ValueError):
                    continue
                if not isinstance(pair, dict):
                    continue
                for key, label in (("ce", "CALL"), ("pe", "PUT")):
                    leg = pair.get(key)
                    if not isinstance(leg, dict):
                        continue
                    bid = float(leg.get("top_bid_price", 0) or 0)
                    ask = float(leg.get("top_ask_price", 0) or 0)
                    ltp = float(leg.get("last_price", 0) or 0)
                    volume = int(leg.get("volume", 0) or 0)
                    oi = int(leg.get("oi", 0) or 0)
                    greeks = leg.get("greeks") or {}
                    try:
                        delta = float(greeks.get("delta", 0) or 0)
                    except (TypeError, ValueError):
                        delta = 0.0
                    mid = (bid+ask)/2 if bid > 0 and ask > 0 else 0
                    spread = (ask-bid)/mid if mid > 0 and ask >= bid else 999
                    abs_delta = abs(delta)
                    if bid > 0 and ask >= bid and ltp > 0 and volume > 0 and oi > 0 and int(leg.get("top_bid_quantity",0) or 0) > 0 and int(leg.get("top_ask_quantity",0) or 0) > 0 and spread <= 0.12 and 0.35 <= abs_delta <= 0.70:
                        score = 30 + min(volume/100000, 20) + min(oi/1000000, 20) + max(0, 20-abs(abs_delta-0.55)*100) + max(0, 10-spread*100)
                        entry_price = ask
                        stop_price = round(entry_price * 0.85, 2)
                        risk_per_unit = entry_price - stop_price
                        target1_price = round(entry_price + risk_per_unit, 2)
                        target2_price = round(entry_price + (2 * risk_per_unit), 2)
                        rows.append({"Type":label,"Strike":strike,"Expiry":expiry,"LTP":round(ltp,2),"Buy Price (Ask)":round(entry_price,2),"Stop Loss":stop_price,"Target 1":target1_price,"Target 2":target2_price,"Spread %":round(spread*100,2),"Delta":round(delta,3),"Volume":volume,"OI":oi,"Score":round(score,1),"Distance":abs(strike-spot),"Security ID":leg.get("security_id")})
            calls = sorted([r for r in rows if r["Type"]=="CALL"],key=lambda r:(-r["Score"],r["Distance"]))
            puts = sorted([r for r in rows if r["Type"]=="PUT"],key=lambda r:(-r["Score"],r["Distance"]))
            st.session_state["nifty_option_scan"] = {"spot":spot,"expiry":expiry,"direction":direction,"regime":regime,"calls":calls[:5],"puts":puts[:5]}
    except Exception as e:
        st.error(f"NIFTY option scan failed: {e}")

if "nifty_option_scan" in st.session_state:
    result = st.session_state["nifty_option_scan"]
    st.metric("NIFTY Spot", f"{result['spot']:.2f}")
    st.write(f"**Market direction:** {result['direction']} | **Daily regime:** {result['regime'].get('regime')} | **Expiry:** {result['expiry']}")
    left, right = st.columns(2)
    with left:
        st.markdown("**CALL candidates**")
        if result["calls"]: st.dataframe(pd.DataFrame(result["calls"]),use_container_width=True,hide_index=True)
        else: st.info("No CALL contract passed liquidity filters.")
    with right:
        st.markdown("**PUT candidates**")
        if result["puts"]: st.dataframe(pd.DataFrame(result["puts"]),use_container_width=True,hide_index=True)
        else: st.info("No PUT contract passed liquidity filters.")
    preferred = "CALL" if "BULLISH" in result["direction"] else "PUT" if "BEARISH" in result["direction"] else None
    best_list = result["calls"] if preferred=="CALL" else result["puts"] if preferred=="PUT" else []
    if best_list:
        best = best_list[0]
        st.success(f"BTST preference: {preferred} {best['Strike']} • BUY near ₹{best['Buy Price (Ask)']:.2f} • SL ₹{best['Stop Loss']:.2f} • T1 ₹{best['Target 1']:.2f} • T2 ₹{best['Target 2']:.2f} • Liquidity score {best['Score']}/100")
        st.caption("Risk model: 15% premium stop; Target 1 = 1R (+15%), Target 2 = 2R (+30%). Ask is indicative, not guaranteed fill. These are formula-based planning levels, not tested predictions; confirm live spread and risk before entry.")
    else:
        st.warning("NO CONFIRMED NIFTY BTST OPTION — trend neutral/mixed છે અથવા કોઈ contract liquidity checks પાસ કરતો નથી.")

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
