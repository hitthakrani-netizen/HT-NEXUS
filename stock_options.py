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
            rows.append({
                "Type": label, "Strike": strike, "Expiry": expiry, "LTP": round(ltp, 2),
                "BUY (Ask)": round(buy, 2), "SL": stop, "Target 1": t1, "Target 2": t2,
                "Spread %": round(spread * 100, 2), "Delta": round(delta, 3),
                "Volume": volume, "OI": oi, "Score": round(score, 1),
                "Distance": abs(strike - spot), "Security ID": leg.get("security_id"),
            })
    calls = sorted((r for r in rows if r["Type"] == "CALL"), key=lambda r: (-r["Score"], r["Distance"]))[:5]
    puts = sorted((r for r in rows if r["Type"] == "PUT"), key=lambda r: (-r["Score"], r["Distance"]))[:5]
    preferred = "CALL" if bias == "LONG" else "PUT" if bias == "SHORT" else None
    best_list = calls if preferred == "CALL" else puts if preferred == "PUT" else []
    return {"symbol": symbol, "spot": spot, "expiry": expiry, "bias": bias,
            "calls": calls, "puts": puts, "preferred": preferred,
            "best": best_list[0] if best_list else None}
