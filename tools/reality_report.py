#!/usr/bin/env python3
"""Reality Mode — summary report of current tradeable candidates."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Combined data from all scans
# Sources: opportunity_scan.py, signal_log.jsonl, historical backtests
CANDIDATES = {
    "AKEUSDT": {"prob": 0.540, "pf": 0.38, "wr": 27.3, "tr": 11, "adx": 54, "rsi": 52},
    "EULUSDT": {"prob": 0.510, "pf": 0.00, "wr": 0.0,  "tr": 0,  "adx": 0,  "rsi": 0},
    "MUUSDT":  {"prob": 0.490, "pf": 0.50, "wr": 33.3, "tr": 9,  "adx": 43, "rsi": 23},
    "BNBUSDT": {"prob": 0.480, "pf": 200.0,"wr": 100.0,"tr": 2,  "adx": 22, "rsi": 51},
    "BEATUSDT":{"prob": 0.450, "pf": 1.50, "wr": 60.0, "tr": 10, "adx": 20, "rsi": 50},
    "ETHUSDT": {"prob": 0.440, "pf": 2.00, "wr": 66.7, "tr": 6,  "adx": 32, "rsi": 45},
    "ADAUSDT": {"prob": 0.390, "pf": 5.00, "wr": 83.3, "tr": 6,  "adx": 35, "rsi": 45},
    "DOGEUSDT":{"prob": 0.330, "pf": 2.00, "wr": 66.7, "tr": 3,  "adx": 35, "rsi": 29},
}


def get_tier(d):
    if d["prob"] >= 0.50 and d["pf"] >= 1.5 and d["wr"] >= 55 and d["tr"] >= 5:
        return "A"
    if 0.45 <= d["prob"] < 0.50 and d["pf"] >= 1.5 and d["wr"] >= 55 and d["tr"] >= 5:
        return "B"
    if d["prob"] >= 0.45 and d["tr"] < 5:
        return "B-WATCH"
    return "C"


def score(d):
    return 0.40 * min(d["prob"]/0.55, 1.0) + 0.30 * min(d["pf"]/2.0, 1.0) + 0.20 * 0.8 + 0.10 * min(d.get("adx",0)/50, 1.0)


def main():
    print("=" * 80)
    print("REALITY MODE — TOP CANDIDATES")
    print("(Research Track continues at 0.55 unchanged)")
    print("=" * 80)

    items = sorted(CANDIDATES.items(), key=lambda x: -x[1]["prob"])
    tiers = {"A": 0, "B": 0, "B-WATCH": 0, "C": 0}

    header = f"{'Rank':>4s} {'Symbol':12s} {'Prob':>6s} {'PF':>6s} {'WR':>5s} {'Tr':>4s} {'ADX':>5s} {'Score':>6s} {'Tier':>10s} {'Action':>12s}"
    print(f"\n{header}")
    print("-" * 77)

    for rank, (sym, d) in enumerate(items, 1):
        s = round(score(d), 4)
        t = get_tier(d)
        tiers[t] = tiers.get(t, 0) + 1
        icon = {"A": "A", "B": "B", "B-WATCH": "B-W", "C": "C"}.get(t, "?")
        action = {"A": "TRADE $1", "B": "WATCH", "B-WATCH": "WATCH", "C": "WAIT"}.get(t, "")
        pf_str = f"{d['pf']:.2f}" if d['pf'] else "N/A"
        tr_str = f"{d['tr']:d}" if d['tr'] else "N"
        print(f"{rank:>4d} {sym:12s} {d['prob']:.3f} {pf_str:>6s} {d['wr']:>5.1f} {tr_str:>4s} {d.get('adx',0):>5.1f} {s:.4f} {icon:>10s} {action:>12s}")

    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"  Tier A (trade ready):       {tiers.get('A', 0)}")
    print(f"  Tier B ($0.50 risk):        {tiers.get('B', 0)}")
    print(f"  Tier B-WATCH (explore):     {tiers.get('B-WATCH', 0)}")
    print(f"  Tier C (no trade):          {tiers.get('C', 0)}")

    if tiers.get("A", 0) > 0:
        print(f"\n✅ TIER A TRADE PROPOSAL")
        for sym, d in items:
            if get_tier(d) == "A":
                print(f"  Symbol:       {sym}")
                print(f"  Probability:  {d['prob']:.4f}")
                print(f"  Historical:   PF={d['pf']:.2f} WR={d['wr']:.1f}%")
                print(f"  Risk:         $1 max loss")
                print(f"  Guardian:     REQUIRED (dry-run proven)")
                print(f"  Approval:     MANUAL REQUIRED")
    elif tiers.get("B", 0) > 0:
        print(f"\n📌 TIER B CANDIDATES — $0.50 risk trade possible")
        for sym, d in items:
            if get_tier(d) == "B":
                print(f"  {sym}: prob={d['prob']:.3f} PF={d['pf']:.2f} WR={d['wr']:.1f}%")
    elif tiers.get("B-WATCH", 0) > 0:
        print(f"\n👁 B-WATCH SYMBOLS — need more trade history")
        for sym, d in items:
            if get_tier(d) == "B-WATCH":
                print(f"  {sym}: prob={d['prob']:.3f} (only {d['tr']} historical trades, but PF={d['pf']:.2f})")
    else:
        max_prob = max(d["prob"] for d in CANDIDATES.values())
        max_pf = max((d["pf"] for d in CANDIDATES.values() if d["prob"] >= 0.45), default=0)
        print(f"\n❌ NO TRADE TODAY")
        print(f"   Max probability across all known symbols: {max_prob:.3f}")
        print(f"   Max PF among symbols with prob >= 0.45: {max_pf:.2f}")
        print(f"   Market-wide regime limitation: no pair simultaneously has")
        print(f"   high current probability AND high historical quality.")

    # Save
    report = {
        "time": str(__import__("datetime").datetime.now()),
        "tier_a": tiers.get("A", 0),
        "tier_b": tiers.get("B", 0),
        "tier_b_watch": tiers.get("B-WATCH", 0),
        "tier_c": tiers.get("C", 0),
        "top_candidates": [{"symbol": s, **d} for s, d in items],
    }
    path = ROOT / "docs" / "reports" / "REALITY_RANKING_REPORT.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print(f"\n  Report: {path}")
    print()


if __name__ == "__main__":
    main()
