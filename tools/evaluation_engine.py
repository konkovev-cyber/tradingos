#!/usr/bin/env python3
"""
Evaluation Engine — диагностический отчёт по закрытым сделкам.

НИЧЕГО не останавливает и не меняет в торговле. Только читает журнал
guardian_effectiveness.jsonl и строит отчёт.

Отчёт:
- общая статистика (PF, winrate, expectancy, avg R, max DD)
- по outcome (SL/TP/BE)
- по символам (где прибыль, где убыток)
- по signal_class
- рекомендация: CONTINUE / REVIEW / STOP TEST (решение за человеком)

Usage:
    python3 tools/evaluation_engine.py
    python3 tools/evaluation_engine.py --last 30
    python3 tools/evaluation_engine.py --json
"""
import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
LOG = ROOT / "guardian" / "guardian_effectiveness.jsonl"


def load_trades(last_n=None, since=None):
    """Load closed trades from guardian journal."""
    trades = []
    if not LOG.exists():
        return trades
    for line in LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("status") != "CLOSED":
            continue
        trades.append(d)
    if since:
        cutoff = datetime.now(timezone.utc) - since
        trades = [t for t in trades
                  if datetime.fromisoformat(t.get("timestamp", "")).replace(tzinfo=timezone.utc) >= cutoff]
    if last_n:
        trades = trades[-last_n:]
    return trades


def compute_stats(trades):
    """Compute PF, winrate, expectancy, avg R, max DD."""
    if not trades:
        return None
    wins = [t for t in trades if t.get("net_pnl", 0) > 0]
    losses = [t for t in trades if t.get("net_pnl", 0) < 0]
    gross_win = sum(t.get("net_pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("net_pnl", 0) for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    winrate = len(wins) / len(trades) * 100 if trades else 0
    avg = statistics.mean([t.get("net_pnl", 0) for t in trades]) if trades else 0
    # expectancy in R: use mfe/mae as proxy for R? Use net_pnl normalized by risk.
    # risk per trade ~ |entry - sl| * size, but sl not stored. Use net_pnl directly.
    # Max DD: cumulative net pnl running min
    cum = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cum += t.get("net_pnl", 0)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return {
        "n": len(trades),
        "pf": pf,
        "winrate": winrate,
        "avg_pnl": avg,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "max_dd": max_dd,
        "wins": len(wins),
        "losses": len(losses),
    }


def recommend(stats):
    """Recommendation — decision stays with human. Nothing auto-stops."""
    if not stats or stats["n"] < 10:
        return "INSUFFICIENT_DATA"
    if stats["pf"] >= 1.2 and stats["winrate"] >= 40:
        return "CONTINUE"
    if stats["pf"] >= 0.9:
        return "REVIEW"
    return "STOP_TEST"


def fmt_pf(pf):
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, default=None, help="last N trades")
    ap.add_argument("--since", type=str, default=None, help="e.g. 7d, 24h")
    ap.add_argument("--json", action="store_true", help="output JSON")
    args = ap.parse_args()

    since = None
    if args.since:
        unit = args.since[-1]
        val = int(args.since[:-1])
        since = timedelta(hours=val) if unit == "h" else timedelta(days=val)

    trades = load_trades(last_n=args.last, since=since)
    stats = compute_stats(trades)

    if args.json:
        print(json.dumps({"trades": trades, "stats": stats,
                          "recommendation": recommend(stats)}, indent=2, default=str))
        return

    print("=" * 60)
    print("EVALUATION ENGINE — LIVE VALIDATION REPORT")
    print("=" * 60)
    if not trades:
        print("Нет закрытых сделок.")
        return
    print(f"Сделок: {stats['n']} (win={stats['wins']} loss={stats['losses']})")
    print(f"Profit Factor: {fmt_pf(stats['pf'])}")
    print(f"Winrate: {stats['winrate']:.1f}%")
    print(f"Avg PnL: ${stats['avg_pnl']:+.4f}")
    print(f"Gross win: ${stats['gross_win']:.3f} | Gross loss: ${stats['gross_loss']:.3f}")
    print(f"Max DD: ${stats['max_dd']:.3f}")
    print(f"Рекомендация: {recommend(stats)} (решение за человеком)")

    # По outcome
    print("\n--- По outcome ---")
    by_outcome = defaultdict(list)
    for t in trades:
        by_outcome[t.get("outcome", "?")].append(t)
    for oc, grp in sorted(by_outcome.items(), key=lambda x: -len(x[1])):
        s = compute_stats(grp)
        print(f"  {oc}: n={len(grp)} PF={fmt_pf(s['pf'])} winrate={s['winrate']:.0f}% avg=${s['avg_pnl']:+.4f}")

    # По символам
    print("\n--- По символам (топ по PnL) ---")
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol", "?")].append(t)
    sym_stats = []
    for sym, grp in by_sym.items():
        s = compute_stats(grp)
        sym_stats.append((sym, s))
    for sym, s in sorted(sym_stats, key=lambda x: -x[1]["avg_pnl"] * x[1]["n"])[:10]:
        print(f"  {sym}: n={s['n']} PF={fmt_pf(s['pf'])} avg=${s['avg_pnl']:+.4f}")

    # По signal_class
    print("\n--- По signal_class ---")
    by_cls = defaultdict(list)
    for t in trades:
        by_cls[t.get("signal_class", "?")].append(t)
    for cls, grp in sorted(by_cls.items(), key=lambda x: -len(x[1])):
        s = compute_stats(grp)
        print(f"  {cls}: n={len(grp)} PF={fmt_pf(s['pf'])} winrate={s['winrate']:.0f}% avg=${s['avg_pnl']:+.4f}")


if __name__ == "__main__":
    main()
