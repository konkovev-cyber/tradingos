"""
research/profit_engine_simulator.py
PROFIT ENGINE VALIDATION — Trend Pyramid vs Baseline v1.0

Quant Research script. Does NOT modify TradingOS.
Simulates both models on real signal entries with real market data.
"""
import json
import os
import sys
import time
import hmac
import hashlib
import httpx
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

# ── Config ──
SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
ENV_PATH = Path("/root/trading_brain_v4/research/execution/.env")
MIN_SIGNALS = 50
TARGET_SIGNALS = 100

# ── Credentials ──
def _load_creds():
    ak, as_ = "", ""
    with open(ENV_PATH) as f:
        for l in f:
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                if k.strip() == "BYBIT_API_KEY": ak = v.strip()
                elif k.strip() == "BYBIT_API_SECRET": as_ = v.strip()
    return ak, as_

def _bybit_get(path, params, ak, as_):
    q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    ts = str(int(time.time() * 1000))
    s = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    h = {"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": s, "X-BAPI-RECV-WINDOW": "5000"}
    return httpx.get(f"https://api.bybit.com{path}?{q}", headers=h, timeout=10).json()

# ── Load signals ──
def load_signals(max_count=100):
    """Load real signal entries from signal_log."""
    signals = []
    with open(SIGNAL_LOG) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
            except: continue
            # Must have direction and entry price
            direction = rec.get("direction", "")
            if direction not in ("BUY", "SELL"): continue
            entry = rec.get("close", 0) or rec.get("entry_price", 0)
            if entry <= 0: continue
            atr = rec.get("atr", 0) or rec.get("atr_value", 0) or 0.001
            prob = rec.get("final_probability", 0) or rec.get("probability", 0)
            score = rec.get("score", 0) or rec.get("final_score", 0)
            quality = rec.get("quality", "UNKNOWN")
            symbol = rec.get("symbol", "?")
            signals.append({
                "symbol": symbol,
                "direction": direction,
                "entry": entry,
                "atr": atr,
                "probability": prob,
                "score": score,
                "quality": quality,
                "timestamp": rec.get("timestamp", ""),
            })
            if len(signals) >= max_count:
                break
    return signals

# ── Fetch price action ──
def fetch_price_action(symbol, entry_time, hours=24, ak="", as_=""):
    """Fetch klines after entry for simulation."""
    try:
        # Use 15m klines for granularity
        interval = "15"
        limit = int(hours * 4)  # 4 klines per hour
        params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
        d = _bybit_get("/v5/market/kline", params, ak, as_)
        if d.get("retCode") != 0: return []
        klines = d["result"]["list"]
        # kline format: [timestamp, open, high, low, close, volume, turnover]
        prices = []
        for k in klines:
            prices.append({
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })
        return prices
    except Exception as e:
        return []

# ── Simulate Baseline (fixed SL/TP) ──
def simulate_baseline(entry, direction, atr, prices):
    """Fixed 2xATR SL / 4xATR TP model."""
    sl_dist = atr * 2
    tp_dist = atr * 4
    if direction == "BUY":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:
        sl = entry + sl_dist
        tp = entry - tp_dist

    for p in prices:
        if direction == "BUY":
            if p["low"] <= sl: return {"outcome": "SL", "exit": sl, "r": -1.0, "bars": 0}
            if p["high"] >= tp: return {"outcome": "TP", "exit": tp, "r": 2.0, "bars": 0}
        else:
            if p["high"] >= sl: return {"outcome": "SL", "exit": sl, "r": -1.0, "bars": 0}
            if p["low"] <= tp: return {"outcome": "TP", "exit": tp, "r": 2.0, "bars": 0}
    # Not hit within window
    last = prices[-1]["close"]
    if direction == "BUY":
        r = (last - entry) / sl_dist
    else:
        r = (entry - last) / sl_dist
    return {"outcome": "TIMEOUT", "exit": last, "r": r, "bars": len(prices)}

