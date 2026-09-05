"""
research/reality_data_recovery.py
REALITY GAP CLOSURE — Fix closed-pnl parsing and create proper trade records.
"""
import os, time, hmac, hashlib, httpx, json
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = Path("/root/trading_brain_v4/research/execution/.env")
TRADE_RESULTS = Path("/root/tradingos/logs/trades/trade_results.jsonl")
OUTPUT = Path("/root/tradingos/research/reality_trade_records_v2.json")

def load_creds():
    ak, as_ = "", ""
    with open(ENV_PATH) as f:
        for l in f:
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                if k.strip() == "BYBIT_API_KEY": ak = v.strip()
                elif k.strip() == "BYBIT_API_SECRET": as_ = v.strip()
    return ak, as_

def bybit_get(path, params, ak, as_):
    q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    ts = str(int(time.time() * 1000))
    s = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    h = {"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": s, "X-BAPI-RECV-WINDOW": "5000"}
    return httpx.get(f"https://api.bybit.com{path}?{q}", headers=h, timeout=10).json()

def parse_trade(item):
    """Parse a single closed-pnl record with correct field names."""
    symbol = item.get("symbol", "?")
    side = item.get("side", "?")
    direction = "SELL" if side == "Buy" else "BUY"  # FIX: Bybit returns closing order side, invert to get position direction
    
    entry = float(item.get("avgEntryPrice", 0) or 0)
    exit_p = float(item.get("avgExitPrice", 0) or 0)
    qty = float(item.get("qty", 0) or 0)  # NOT "size"
    closed_size = float(item.get("closedSize", 0) or 0)
    gross_pnl = float(item.get("closedPnl", 0) or 0)
    open_fee = float(item.get("openFee", 0) or 0)
    close_fee = float(item.get("closeFee", 0) or 0)
    total_fees = open_fee + close_fee
    net_pnl = gross_pnl - total_fees
    
    entry_time_ms = int(item.get("createdTime", 0))
    exit_time_ms = int(item.get("updatedTime", 0))
    entry_time = datetime.fromtimestamp(entry_time_ms / 1000, tz=timezone.utc).isoformat() if entry_time_ms else ""
    exit_time = datetime.fromtimestamp(exit_time_ms / 1000, tz=timezone.utc).isoformat() if exit_time_ms else ""
    holding_h = (exit_time_ms - entry_time_ms) / 3600000 if exit_time_ms > entry_time_ms else 0
    
    order_type = item.get("orderType", "?")
    exec_type = item.get("execType", "?")
    leverage = float(item.get("leverage", 1) or 1)
    order_id = item.get("orderId", "")
    cum_entry = float(item.get("cumEntryValue", 0) or 0)
    cum_exit = float(item.get("cumExitValue", 0) or 0)
    
    # Risk: SL distance * qty. If SL not in API, estimate from entry value
    risk_usd = cum_entry * 0.02  # 2% risk estimate (2x ATR)
    
    # R multiple
    if direction == "SELL":
        r = (entry - exit_p) * qty / max(risk_usd, 0.001)
    else:
        r = (exit_p - entry) * qty / max(risk_usd, 0.001)
    
    # Exit reason classification
    if gross_pnl > 0:
        if order_type == "Limit":
            exit_reason = "TP"
        elif order_type == "Market":
            exit_reason = "TP (market)"
        else:
            exit_reason = f"WIN ({order_type})"
    else:
        if order_type == "Stop":
            exit_reason = "SL"
        elif order_type == "Market":
            exit_reason = "SL (market)"
        else:
            exit_reason = f"LOSS ({order_type})"
    
    return {
        "trade_id": order_id,
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "exit_price": exit_p,
        "size": qty,
        "closed_size": closed_size,
        "gross_pnl": round(gross_pnl, 6),
        "open_fee": round(open_fee, 6),
        "close_fee": round(close_fee, 6),
        "total_fees": round(total_fees, 6),
        "net_pnl": round(net_pnl, 6),
        "risk_usd": round(risk_usd, 4),
        "R_multiple": round(r, 3),
        "entry_time": entry_time,
        "exit_time": exit_time,
        "holding_hours": round(holding_h, 1),
        "exit_reason": exit_reason,
        "order_type": order_type,
        "exec_type": exec_type,
        "leverage": leverage,
        "cum_entry_value": cum_entry,
        "cum_exit_value": cum_exit,
    }

