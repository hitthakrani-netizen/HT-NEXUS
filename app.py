"""HT NEXUS PRO - Streamlit dashboard. Read-only Dhan data; no order placement."""
import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from market_data import DhanDataClient, parse_universe
from strategy_engine import market_regime, evaluate_symbol, best_signal
from backtest import run_backtest

IST=ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="HT NEXUS", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container{padding-top:1.2rem;padding-bottom:2rem;max-width:1200px}
[data-testid="stMetric"]{background:rgba(120,130,150,.08);padding:12px;border-radius:12px}
.stButton>button{width:100%;border-radius:10px}
@media(max-width:700px){.block-container{padding-left:1rem;padding-right:1rem}}
</style>
""",unsafe_allow_html=True)
st.title("📈 HT NEXUS")
st.caption("BTST • Swing Trading • DhanHQ Data • Signals only — no automatic orders")

with st.sidebar:
    st.subheader("Configuration")
    client_id=st.text_input("Dhan Client ID", value=st.secrets.get("DHAN_CLIENT_ID", os.getenv("DHAN_CLIENT_ID","")))
    token=st.text_input("Dhan Access Token", value=st.secrets.get("DHAN_ACCESS_TOKEN", os.getenv("DHAN_ACCESS_TOKEN","")), type="password")
    strategy=st.selectbox("Strategy",["BTST","SWING"])
    interval=st.selectbox("Intraday candle interval (minutes)",["5","15","1"],index=0)
    lookback=st.slider("Intraday history (days)",7,90,30)
    daily_lookback=st.slider("Daily history (calendar days)",250,1500,500,step=50)
    nifty_id=st.text_input("NIFTY 50 Security ID (Dhan instrument master)",value="13",help="Verify this ID against Dhan's current instrument master.")
    capital=st.number_input("Backtest capital (₹)",min_value=10000,value=100000,step=10000)
    risk=st.slider("Risk per trade (%)",0.25,2.0,1.0,step=0.25)/100
    st.markdown("**Universe format:** `SYMBOL,SECURITY_ID,SECTOR`")
    universe_text=st.text_area("NSE symbols", value="RELIANCE,2885,ENERGY\nTCS,11536,IT\nINFY,1594,IT\nHDFCBANK,1333,BANK\nICICIBANK,4963,BANK\nSBIN,3045,BANK\nLT,11483,INDUSTRIALS\nITC,1660,FMCG\nAXISBANK,5900,BANK\nBHARTIARTL,10604,TELECOM",height=190)
    st.caption("Edit IDs/sectors against Dhan's current instrument master before live use.")
    st.markdown("**Sector index proxies** (`SECTOR,SECURITY_ID`)")
    sector_text = st.text_area("Sector proxies", value="", height=90,
                               help="Replace with verified Dhan index IDs, e.g. BANKNIFTY and NIFTY IT proxy IDs.")
    sector_map = {}
    for line in sector_text.splitlines():
        bits = [v.strip() for v in line.split(",")]
        if len(bits) >= 2 and bits[1]:
            sector_map[bits[0].upper()] = bits[1]

universe=parse_universe(universe_text)
col1,col2,col3=st.columns(3)
col1.metric("Strategy",strategy)
col2.metric("Universe",len(universe))
col3.metric("IST",datetime.now(IST).strftime("%d %b %Y, %H:%M:%S"))

if strategy=="BTST":
    now=datetime.now(IST).time()
    if not (datetime.strptime("15:15","%H:%M").time()<=now<=datetime.strptime("15:29","%H:%M").time()):
        st.warning("BTST signal window is 3:15 PM–3:29 PM IST. Outside this window the engine will not generate a BTST signal.")

if st.button("🔌 Test Dhan API Connection"):
    try:
        if not client_id or not token:
            st.error("Client ID અથવા Access Token ખાલી છે. Streamlit Secrets માં DHAN_CLIENT_ID અને DHAN_ACCESS_TOKEN સેટ કરો.")
        else:
            with st.spinner("Checking Dhan access token..."):
                profile = DhanDataClient(client_id, token).profile()
            st.success("Dhan API authentication successful.")
            st.write({
                "Dhan Client ID matches": str(profile.get("dhanClientId")) == str(client_id).strip(),
                "Token validity": profile.get("tokenValidity", "Not returned"),
                "Data API plan": profile.get("dataPlan", "Not returned"),
                "Data validity": profile.get("dataValidity", "Not returned"),
                "Active segments": profile.get("activeSegment", "Not returned"),
            })
            if str(profile.get("dhanClientId")) != str(client_id).strip():
                st.warning("આ Access Token અને Client ID એકબીજાના નથી. બંને એક જ Dhan accountના હોવા જોઈએ.")
            if str(profile.get("dataPlan", "")).lower() != "active":
                st.warning("Dhan profile મુજબ Data API plan Active નથી. Historical market data માટે Data API subscription જરૂરી હોઈ શકે છે.")
    except Exception as e:
        st.error(f"Dhan connection test failed: {e}")

if st.button("🔍 Scan Market",type="primary"):
    try:
        client=DhanDataClient(client_id,token)
        with st.spinner("Fetching Dhan candles and evaluating filters..."):
            nifty_daily=client.daily(nifty_id,exchange_segment="IDX_I",instrument="INDEX",
                                     from_date=datetime.now(IST).date()-timedelta(days=daily_lookback))
            regime=market_regime(nifty_daily)
            nifty_strength = (float(nifty_daily["close"].iloc[-1] / nifty_daily["close"].iloc[-21] - 1) * 100
                              if len(nifty_daily) >= 21 else None)
            st.subheader("Market Regime & Index Strength")
            st.write(f"**{regime['regime']}** — {regime['reason']}")
            if nifty_strength is not None:
                st.metric("NIFTY 20-session return", f"{nifty_strength:+.2f}%")
            sector_frames = {}
            sector_strength_rows = []
            for sector, sid in sector_map.items():
                try:
                    sf = client.daily(sid, exchange_segment="IDX_I", instrument="INDEX",
                                      from_date=datetime.now(IST).date()-timedelta(days=daily_lookback))
                    sector_frames[sector] = sf
                    ret = (float(sf["close"].iloc[-1] / sf["close"].iloc[-21] - 1) * 100
                           if len(sf) >= 21 else None)
                    sector_strength_rows.append({"Sector":sector, "20-session return %":ret})
                except Exception as se:
                    st.warning(f"Sector proxy {sector}: {se}")
            if sector_strength_rows:
                st.write("**Sector index strength**")
                st.dataframe(pd.DataFrame(sector_strength_rows), use_container_width=True, hide_index=True)
            candidates=[]
            progress=st.progress(0)
            for i,item in enumerate(universe):
                try:
                    end=datetime.now(IST).replace(tzinfo=None)
                    start=end-timedelta(days=lookback)
                    intra=client.intraday(item["security_id"],"NSE_EQ","EQUITY",interval,start,end)
                    daily=client.daily(item["security_id"],"NSE_EQ","EQUITY",
                                       datetime.now(IST).date()-timedelta(days=daily_lookback))
                    # Sector proxy data is not inferred from stock names. Relative strength is optional
                    # until user configures a validated sector-index instrument mapping.
                    relative_strength = None
                    if item["sector"] in sector_frames and len(sector_frames[item["sector"]]) >= 21 and len(daily) >= 21:
                        sf = sector_frames[item["sector"]]
                        stock_ret = daily["close"].iloc[-1] / daily["close"].iloc[-21] - 1
                        sector_ret = sf["close"].iloc[-1] / sf["close"].iloc[-21] - 1
                        relative_strength = float(stock_ret - sector_ret)
                    sig=evaluate_symbol(item["symbol"],intra,daily,regime,strategy=strategy,
                                        sector_relative_strength=relative_strength)
                    if sig: candidates.append(sig)
                except Exception as e:
                    st.warning(f"{item['symbol']}: {e}")
                progress.progress((i+1)/max(len(universe),1))
            chosen=best_signal(candidates)
            st.session_state["last_regime"]=regime
            st.session_state["last_candidates"]=candidates
            st.session_state["last_signal"]=chosen
    except Exception as e:
        st.error(f"Data/API error: {e}")
        st.info("Check Data API subscription, token validity, instrument IDs, and network connection.")

if "last_regime" in st.session_state:
    st.subheader("Best Confirmed Signal")
    chosen=st.session_state.get("last_signal")
    if not chosen:
        st.error("NO TRADE — No symbol passed every required confirmation filter.")
    else:
        a,b,c=st.columns(3)
        a.metric("Symbol / Side",f"{chosen['symbol']} / {chosen['side']}")
        b.metric("Entry (reference)",f"₹{chosen['entry']:.2f}")
        c.metric("Risk : Reward",f"1 : {chosen['risk_reward']:.2f}")
        d,e,f=st.columns(3)
        d.metric("Stop Loss",f"₹{chosen['stop_loss']:.2f}")
        e.metric("Target",f"₹{chosen['target']:.2f}")
        f.metric("Score",chosen["score"])
        st.write("**Confirmation:** "+chosen["reasons"])
        st.caption("Reference levels only. Confirm live quote, liquidity, spread, corporate actions and risk before any manual trade.")

with st.expander("Historical Backtesting"):
    st.write("Backtest one configured symbol using Dhan historical data. Results are estimates, not proof of future performance.")
    bt_symbol=st.selectbox("Backtest symbol",[x["symbol"] for x in universe] or ["RELIANCE"])
    bt_lookback=st.selectbox("Backtest intraday history (calendar days)",[30,60,90],index=2)
    if st.button("Run Backtest"):
        try:
            item=next(x for x in universe if x["symbol"]==bt_symbol)
            client=DhanDataClient(client_id,token)
            end=datetime.now(IST).replace(tzinfo=None)
            with st.spinner(f"Downloading {bt_lookback} days of {bt_symbol} historical candles..."):
                intra=client.intraday(item["security_id"],"NSE_EQ","EQUITY",interval,
                                      end-timedelta(days=bt_lookback),end)
                daily=client.daily(item["security_id"],"NSE_EQ","EQUITY",
                                   datetime.now(IST).date()-timedelta(days=daily_lookback))
            st.subheader("Historical Data Diagnostics")
            d1,d2,d3=st.columns(3)
            d1.metric("Intraday candles",len(intra))
            d2.metric("Daily candles",len(daily))
            d3.metric("Intraday interval",f"{interval} min")
            if not intra.empty:
                st.caption(f"Intraday range: {intra.index.min()} to {intra.index.max()}")
            if not daily.empty:
                st.caption(f"Daily range: {daily.index.min()} to {daily.index.max()}")
            if intra.empty or daily.empty:
                st.error("Dhan returned empty historical data. Check the Security ID, instrument type, date range and Data API access.")
            else:
                regime=market_regime(daily)
                st.write(f"**NIFTY-independent symbol test; market regime input:** {regime['regime']} — {regime['reason']}")
                result=run_backtest(bt_symbol,intra,daily,strategy,capital,risk)
                st.json(result["summary"])
                if result["summary"]["total_trades"] == 0:
                    st.warning("No historical trades passed every strategy filter in this period. This is not a profitable/losing result; extend the test window or inspect the strategy filters.")
                if not result["trades"].empty:
                    st.dataframe(result["trades"],use_container_width=True)
                    st.download_button("Download trades CSV",result["trades"].to_csv(index=False),
                                       file_name=f"{bt_symbol}_{strategy}_backtest.csv",mime="text/csv")
        except Exception as e:
            st.error(f"Backtest error: {e}")

with st.expander("Paper Trading Journal"):
    st.caption("Manual paper-trade logging is intentionally separate from Dhan order APIs.")
    with st.form("paper_form"):
        sym=st.text_input("Symbol")
        side=st.selectbox("Side",["LONG","SHORT"])
        entry=st.number_input("Entry",min_value=0.0,format="%.2f")
        stop=st.number_input("Stop Loss",min_value=0.0,format="%.2f")
        target=st.number_input("Target",min_value=0.0,format="%.2f")
        submitted=st.form_submit_button("Add paper trade")
    if submitted and sym and entry>0 and stop>0 and target>0:
        row={"time":datetime.now(IST).isoformat(),"symbol":sym.upper(),"side":side,
             "entry":entry,"stop":stop,"target":target,"status":"OPEN"}
        st.session_state.setdefault("paper_trades",[]).append(row)
    if st.session_state.get("paper_trades"):
        st.dataframe(pd.DataFrame(st.session_state["paper_trades"]),use_container_width=True)

st.divider()
st.caption("HT NEXUS PRO is an analytical tool, not investment advice. No accuracy or profit is guaranteed. Validate with clean out-of-sample data and realistic costs.")