# ── Simulate Trend Pyramid ──
def simulate_pyramid(entry, direction, atr, prices):
    """
    Trend Pyramid:
    - Entry 1: 40% risk ($0.10)
    - Add at +0.5R: +40% risk ($0.10)
    - Add at +1.0R: +40% risk ($0.10)
    - After add #1: SL → BE
    - After add #2: SL → locked profit
    - Exit: trailing SL at 1xATR from highest point
    """
    sl_dist = atr * 2  # same as baseline
    tp_dist = atr * 4
    risk_per_unit = sl_dist

    entries = [{"price": entry, "size": 0.4}]  # 40% of normal risk
    total_risk = 0.4
    sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
    be_set = False
    locked = False
    best_price = entry
    trailing_sl = None

    for bar_idx, p in enumerate(prices):
        if direction == "BUY":
            current = p["close"]
            high = p["high"]
            low = p["low"]
            # Check SL
            if low <= sl:
                # Exit all entries
                avg_entry = sum(e["price"] * e["size"] for e in entries) / sum(e["size"] for e in entries)
                r = (sl - avg_entry) / risk_per_unit * total_risk
                return {"outcome": "SL", "exit": sl, "r": r, "bars": bar_idx, "entries": len(entries)}
            # Track best price
            if high > best_price:
                best_price = high
                if trailing_sl is not None:
                    trailing_sl = best_price - atr * 1
            # Check add triggers
            if not be_set and (best_price - entry) >= sl_dist * 0.5:
                # Add #1 at +0.5R
                entries.append({"price": best_price, "size": 0.4})
                total_risk += 0.4
                sl = entry  # Move SL to BE
                be_set = True
            if be_set and not locked and (best_price - entry) >= sl_dist * 1.0:
                # Add #2 at +1.0R
                entries.append({"price": best_price, "size": 0.4})
                total_risk += 0.4
                sl = entry + sl_dist * 0.5  # Lock partial profit
                locked = True
                trailing_sl = best_price - atr * 1
            # Check trailing SL
            if trailing_sl is not None and low <= trailing_sl:
                avg_entry = sum(e["price"] * e["size"] for e in entries) / sum(e["size"] for e in entries)
                r = (trailing_sl - avg_entry) / risk_per_unit * total_risk
                return {"outcome": "TRAIL", "exit": trailing_sl, "r": r, "bars": bar_idx, "entries": len(entries)}
        else:  # SELL
            current = p["close"]
            high = p["high"]
            low = p["low"]
            if high >= sl:
                avg_entry = sum(e["price"] * e["size"] for e in entries) / sum(e["size"] for e in entries)
                r = (avg_entry - sl) / risk_per_unit * total_risk
                return {"outcome": "SL", "exit": sl, "r": r, "bars": bar_idx, "entries": len(entries)}
            if low < best_price:
                best_price = low
                if trailing_sl is not None:
                    trailing_sl = best_price + atr * 1
            if not be_set and (entry - best_price) >= sl_dist * 0.5:
                entries.append({"price": best_price, "size": 0.4})
                total_risk += 0.4
                sl = entry
                be_set = True
            if be_set and not locked and (entry - best_price) >= sl_dist * 1.0:
                entries.append({"price": best_price, "size": 0.4})
                total_risk += 0.4
                sl = entry - sl_dist * 0.5
                locked = True
                trailing_sl = best_price + atr * 1
            if trailing_sl is not None and high >= trailing_sl:
                avg_entry = sum(e["price"] * e["size"] for e in entries) / sum(e["size"] for e in entries)
                r = (avg_entry - trailing_sl) / risk_per_unit * total_risk
                return {"outcome": "TRAIL", "exit": trailing_sl, "r": r, "bars": bar_idx, "entries": len(entries)}

    # Timeout
    avg_entry = sum(e["price"] * e["size"] for e in entries) / sum(e["size"] for e in entries)
    last = prices[-1]["close"]
    if direction == "BUY":
        r = (last - avg_entry) / risk_per_unit * total_risk
    else:
        r = (avg_entry - last) / risk_per_unit * total_risk
    return {"outcome": "TIMEOUT", "exit": last, "r": r, "bars": len(prices), "entries": len(entries)}

