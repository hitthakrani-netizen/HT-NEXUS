"""Read-only Dhan stock option-chain screening for HT NEXUS."""
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def scan_stock_option_chain(client, symbol, security_id, bias=None):
    """Return liquid CE/PE candidates and transparent premium risk levels."""
    expiries = client.option_expiries(security_id, "NSE_FNO")
    today = datetime.now(IST).date()
    valid = []
    for value in expiries:
        try:
            expiry = datetime.strptime(str(value), "%Y-%m-%d").date()
            if expiry >= today:
                valid.append(expiry)
        except ValueError:
            continue
    if not valid:
        raise ValueError(f"{symbol}: no active stock-option expiry from Dhan; verify F&O eligibility and underlying ID.")
    expiry = min(valid).isoformat()
    chain = client.option_chain(security_id, expiry, "NSE_FNO")
    data = chain.get("data", {}) if isinstance(chain, dict) else {}
    spot = float(data.get("last_price", 0) or 0)
    oc = data.get("oc", {})
    if spot <= 0 or not isinstance(oc, dict):
        raise ValueError(f"{symbol}: Dhan returned no valid option-chain spot/strike data.")
    rows = []
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
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            spread = (ask - bid) / mid if mid > 0 and ask >= bid else 999
            depth_ok = int(leg.get("top_bid_quantity", 0) or 0) > 0 and int(leg.get("top_ask_quantity", 0) or 0) > 0
            if not (bid > 0 and ask >= bid and ltp > 0 and volume > 0 and oi > 0 and depth_ok and spread <= 0.12 and 0.30 <= abs(delta) <= 0.75):
                continue
            buy = ask
            stop = round(buy * 0.85, 2)
            risk = buy - stop
            t1 = round(buy + risk, 2)
            t2 = round(buy + 2 * risk, 2)
            score = 30 + min(volume / 100000, 20) + min(oi / 1000000, 20) + max(0, 20 - abs(abs(delta) - 0.55) * 100) + max(0, 10 - spread * 100)
            previous_oi = int(leg.get("previous_oi", 0) or 0)
            previous_volume = int(leg.get("previous_volume", 0) or 0)
            previous_close = float(leg.get("previous_close_price", 0) or 0)
            oi_change = oi - previous_oi if previous_oi > 0 else 0
            premium_change_pct = ((ltp / previous_close) - 1) * 100 if previous_close > 0 else 0.0
            bid_qty = int(leg.get("top_bid_quantity", 0) or 0)
            ask_qty = int(leg.get("top_ask_quantity", 0) or 0)
            depth_imbalance = ((bid_qty - ask_qty) / (bid_qty + ask_qty)) if bid_qty + ask_qty else 0.0
            rows.append({
                "Type": label, "Strike": strike, "Expiry": expiry, "LTP": round(ltp, 2),
                "BUY (Ask)": round(buy, 2), "SL": stop, "Target 1": t1, "Target 2": t2,
                "Spread %": round(spread * 100, 2), "Delta": round(delta, 3),
                "Volume": volume, "Previous Volume": previous_volume, "OI": oi,
                "Previous OI": previous_oi, "OI Change": oi_change,
                "Premium Change %": round(premium_change_pct, 2),
                "Bid Qty": bid_qty, "Ask Qty": ask_qty, "Depth Imbalance": round(depth_imbalance, 3),
                "Score": round(score, 1), "Distance": abs(strike - spot),
                "Security ID": leg.get("security_id"),
            })
    calls = sorted((r for r in rows if r["Type"] == "CALL"), key=lambda r: (-r["Score"], r["Distance"]))[:5]
    puts = sorted((r for r in rows if r["Type"] == "PUT"), key=lambda r: (-r["Score"], r["Distance"]))[:5]
    preferred = "CALL" if bias == "LONG" else "PUT" if bias == "SHORT" else None
    best_list = calls if preferred == "CALL" else puts if preferred == "PUT" else []
    near_strikes = sorted({r["Strike"] for r in rows}, key=lambda strike: abs(strike - spot))[:5]
    near_rows = [r for r in rows if r["Strike"] in near_strikes]
    def flow(side):
        group = [r for r in near_rows if r["Type"] == side]
        if not group:
            return {"volume":0,"oi_change":0,"premium_change":0.0,"depth":0.0}
        depth_total = sum(r["Bid Qty"] + r["Ask Qty"] for r in group)
        return {"volume":sum(r["Volume"] for r in group),
                "oi_change":sum(r["OI Change"] for r in group),
                "premium_change":round(sum(r["Premium Change %"] for r in group)/len(group),2),
                "depth":round(sum(r["Depth Imbalance"]*(r["Bid Qty"]+r["Ask Qty"]) for r in group)/depth_total,3) if depth_total else 0.0}
    call_flow, put_flow = flow("CALL"), flow("PUT")
    flow_bias = "CALL SIDE STRONGER" if call_flow["premium_change"] > put_flow["premium_change"] and call_flow["oi_change"] > 0 and call_flow["depth"] > 0 else "PUT SIDE STRONGER" if put_flow["premium_change"] > call_flow["premium_change"] and put_flow["oi_change"] > 0 and put_flow["depth"] > 0 else "MIXED / NO CLEAR EDGE"
    return {"symbol": symbol, "spot": spot, "expiry": expiry, "bias": bias,
            "calls": calls, "puts": puts, "preferred": preferred, "call_flow":call_flow,
            "put_flow":put_flow, "flow_bias":flow_bias, "near_strikes":near_strikes,
            "best": best_list[0] if best_list else None}
