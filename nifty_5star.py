"""Faithful Python port of the user's supplied NIFTY 5-STAR Pine indicator.

Pine inputs preserved: ATR 10, Supertrend factor 3, EMA 21, VWMA 20,
3-minute volume threshold 125,000, bar-close confirmation, 09:31-15:00 IST,
and first signal per trading day. HT NEXUS uses 5-minute NIFTY candles as the
chart timeframe; 3-minute volume is separately resampled from Dhan 1-minute data.
"""
from __future__ import annotations
import pandas as pd
import numpy as np

def _vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    day = pd.Series(df.index.date, index=df.index)
    pv = typical * vol
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = vol.groupby(day).cumsum().replace(0, np.nan)
    return cum_pv / cum_vol

def _supertrend_source(high, low, close, period=10, factor=3.0):
    prev_close = close.shift(1)
    tr = pd.concat([(high-low), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    mid = (high + low) / 2.0
    upper = mid + factor * atr
    lower = mid - factor * atr
    st = pd.Series(np.nan, index=close.index, dtype=float)
    direction = pd.Series(False, index=close.index, dtype=bool)
    for i in range(len(close)):
        if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]):
            continue
        if i == 0 or pd.isna(st.iloc[i-1]):
            st.iloc[i] = lower.iloc[i] if close.iloc[i] > upper.iloc[i] else upper.iloc[i]
            direction.iloc[i] = bool(close.iloc[i] > upper.iloc[i])
        elif close.iloc[i] > st.iloc[i-1]:
            st.iloc[i] = max(lower.iloc[i], st.iloc[i-1])
            direction.iloc[i] = True
        else:
            st.iloc[i] = min(upper.iloc[i], st.iloc[i-1])
            direction.iloc[i] = False
    return st, direction