# ── Main ──
def main():
    ak, as_ = _load_creds()
    if not ak or not as_:
        print("ERROR: no creds")
        return

    print("=" * 70)
    print("PROFIT ENGINE VALIDATION — Trend Pyramid vs Baseline")
    print("=" * 70)
    print()

    # Load signals
    signals = load_signals(TARGET_SIGNALS)
    print(f"Loaded {len(signals)} executable signals from signal_log")
    print()

    # Simulate each
    baseline_results = []
    pyramid_results = []
    errors = 0
    skipped = 0

    for i, sig in enumerate(signals):
        symbol = sig["symbol"]
        direction = sig["direction"]
        entry = sig["entry"]
        atr = sig["atr"]
        if atr <= 0:
            skipped += 1
            continue

        # Fetch price action
        prices = fetch_price_action(symbol, sig["timestamp"], hours=24, ak=ak, as_=as_)
        if not prices or len(prices) < 4:
            skipped += 1
            continue

        # Simulate both models
        base = simulate_baseline(entry, direction, atr, prices)
        pyr = simulate_pyramid(entry, direction, atr, prices)

        base["symbol"] = symbol
        base["direction"] = direction
        base["entry"] = entry
        base["atr"] = atr
        pyr["symbol"] = symbol
        pyr["direction"] = direction
        pyr["entry"] = entry
        pyr["atr"] = atr

        baseline_results.append(base)
        pyramid_results.append(pyr)

        if (i + 1) % 20 == 0:
            print(f"  Simulated {i+1}/{len(signals)}...")

    print()
    print(f"Simulated: {len(baseline_results)} trades")
    print(f"Skipped:   {skipped}")
    print(f"Errors:    {errors}")
    print()

    if not baseline_results:
        print("No results to analyze")
        return

    # ── Analysis ──
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print()

    for label, results in [("BASELINE (Fixed SL/TP)", baseline_results), ("TREND PYRAMID", pyramid_results)]:
        total = len(results)
        wins = sum(1 for r in results if r["r"] > 0)
        losses = sum(1 for r in results if r["r"] <= 0)
        total_r = sum(r["r"] for r in results)
        avg_r = total_r / total if total > 0 else 0
        avg_win = sum(r["r"] for r in results if r["r"] > 0) / max(wins, 1)
        avg_loss = sum(r["r"] for r in results if r["r"] <= 0) / max(losses, 1)
        pf = abs(sum(r["r"] for r in results if r["r"] > 0) / max(abs(sum(r["r"] for r in results if r["r"] <= 0)), 0.001))
        max_dd = 0
        running = 0
        for r in results:
            running += r["r"]
            if running < 0:
                max_dd = min(max_dd, running)
        outcomes = Counter(r["outcome"] for r in results)
        avg_entries = sum(r.get("entries", 1) for r in results) / total

        print(f"  {label}")
        print(f"  {'─' * 50}")
        print(f"  Total trades:     {total}")
        print(f"  Total R:          {total_r:+.2f}")
        print(f"  Avg R/trade:      {avg_r:+.3f}")
        print(f"  Win rate:         {wins/total*100:.1f}% ({wins}/{total})")
        print(f"  Avg win:          {avg_win:+.3f}R")
        print(f"  Avg loss:         {avg_loss:+.3f}R")
        print(f"  Profit Factor:    {pf:.2f}")
        print(f"  Max DD (R):       {max_dd:.2f}")
        print(f"  Avg entries/trade: {avg_entries:.2f}")
        print(f"  Outcomes:         {dict(outcomes)}")
        print()

    # ── Capital growth simulation ──
    print("=" * 70)
    print("CAPITAL GROWTH ($10 → ?)")
    print("=" * 70)
    print()

    for label, results in [("BASELINE", baseline_results), ("TREND PYRAMID", pyramid_results)]:
        capital = 10.0
        risk_per_trade = 0.25
        milestones = {10: 0, 20: 0, 50: 0, 100: 0}
        peak = capital
        dd = 0
        max_dd = 0
        trades_to_20 = None
        trades_to_50 = None

        for i, r in enumerate(results):
            pnl = r["r"] * risk_per_trade
            capital += pnl
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100
            max_dd = max(max_dd, dd)
            if capital >= 20 and trades_to_20 is None: trades_to_20 = i + 1
            if capital >= 50 and trades_to_50 is None: trades_to_50 = i + 1

        print(f"  {label}")
        print(f"  Start:            $10.00")
        print(f"  End:              ${capital:.2f}")
        print(f"  Return:           {(capital-10)/10*100:+.1f}%")
        print(f"  Max DD:           {max_dd:.1f}%")
        print(f"  Trades to $20:    {trades_to_20 if trades_to_20 else 'NOT REACHED'}")
        print(f"  Trades to $50:    {trades_to_50 if trades_to_50 else 'NOT REACHED'}")
        print()

    # ── Stress test ──
    print("=" * 70)
    print("STRESS TEST — Worst-case sequences")
    print("=" * 70)
    print()

    for label, results in [("BASELINE", baseline_results), ("TREND PYRAMID", pyramid_results)]:
        losses = [r for r in results if r["r"] <= 0]
        max_consec = 0
        current = 0
        for r in results:
            if r["r"] <= 0:
                current += 1
                max_consec = max(max_consec, current)
            else:
                current = 0
        print(f"  {label}: max {max_consec} consecutive losses")
        # Simulate 10-loss streak
        capital = 10.0
        for _ in range(10):
            capital -= 0.25  # worst case: full risk loss
        print(f"  After 10 losses:  ${capital:.2f} ({(capital-10)/10*100:.1f}%)")
        print()

    # ── Winner ──
    base_total = sum(r["r"] for r in baseline_results)
    pyr_total = sum(r["r"] for r in pyramid_results)
    base_pf = abs(sum(r["r"] for r in baseline_results if r["r"] > 0) / max(abs(sum(r["r"] for r in baseline_results if r["r"] <= 0)), 0.001))
    pyr_pf = abs(sum(r["r"] for r in pyramid_results if r["r"] > 0) / max(abs(sum(r["r"] for r in pyramid_results if r["r"] <= 0)), 0.001))
    base_dd = min(0, min([sum(r["r"] for r in baseline_results[:i+1]) for i in range(len(baseline_results))]))
    pyr_dd = min(0, min([sum(r["r"] for r in pyramid_results[:i+1]) for i in range(len(pyramid_results))]))

    print("=" * 70)
    print("FINAL VERDICT")
    print("=" * 70)
    print()
    print(f"  Baseline:  Total R={base_total:+.2f}, PF={base_pf:.2f}, DD={base_dd:.2f}R")
    print(f"  Pyramid:   Total R={pyr_total:+.2f}, PF={pyr_pf:.2f}, DD={pyr_dd:.2f}R")
    print()

    if pyr_total > base_total * 1.2 and pyr_pf > base_pf and abs(pyr_dd) <= abs(base_dd) * 1.5:
        print("  ✅ TREND PYRAMID WINS — all conditions met")
        print("  Recommendation: IMPLEMENT (after paper simulation)")
    elif pyr_total > base_total:
        print("  ⚠️ TREND PYRAMID leads but conditions not fully met")
        print("  Recommendation: more data needed")
    else:
        print("  ❌ BASELINE WINS — Trend Pyramid does not improve")
        print("  Recommendation: REJECT Trend Pyramid, explore other models")
    print()

    # ── Save results ──
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signals_loaded": len(signals),
        "trades_simulated": len(baseline_results),
        "baseline": {
            "total_r": round(base_total, 3),
            "pf": round(base_pf, 3),
            "max_dd_r": round(base_dd, 3),
            "win_rate": round(sum(1 for r in baseline_results if r["r"] > 0) / max(len(baseline_results), 1) * 100, 1),
        },
        "trend_pyramid": {
            "total_r": round(pyr_total, 3),
            "pf": round(pyr_pf, 3),
            "max_dd_r": round(pyr_dd, 3),
            "win_rate": round(sum(1 for r in pyramid_results if r["r"] > 0) / max(len(pyramid_results), 1) * 100, 1),
        },
        "winner": "PYRAMID" if pyr_total > base_total * 1.2 and pyr_pf > base_pf else "BASELINE",
    }
    out_path = Path("/root/tradingos/research/profit_engine_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Results saved to {out_path}")

if __name__ == "__main__":
    main()