def main():
    ak, as_ = load_creds()
    if not ak or not as_: return print("No creds")
    
    # Fetch ALL closed PnL
    all_items = []
    cursor = None
    while True:
        params = {"category": "linear", "limit": 100, "settleCoin": "USDT"}
        if cursor: params["cursor"] = cursor
        d = bybit_get("/v5/position/closed-pnl", params, ak, as_)
        if d.get("retCode") != 0: break
        items = d["result"].get("list", [])
        if not items: break
        all_items.extend(items)
        cursor = d["result"].get("nextPageCursor", "")
        if not cursor: break
    
    print(f"Fetched {len(all_items)} raw records from Bybit")
    
    # Parse each
    trades = [parse_trade(item) for item in all_items]
    
    # Save
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(trades, f, indent=2)
    print(f"Saved {len(trades)} parsed trades to {OUTPUT}")
    print()
    
    # ── REPORT ──
    print("=" * 70)
    print("REALITY TRADE RECORDS v2 — REPORT")
    print("=" * 70)
    print()
    
    total = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    total_pnl = sum(t["net_pnl"] for t in trades)
    total_fees = sum(t["total_fees"] for t in trades)
    avg_hold = sum(t["holding_hours"] for t in trades) / max(total, 1)
    
    print(f"Total trades:     {total}")
    print(f"Wins:             {len(wins)}")
    print(f"Losses:           {len(losses)}")
    print(f"Win rate:         {len(wins)/max(total,1)*100:.1f}%")
    print(f"Gross PnL:        ${sum(t['gross_pnl'] for t in trades):.4f}")
    print(f"Total fees:       ${total_fees:.4f}")
    print(f"Net PnL:          ${total_pnl:.4f}")
    print(f"Avg holding:      {avg_hold:.1f}h")
    print(f"Avg R:            {sum(t['R_multiple'] for t in trades)/max(total,1):.3f}")
    print()
    
    # By exit reason
    from collections import Counter
    reasons = Counter(t["exit_reason"] for t in trades)
    print("Exit reasons:")
    for r, c in reasons.most_common():
        print(f"  {r}: {c}")
    print()
    
    # By symbol
    print("By symbol:")
    syms = {}
    for t in trades:
        s = t["symbol"]
        if s not in syms: syms[s] = {"n": 0, "pnl": 0, "w": 0}
        syms[s]["n"] += 1
        syms[s]["pnl"] += t["net_pnl"]
        if t["net_pnl"] > 0: syms[s]["w"] += 1
    for sym, st in sorted(syms.items(), key=lambda x: -x[1]["pnl"]):
        print(f"  {sym:15s} {st['n']:2d} trades  {st['w']}W  PnL=${st['pnl']:+.4f}")
    print()
    
    # Data quality check
    print("Data quality:")
    bad_size = sum(1 for t in trades if t["size"] == 0)
    bad_pnl = sum(1 for t in trades if t["gross_pnl"] == 0)
    bad_reason = sum(1 for t in trades if "?" in t["exit_reason"])
    print(f"  Size=0:       {bad_size}")
    print(f"  PnL=0:        {bad_pnl}")
    print(f"  Unknown exit: {bad_reason}")
    print(f"  Quality:      {'PASS' if bad_size == 0 and bad_pnl == 0 else 'FAIL'}")
    print()
    
    # Save to trade_results.jsonl in new format
    print("Saving to trade_results.jsonl (v2 format)...")
    count = 0
    with open(TRADE_RESULTS, "w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")
            count += 1
    print(f"Written {count} records to {TRADE_RESULTS}")
    print()
    
    # Simulation comparison
    print("=" * 70)
    print("SIMULATION vs REALITY (v2 data)")
    print("=" * 70)
    print()
    
    sim_pf = 2.43
    sim_wr = 52.0
    real_pf = abs(sum(t['net_pnl'] for t in wins) / max(abs(sum(t['net_pnl'] for t in losses)), 0.0001))
    real_wr = len(wins) / max(total, 1) * 100
    
    print(f"{'Metric':<20} {'Simulation':<15} {'Reality':<15} {'Gap':<15}")
    print("-" * 65)
    print(f"{'Trades':<20} {100:<15} {total:<15} {total-100:<+15}")
    print(f"{'Win Rate':<20} {sim_wr:<15.1f} {real_wr:<15.1f} {real_wr-sim_wr:<+15.1f}")
    print(f"{'Profit Factor':<20} {sim_pf:<15.2f} {real_pf:<15.2f} {real_pf-sim_pf:<+15.2f}")
    print(f"{'Net PnL ($)':<20} {'+8.75':<15} {total_pnl:<+15.4f} {total_pnl-8.75:<+15.4f}")
    print()
    
    # Decision
    print("=" * 70)
    print("CEO DECISION")
    print("=" * 70)
    print()
    if total < 30:
        print(f"  ⚠️ Only {total} trades — insufficient for conclusion")
        print("  Decision: CONTINUE collecting data")
    elif real_pf > 1.3:
        print("  ✅ PF > 1.3 — strategy hypothesis SUPPORTED")
        print("  Decision: CONTINUE current model")
    elif real_pf > 0.8:
        print("  ⚠️ PF 0.8-1.3 — investigate execution/filters")
        print("  Decision: ANALYZE, do NOT change strategy")
    else:
        print("  ❌ PF < 0.8 — strategy hypothesis WEAKENED")
        print("  Decision: ANALYZE cause, do NOT change strategy yet")
    print()
    print(f"  Next gate: 30 trades → first Expectancy estimate")
    print(f"  Next gate: 50 trades → VALIDATION REVIEW")

if __name__ == "__main__":
    main()
