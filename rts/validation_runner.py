"""
rts/validation_runner.py
RTS Profit Validation — historical replay on XAUUSD M5.
Simulates basket management with realistic costs, SL, and expiry.
"""
import csv, json, sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

LOGS_DIR = Path("/root/tradingos/logs/rts_validation")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
JOURNAL_PATH = LOGS_DIR / "rts_validation.jsonl"
REPORT_PATH = Path("/root/tradingos/reports/rts_validation_report.md")

COMMISSION = 0.0006
SPREAD = 0.00018
SLIPPAGE = 0.0001


def load_csv(path: str) -> list:
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("open"):
                continue
            rows.append({
                "time": row["time"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
    return rows


def simulate_basket(rows: list, idx: int, direction: str, atr: float):
    """Run one RTS basket. Returns trade dict or None."""
    side = 1 if direction == "LONG" else -1
    entry = rows[idx]["close"]
    sizes = [0.01, 0.013, 0.016]
    max_lvl = 3
    entries = [{"p": entry, "q": sizes[0]}]
    cost = entry * sizes[0] * COMMISSION
    lvl = 1
    last_add = entry
    add_dist = atr * 0.5

    for i in range(idx + 1, min(idx + 100, len(rows))):
        c = rows[i]["close"]
        avg = sum(e["p"] * e["q"] for e in entries) / sum(e["q"] for e in entries)
        qty = sum(e["q"] for e in entries)
        pnl = (c - avg) * qty * side
        pnl_pct = pnl / (avg * qty) * 100

        # Stop loss at -0.5%
        if pnl_pct < -0.5:
            net = round(pnl - cost - c * qty * COMMISSION - c * qty * SLIPPAGE - SPREAD * qty, 2)
            return {"direction": direction, "entry": round(entry, 2), "adds": lvl - 1,
                    "avg": round(avg, 4), "exit": round(c, 2), "pnl": net,
                    "bars": i - idx, "result": "LOSS"}

        # Add level on pullback
        if lvl < max_lvl and (c - last_add) * side < -add_dist:
            last_add = c
            aq = sizes[lvl]
            cost += c * aq * COMMISSION
            entries.append({"p": c, "q": aq})
            lvl += 1
            continue

        # Basket TP at +0.3%
        if pnl_pct > 0.3:
            net = round(pnl - cost - c * qty * COMMISSION - c * qty * SLIPPAGE - SPREAD * qty, 2)
            return {"direction": direction, "entry": round(entry, 2), "adds": lvl - 1,
                    "avg": round(avg, 4), "exit": round(c, 2), "pnl": net,
                    "bars": i - idx, "result": "WIN"}

    # Expired — no TP/SL hit in 100 bars
    last = rows[min(idx + 99, len(rows) - 1)]["close"]
    avg = sum(e["p"] * e["q"] for e in entries) / sum(e["q"] for e in entries)
    qty = sum(e["q"] for e in entries)
    final_pnl = (last - avg) * qty * side
    final_net = round(final_pnl - cost - last * qty * COMMISSION - last * qty * SLIPPAGE - SPREAD * qty, 2)
    return {"direction": direction, "entry": round(entry, 2), "adds": lvl - 1,
            "avg": round(avg, 4), "exit": round(last, 2), "pnl": final_net,
            "bars": 100, "result": "WIN" if final_net > 0 else "LOSS"}


def run():
    rows = load_csv("/root/mt5_trading_bot/data/XAUUSD_M5_full.csv")
    rows = rows[-25920:]  # last 90 days
    print(f"Loaded {len(rows)} candles (90 days XAUUSD M5)")

    # Rolling ATR
    atrs = []
    for i in range(14, len(rows)):
        tr = max(rows[i]["high"] - rows[i]["low"],
                 abs(rows[i]["high"] - rows[i-1]["close"]),
                 abs(rows[i]["low"] - rows[i-1]["close"]))
        atrs.append(tr)
    avg_atr = sum(atrs) / len(atrs) if atrs else 1

    trades = []
    for i in range(50, len(rows) - 100, 6):
        bar = rows[i]
        low20 = min(rows[j]["low"] for j in range(i - 20, i))
        high20 = max(rows[j]["high"] for j in range(i - 20, i))
        atr = atrs[i - 15] if i - 15 < len(atrs) else avg_atr

        if bar["close"] <= low20 * 1.001:
            t = simulate_basket(rows, i, "LONG", atr)
            if t: trades.append(t)
        elif bar["close"] >= high20 * 0.999:
            t = simulate_basket(rows, i, "SHORT", atr)
            if t: trades.append(t)

        if len(trades) >= 100:
            break

    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    tot = len(trades)
    wr = len(wins) / tot * 100 if tot else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = round(gross_profit / gross_loss, 2) if gross_loss else 0
    net = round(sum(t["pnl"] for t in trades), 2)
    max_dd = round(min(t["pnl"] for t in trades), 2) if trades else 0
    avg_adds = round(sum(t["adds"] for t in trades) / tot, 1) if tot else 0
    avg_pnl = round(sum(t["pnl"] for t in trades) / tot, 4) if tot else 0

    status = "✅ PROCEED TO DEMO" if pf > 1.2 else ("⚠️  NEEDS_FILTER" if pf > 0.9 else "❌ STOP")

    # Save journal
    with JOURNAL_PATH.open("w") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")

    # Report
    r = f"""# RTS Validation Report #001

**Data:** XAUUSD M5 (90 days)
**Engine:** RTS Basket v1 (max 3 levels, ATR-based)

## Results

| Metric | Value |
|--------|-------|
| Trades | {tot} |
| Winrate | {wr:.1f}% |
| PF | {pf:.2f} |
| Net PnL | ${net:.2f} |
| Max DD | ${max_dd:.2f} |
| Avg PnL | ${avg_pnl:.2f} |
| Avg adds | {avg_adds} |
| Avg duration | {sum(t['bars'] for t in trades)/tot:.0f} bars |

## Long vs Short
| | LONG | SHORT |
|--|------|-------|
| Trades | {len([t for t in trades if t['direction']=='LONG'])} | {len([t for t in trades if t['direction']=='SHORT'])} |
| Winrate | {sum(1 for t in trades if t['direction']=='LONG' and t['result']=='WIN')/max(len([t for t in trades if t['direction']=='LONG']),1)*100:.0f}% | {sum(1 for t in trades if t['direction']=='SHORT' and t['result']=='WIN')/max(len([t for t in trades if t['direction']=='SHORT']),1)*100:.0f}% |

## Decision

**{status}**"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(r)

    print(f"\n{'='*50}")
    print(f"  RTS VALIDATION #001")
    print(f"{'='*50}")
    print(f"  Trades:  {tot}  |  WR: {wr:.0f}%  |  PF: {pf}")
    print(f"  Net:     ${net:.2f}  |  DD: ${max_dd:.2f}")
    print(f"  Adds:    {avg_adds}  |  Avg PnL: ${avg_pnl:.2f}")
    print(f"  Status:  {status}")
    print(f"  Report:  {REPORT_PATH}")

if __name__ == "__main__":
    run()