def evaluate_nifty_5star(chart_5m: pd.DataFrame, volume_1m: pd.DataFrame,
                         min_3m_volume: int = 125000) -> dict:
    """Return the first confirmed daily Pine-equivalent signal, if any."""
    required = {"open","high","low","close","volume"}
    if chart_5m is None or chart_5m.empty or not required.issubset(chart_5m.columns):
        return {"status":"DATA_UNAVAILABLE","reason":"NIFTY 5-minute candles missing."}
    if volume_1m is None or volume_1m.empty or "volume" not in volume_1m:
        return {"status":"DATA_UNAVAILABLE","reason":"NIFTY 1-minute candles required for 3-minute volume filter."}
    df = chart_5m.copy().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    vol = volume_1m.copy().sort_index()
    vol = vol[~vol.index.duplicated(keep="last")]
    # Normalize both Dhan candle indexes to IST before resampling/alignment.
    try:
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index))
        vol.index = pd.DatetimeIndex(pd.to_datetime(vol.index))
        df.index = df.index.tz_localize("Asia/Kolkata") if df.index.tz is None else df.index.tz_convert("Asia/Kolkata")
        vol.index = vol.index.tz_localize("Asia/Kolkata") if vol.index.tz is None else vol.index.tz_convert("Asia/Kolkata")
    except Exception as exc:
        return {"status":"DATA_UNAVAILABLE","reason":f"Invalid candle timestamps: {exc}"}
    df = df[~df.index.duplicated(keep="last")]
    vol = vol[~vol.index.duplicated(keep="last")]
    raw_volume = pd.to_numeric(vol["volume"], errors="coerce").fillna(0.0)
    if raw_volume.empty or float(raw_volume.abs().sum()) <= 0:
        return {"status":"DATA_UNAVAILABLE","reason":"NIFTY 50 Spot 1-minute volume is zero/unavailable from Dhan; the 5-STAR volume condition cannot be verified."}
    # Aggregate actual NIFTY 50 Spot 1-minute volume into completed 3-minute buckets.
    v3 = raw_volume.resample("3min", origin="start_day", label="right", closed="left").sum()
    v3 = v3[v3.index.time <= pd.Timestamp("15:00").time()]
    # Map the latest completed 3-minute bucket to each completed 5-minute chart bar.
    df["vol3m"] = v3.reindex(df.index + pd.Timedelta(minutes=5), method="ffill")
    if df["vol3m"].notna().sum() == 0:
        return {"status":"DATA_UNAVAILABLE","reason":"NIFTY 50 Spot 1-minute volume timestamps do not overlap the 5-minute candles."}
    df["ema21"] = df["close"].ewm(span=21, adjust=False, min_periods=21).mean()
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol_chart = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df["vwap"] = _vwap(df)
    df["vwma20"] = (df["close"] * vol_chart).rolling(20, min_periods=20).sum() / vol_chart.rolling(20, min_periods=20).sum().replace(0,np.nan)
    df["supertrend"], df["st_up"] = _supertrend_source(df["high"],df["low"],df["close"],10,3.0)
    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    df["body_pct"] = (df["close"] - df["open"]).abs() / candle_range
    df["close_location"] = (df["close"] - df["low"]) / candle_range
    prior_avg_volume = vol_chart.shift(1).rolling(20, min_periods=20).mean()
    df["relative_volume"] = vol_chart / prior_avg_volume.replace(0, np.nan)
    strong_buy_candle = (df["body_pct"] >= 0.60) & (df["close_location"] >= 0.80)
    strong_sell_candle = (df["body_pct"] >= 0.60) & (df["close_location"] <= 0.20)
    volume_confirmed = (df["relative_volume"] >= 1.50) & (df["vol3m"] >= min_3m_volume)
    df["buy"] = (df["close"]>df["ema21"]) & (df["close"]>df["vwap"]) & (df["close"]>df["vwma20"]) & (df["close"]>df["supertrend"]) & volume_confirmed & strong_buy_candle
    df["sell"] = (df["close"]<df["ema21"]) & (df["close"]<df["vwap"]) & (df["close"]<df["vwma20"]) & (df["close"]<df["supertrend"]) & volume_confirmed & strong_sell_candle
    # Keep only fully closed candles and the script's 09:31-15:00 time window.
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    df = df[(df.index.time >= pd.Timestamp("09:31").time()) & (df.index.time <= pd.Timestamp("15:00").time())]
    df = df[df.index < now.floor("5min")]
    if df.empty:
        return {"status":"NO_SIGNAL","reason":"No completed NIFTY candles inside the indicator's 09:31-15:00 window."}
    # Pine allows only the first signal of each day.
    candidates=[]
    for day, group in df.groupby(df.index.date):
        mask = group["buy"] | group["sell"]
        hit = group[mask]
        if not hit.empty:
            row = hit.iloc[0]
            ts = hit.index[0]
            side = "CALL" if bool(row["buy"]) else "PUT"
            entry=float(row["close"]); stop=float(row["supertrend"])
            risk=entry-stop if side=="CALL" else stop-entry
            target=entry+risk if side=="CALL" else entry-risk
            if risk>0:
                candidates.append({"date":str(day),"time":ts.strftime("%H:%M:%S"),"side":side,
                    "entry":round(entry,2),"stop_loss":round(stop,2),"target1":round(target,2),
                    "target2":round(entry+(2*risk if side=="CALL" else -2*risk),2),
                    "volume_3m":int(row["vol3m"]),"relative_volume":round(float(row["relative_volume"]),2),"candle_body_pct":round(float(row["body_pct"]),2),"ema21":round(float(row["ema21"]),2),
                    "vwap":round(float(row["vwap"]),2),"vwma20":round(float(row["vwma20"]),2),
                    "supertrend":round(stop,2),"signal_timeframe":"5m chart + 3m volume"})
    today = now.date()
    today_hits=[x for x in candidates if x["date"]==str(today)]
    if not today_hits:
        last=df.iloc[-1]
        return {"status":"NO_SIGNAL","reason":"No 5-STAR BUY/SELL condition confirmed today. This is not a Dhan/NSE data error.",
                "last_close":round(float(last["close"]),2),"last_3m_volume":int(last["vol3m"]) if pd.notna(last["vol3m"]) else 0}
    return {"status":"SIGNAL","signal":today_hits[0],"reason":"First confirmed signal of the day: strong candle body/close + 1.5x relative volume + supplied Pine filters."}
