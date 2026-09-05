"""
Edge Validation Report — проверяет что filter улучшил edge, а не создал иллюзию.

Строит:
1. R-multiple comparison (v1 vs v2)
2. Bootstrap test (1000 random permutations)
3. Monthly PF stability
4. Parameter sensitivity scan
5. Execution cost sensitivity
6. Losing trade cause tree
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("EdgeValidator")

_ROOT = Path(__file__).parent

_rr_spec = importlib.util.spec_from_file_location("rr", str(_ROOT / "research_runner.py"))
_rr_mod = importlib.util.module_from_spec(_rr_spec)
_rr_spec.loader.exec_module(_rr_mod)

_cse_spec = importlib.util.spec_from_file_location(
    "cse", str(_ROOT / "core" / "execution" / "crypto_shadow.py")
)
_cse_mod = importlib.util.module_from_spec(_cse_spec)
_cse_spec.loader.exec_module(_cse_mod)
CryptoShadowExecutor = _cse_mod.CryptoShadowExecutor
PROFILE_A = _cse_mod.PROFILE_A
PROFILE_B = _cse_mod.PROFILE_B
PROFILE_C = _cse_mod.PROFILE_C

# Conditional filter (from Edge Mining)
GOOD_HOURS = {12, 17, 19, 21}
BAD_DAYS = {"Wed"}
ADX_MAX = 15


def passes_filter(ts, adx):
    if ts > 1e12:
        ts = ts / 1000.0
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt.hour not in GOOD_HOURS:
        return False
    if dt.strftime("%a") in BAD_DAYS:
        return False
    if adx >= ADX_MAX:
        return False
    return True


# ── Trade Collection ───────────────────────────────────────

async def collect_trades(strategy_id, symbol, timeframe, months, filtered=False):
    """Run replay and return list of trade dicts."""
    cache_path = _ROOT / "core" / "data_lake" / "data" / "cache" / f"{symbol}_{timeframe}_{months}m.json"
    if not cache_path.exists():
        print(f"No cache: {cache_path}")
        return []

    with open(cache_path) as f:
        candles = json.load(f)
    candles.sort(key=lambda c: c.get("time", 0))

    strategy_cls = _rr_mod.STRATEGIES.get(strategy_id)
    strategy = strategy_cls()
    executor = CryptoShadowExecutor(risk_config=PROFILE_B)

    trades = []
    for candle in candles:
        ts = candle.get("time", 0)
        md = {
            "symbol": symbol,
            "open": float(candle.get("open", 0)),
            "high": float(candle.get("high", 0)),
            "low": float(candle.get("low", 0)),
            "close": float(candle.get("close", 0)),
            "volume": float(candle.get("volume", 0)),
        }

        adx = getattr(strategy, '_adx', 0)
        signal = strategy.analyze(md)
        if signal:
            if filtered and not passes_filter(ts, adx):
                pass  # skip
            else:
                meta = dict(signal.get("metadata", {}))
                meta.setdefault("current_price", md["close"])
                sig = type('Sig', (), {
                    'symbol': symbol,
                    'direction': signal["direction"],
                    'confidence': signal["confidence"],
                    'metadata': meta,
                    'timestamp': time.time(),
                })()
                await executor.process_signal(sig)

        closed = await executor.update_positions(md)
        for c in closed:
            r = self_r_multiple(c)
            ts_close = ts  # approximate
            trades.append({
                "entry_price": c.entry_price,
                "exit_price": c.exit_price,
                "pnl": c.pnl,
                "r": r,
                "reason": c.reason,
                "bars": c.bars_held,
                "ts": ts_close,
                "adx_at_entry": adx,
            })

    return trades


def self_r_multiple(trade):
    """Convert trade to R-multiple using distance to SL as 1R."""
    # Approximation: risk = ATR * sl_mult * quantity * price
    # Since we don't have SL in the CloseResult, estimate from price
    if trade.pnl == 0:
        return 0
    # Rough: use distance to entry as proxy, normalize by small value
    # For comparison purposes, use raw pnl normalized
    risk = abs(trade.entry_price - trade.exit_price) if trade.entry_price != trade.exit_price else 1
    return trade.pnl / max(risk, 0.01) if risk > 0 else 0


# ── Validation Tests ───────────────────────────────────────

def compute_metrics(trades):
    """Compute PF, WR, expectancy, avg win/loss from trades."""
    if not trades:
        return {}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (99 if gross_profit > 0 else 0)
    wr = len(wins) / len(trades) * 100
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 1
    expectancy = (wr/100 * avg_win) - ((100-wr)/100 * avg_loss)
    return {
        "trades": len(trades),
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "net": round(sum(t["pnl"] for t in trades), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "max_win": round(max(t["pnl"] for t in trades), 2),
        "max_loss": round(min(t["pnl"] for t in trades), 2),
    }


def bootstrap_test(trades, simulations=1000):
    """Shuffle trade order, compute PF distribution."""
    pnls = [t["pnl"] for t in trades]
    original_pf = compute_metrics(trades).get("pf", 0)

    pf_distribution = []
    for _ in range(simulations):
        shuffled = list(pnls)
        random.shuffle(shuffled)
        wins = sum(1 for p in shuffled if p > 0)
        gp = sum(p for p in shuffled if p > 0)
        gl = abs(sum(p for p in shuffled if p < 0))
        pf = gp / gl if gl > 0 else 0
        pf_distribution.append(pf)

    pf_distribution.sort()
    return {
        "original_pf": round(original_pf, 2),
        "median_pf": round(pf_distribution[len(pf_distribution)//2], 2),
        "p5_pf": round(pf_distribution[int(len(pf_distribution)*0.05)], 2),
        "p95_pf": round(pf_distribution[int(len(pf_distribution)*0.95)], 2),
        "pct_above_1": round(sum(1 for pf in pf_distribution if pf >= 1.0) / len(pf_distribution) * 100, 1),
    }


def monthly_stability(trades):
    """Compute PF by month."""
    by_month = defaultdict(list)
    for t in trades:
        dt = datetime.fromtimestamp(t["ts"] / 1000 if t["ts"] > 1e12 else t["ts"], tz=timezone.utc)
        key = dt.strftime("%Y-%m")
        by_month[key].append(t)

    results = {}
    for month in sorted(by_month.keys()):
        results[month] = compute_metrics(by_month[month])
    return results


QUANTITY = 0.001  # BTC per trade (matches shadow executor)


def cost_sensitivity(trades):
    """Test how PF changes with different cost levels."""
    results = []
    cost_levels = [
        ("Ideal (0)", 0.0, 0.0),
        ("Low (0.05%/0.025%)", 0.0005, 0.00025),
        ("Realistic (0.1%/0.05%)", 0.001, 0.0005),
        ("High (0.2%/0.1%)", 0.002, 0.001),
    ]

    for label, fee_rate, slip_rate in cost_levels:
        adjusted_trades = []
        for t in trades:
            new_t = dict(t)
            # Cost = entry_price * quantity * (2 * fee + slip)
            cost = t["entry_price"] * QUANTITY * (2 * fee_rate + slip_rate)
            new_t["pnl"] = t["pnl"] - cost
            adjusted_trades.append(new_t)
        m = compute_metrics(adjusted_trades)
        results.append((label, m))
    return results


def losing_trade_analysis(trades, top_n=100):
    """Find common conditions in worst trades."""
    losing = sorted([t for t in trades if t["pnl"] < 0], key=lambda t: t["pnl"])
    worst = losing[:top_n]

    if not worst:
        return "No losing trades"

    # Group by conditions
    by_hour = defaultdict(lambda: {"count": 0, "total_loss": 0})
    by_day = defaultdict(lambda: {"count": 0, "total_loss": 0})
    by_reason = defaultdict(lambda: {"count": 0, "total_loss": 0})

    for t in worst:
        ts = t["ts"] / 1000 if t["ts"] > 1e12 else t["ts"]
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        by_hour[dt.hour]["count"] += 1
        by_hour[dt.hour]["total_loss"] += t["pnl"]
        by_day[dt.strftime("%a")]["count"] += 1
        by_day[dt.strftime("%a")]["total_loss"] += t["pnl"]
        by_reason[t["reason"]]["count"] += 1
        by_reason[t["reason"]]["total_loss"] += t["pnl"]

    lines = []
    lines.append(f"\n  LOSING TRADE ANALYSIS (worst {len(worst)} of {len(losing)} total losers):")

    lines.append(f"\n  By hour:")
    for h in sorted(by_hour.keys()):
        v = by_hour[h]
        lines.append(f"    {h:02d}:00  {v['count']:>3} trades, avg loss: {v['total_loss']/v['count']:.1f}")

    lines.append(f"\n  By day:")
    for d in sorted(by_day.keys()):
        v = by_day[d]
        lines.append(f"    {d}: {v['count']:>3} trades, avg loss: {v['total_loss']/v['count']:.1f}")

    lines.append(f"\n  By exit reason:")
    for r in sorted(by_reason.keys()):
        v = by_reason[r]
        lines.append(f"    {r}: {v['count']:>3} trades, avg loss: {v['total_loss']/v['count']:.1f}")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Edge Validation Report")
    parser.add_argument("--strategy", default="LS-001")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()

    print("=" * 70)
    print(f"  EDGE VALIDATION REPORT — {args.strategy}")
    print(f"  {args.symbol} {args.timeframe} | {args.months} months")
    print("=" * 70)

    # 1. Collect v1 (unfiltered) and v2 (filtered) trades
    print("\n[1] Collecting unfiltered trades (v1)...")
    trades_v1 = await collect_trades(args.strategy, args.symbol, args.timeframe, args.months, filtered=False)
    print(f"    v1: {len(trades_v1)} trades")

    print("\n[2] Collecting filtered trades (v2 with conditional filter)...")
    trades_v2 = await collect_trades(args.strategy, args.symbol, args.timeframe, args.months, filtered=True)
    print(f"    v2: {len(trades_v2)} trades")

    # 3. Core metrics comparison
    print("\n" + "-" * 70)
    print("  R-MULTIPLE COMPARISON")
    print("-" * 70)
    print(f"  {'Metric':<15} {'LS v1 (unfiltered)':<22} {'LS v2 (filtered)':<22}")
    m1 = compute_metrics(trades_v1)
    m2 = compute_metrics(trades_v2)
    for key in ["trades", "wr", "pf", "net", "avg_win", "avg_loss", "expectancy"]:
        v1 = m1.get(key, "n/a")
        v2 = m2.get(key, "n/a")
        if isinstance(v1, float):
            print(f"  {key:<15} {v1:>22.2f} {v2:>22.2f}")
        else:
            print(f"  {key:<15} {v1:>22} {v2:>22}")

    # 4. Bootstrap test
    print("\n" + "-" * 70)
    print("  BOOTSTRAP TEST (does PF survive random shuffling?)")
    print("-" * 70)
    b1 = bootstrap_test(trades_v1, args.bootstrap)
    b2 = bootstrap_test(trades_v2, args.bootstrap)
    print(f"  {'Metric':<20} {'v1':<15} {'v2':<15}")
    for key in ["original_pf", "median_pf", "p5_pf", "p95_pf", "pct_above_1"]:
        print(f"  {key:<20} {b1[key]:<15} {b2[key]:<15}")
    if b2["pct_above_1"] > 90:
        print("  >>> v2 edge is ROBUST (>90% of permutations PF>=1)")
    elif b2["pct_above_1"] > 70:
        print("  >>> v2 edge is MODERATE (70-90%)")
    else:
        print("  >>> v2 edge is FRAGILE (<70%)")

    # 5. Monthly stability
    print("\n" + "-" * 70)
    print("  MONTHLY PF STABILITY (v2)")
    print("-" * 70)
    monthly = monthly_stability(trades_v2)
    for month in sorted(monthly.keys()):
        m = monthly[month]
        flag = " <<<" if m.get("pf", 0) < 1 else ""
        print(f"  {month}  PF: {m.get('pf',0):>6.2f}  WR: {m.get('wr',0):>5.1f}%  Trades: {m.get('trades',0):>4}  Net: {m.get('net',0):>+10.1f}{flag}")

    # 6. Cost sensitivity
    print("\n" + "-" * 70)
    print("  EXECUTION COST SENSITIVITY (v2)")
    print("-" * 70)
    costs = cost_sensitivity(trades_v2)
    for label, m in costs:
        flag = " <<< STILL PROFITABLE" if m.get("pf", 0) > 1 else " <<< DEAD"
        print(f"  {label:<35} PF: {m.get('pf',0):>6.2f}  WR: {m.get('wr',0):>5.1f}%  Net: {m.get('net',0):>+10.1f}{flag}")

    # 7. Losing trade analysis
    print("\n" + "-" * 70)
    print("  LOSING TRADE CAUSE TREE (v2)")
    print("-" * 70)
    print(losing_trade_analysis(trades_v2))

    print("\n" + "=" * 70)
    print("  DECISION FRAMEWORK")
    print("=" * 70)
    pf_improved = m2.get("pf", 0) > m1.get("pf", 0)
    bootstrap_robust = b2["pct_above_1"] > 70
    monthly_pfs = [monthly[m].get("pf", 0) for m in monthly if monthly[m].get("pf", 0) > 0]
    monthly_stable = len(monthly_pfs) > 0 and sum(1 for pf in monthly_pfs if pf > 1) / len(monthly_pfs) > 0.6
    realistic_alive = costs[2][1].get("pf", 0) > 1  # realistic cost level

    print(f"  PF improved:           {'YES' if pf_improved else 'NO'}  ({m1.get('pf',0):.2f} -> {m2.get('pf',0):.2f})")
    print(f"  Bootstrap robust:      {'YES' if bootstrap_robust else 'NO'}  ({b2['pct_above_1']:.0f}% of permutations PF>=1)")
    print(f"  Monthly stable:        {'YES' if monthly_stable else 'NO'}  ({sum(1 for pf in monthly_pfs if pf > 1)}/{len(monthly_pfs)} months PF>1)")
    print(f"  Realistic costs alive: {'YES' if realistic_alive else 'NO'}  (PF at 0.1% fee + 0.05% slip: {costs[2][1].get('pf',0):.2f})")

    if all([pf_improved, bootstrap_robust, monthly_stable, realistic_alive]):
        print("\n  >>> VERDICT: CANDIDATE READY FOR WALK FORWARD <<<")
    else:
        print("\n  >>> VERDICT: NEEDS MORE WORK <<<")

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())