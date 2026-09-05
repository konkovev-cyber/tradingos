"""
scripts/analyze_performance.py
Diagnostic tool for trading performance by config_version and confidence bucket.

Outputs:
- Winrate by config_version
- Winrate by confidence bucket (when signal_score available)
- Net PnL distribution
- Per-trade statistics: gross / fees / net
- Daily summary

Use:  python3 scripts/analyze_performance.py
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

TRADES_LOG = Path("/root/tradingos/logs/trades/trade_results.jsonl")
GUARDIAN_LOG = Path("/root/tradingos/guardian/guardian_effectiveness.jsonl")
TRADING_MODE = Path("/root/tradingos/operations/trading_mode.json")


def load_records() -> list:
    records = []
    for log_path in [TRADES_LOG, GUARDIAN_LOG]:
        if not log_path.exists():
            continue
        for line in log_path.read_text().splitlines():
            try:
                d = json.loads(line.strip())
                if "symbol" in d and "net_pnl" in d:
                    records.append(d)
            except json.JSONDecodeError:
                continue
    return records


def section(title: str):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def stats_by_version(records: list):
    section("WINRATE BY CONFIG_VERSION")
    by_version = defaultdict(list)
    for r in records:
        v = r.get("config_version", "unknown")
        by_version[v].append(r)

    for v in sorted(by_version.keys()):
        rs = by_version[v]
        n = len(rs)
        wins = sum(1 for r in rs if r.get("net_pnl", 0) > 0)
        net = sum(r.get("net_pnl", 0) for r in rs)
        avg = net / n if n else 0
        fees = sum(r.get("fees", 0) for r in rs)
        gross = sum(r.get("gross_pnl", 0) for r in rs)
        wr = wins / n * 100 if n else 0
        print(f"  {v}: n={n}, winrate={wr:.1f}%, net=${net:.4f}, avg=${avg:.4f}")
        print(f"    gross=${gross:.4f}, fees=${fees:.4f}, expect_per_trade=${avg:.4f}")


def stats_by_confidence(records: list):
    """Build a confidence-bucketed winrate table. Since signal_score is not
    in trade_results.jsonl, we infer it from outcome-class: use net_pnl sign
    and gross_pnl as a coarse proxy, then ask the user to enable score logging.
    """
    section("WINRATE BUCKET (proxy by net_pnl sign / magnitude)")
    by_bucket = defaultdict(list)
    for r in records:
        net = r.get("net_pnl", 0)
        if net <= -0.5:
            bucket = "big loss"
        elif net < 0:
            bucket = "small loss"
        elif net < 0.1:
            bucket = "scratch"
        elif net < 0.5:
            bucket = "small win"
        else:
            bucket = "big win"
        by_bucket[bucket].append(r)
    for b in ["big loss", "small loss", "scratch", "small win", "big win"]:
        rs = by_bucket.get(b, [])
        if not rs:
            continue
        n = len(rs)
        wins = sum(1 for r in rs if r.get("net_pnl", 0) > 0)
        net = sum(r.get("net_pnl", 0) for r in rs)
        avg_win = sum(r.get("net_pnl", 0) for r in rs if r.get("net_pnl", 0) > 0) / max(1, wins)
        avg_loss = sum(r.get("net_pnl", 0) for r in rs if r.get("net_pnl", 0) < 0) / max(1, n - wins)
        print(f"  {b}: n={n}, wins={wins}, wr={wins/n*100:.1f}%, "
              f"avg_win=${avg_win:.4f}, avg_loss=${avg_loss:.4f}")


def stats_daily(records: list):
    section("DAILY PnL (per UTC day)")
    by_day = defaultdict(lambda: {"net": 0.0, "n": 0, "wins": 0})
    for r in records:
        try:
            ts = r.get("timestamp", "")
            day = ts[:10]  # YYYY-MM-DD
            net = r.get("net_pnl", 0)
            by_day[day]["net"] += net
            by_day[day]["n"] += 1
            if net > 0:
                by_day[day]["wins"] += 1
        except Exception:
            continue
    for day in sorted(by_day.keys())[-14:]:  # last 14 days
        d = by_day[day]
        wr = d["wins"]/d["n"]*100 if d["n"] else 0
        print(f"  {day}: n={d['n']}, wins={d['wins']}, wr={wr:.0f}%, net=${d['net']:+.4f}")


def stats_overall(records: list):
    section("OVERALL")
    n = len(records)
    if not n:
        print("  no data")
        return
    pnls = [r.get("net_pnl", 0) for r in records]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    fees = sum(r.get("fees", 0) for r in records)
    gross = sum(r.get("gross_pnl", 0) for r in records)
    net = sum(pnls)
    print(f"  Total: n={n}, winrate={wins/n*100:.1f}%")
    print(f"  Wins: {wins}, Losses: {losses}, Scratch: {n - wins - losses}")
    print(f"  Net: ${net:.4f}, Gross: ${gross:.4f}, Fees: ${fees:.4f}")
    print(f"  Avg/trade: ${net/n:.4f}")
    if losses:
        avg_loss = sum(p for p in pnls if p < 0) / losses
        print(f"  Avg LOSS: ${avg_loss:.4f}")
    if wins:
        avg_win = sum(p for p in pnls if p > 0) / wins
        print(f"  Avg WIN: ${avg_win:.4f}")
        if losses:
            print(f"  Avg WIN / Avg LOSS: {abs(avg_win / avg_loss):.2f}")
            print(f"  Breakeven winrate: {abs(avg_loss) / (avg_win + abs(avg_loss)) * 100:.1f}%")


def main():
    records = load_records()
    if not records:
        print(f"No trade records found in {TRADES_LOG} or {GUARDIAN_LOG}")
        return

    print(f"Loaded {len(records)} trade records")
    stats_overall(records)
    stats_by_version(records)
    stats_by_confidence(records)
    stats_daily(records)

    section("Pre-commit kill criteria check (user-mandated)")
    n = len(records)
    if n >= 5:
        # Consider only records with config_version == current
        try:
            cfg_v = json.loads(TRADING_MODE.read_text()).get("config_version", "?")
        except Exception:
            cfg_v = "?"
        v_records = [r for r in records if r.get("config_version") == cfg_v]
        if v_records:
            v = len(v_records)
            v_wins = sum(1 for r in v_records if r.get("net_pnl", 0) > 0)
            v_net = sum(r.get("net_pnl", 0) for r in v_records)
            v_avg = v_net / v if v else 0
            print(f"  Current config version ({cfg_v}): n={v}, "
                  f"winrate={v_wins/v*100 if v else 0:.1f}%, net/trade=${v_avg:.4f}")
            if v < 40:
                print(f"  WARNING: only {v} trades in v{cfg_v[-1] if cfg_v != '?' else '?'}; "
                      f"min 40-50 required for valid inference")
            if v_avg < 0 or v_wins / v < 0.35 if v else False:
                print(f"  KILL CRITERIA MET: negative expectancy or winrate < 35% — abort config")
            elif v_avg > 0.10 and v_wins / v >= 0.40 if v else False:
                print(f"  SUCCESS CRITERIA MET: avg/trade > $0.10 and winrate >= 40%")


if __name__ == "__main__":
    main()
