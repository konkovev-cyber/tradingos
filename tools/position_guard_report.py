"""
tools/position_guard_report.py
Shadow Analytics Reporter for Position Guard v1.

READ-ONLY. Does not touch live systems, JSONL log, or PIE DB.
Generates a daily report on what Guard has been suggesting.

Usage:
    python3 tools/position_guard_report.py
    python3 tools/position_guard_report.py --since 24h
    python3 tools/position_guard_report.py --json
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any


SHADOW_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")


def parse_since(s: str) -> datetime:
    """Parse duration like '24h', '6h', '30m' into datetime cutoff."""
    unit = s[-1]
    value = int(s[:-1])
    now = datetime.now(timezone.utc)
    if unit == "h":
        return now - timedelta(hours=value)
    if unit == "m":
        return now - timedelta(minutes=value)
    if unit == "d":
        return now - timedelta(days=value)
    raise ValueError(f"Unsupported duration format: {s}")


def load_records(since: datetime) -> List[Dict[str, Any]]:
    """Read JSONL and filter by timestamp >= since."""
    if not SHADOW_LOG.exists():
        return []
    records = []
    with SHADOW_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= since:
                    records.append(rec)
            except Exception:
                continue
    return records


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build aggregate stats from a list of Guard records."""
    total = len(records)
    by_action: Dict[str, int] = defaultdict(int)
    by_symbol: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"hold": 0, "move_sl": 0, "ignore": 0, "skipped": 0, "pnl_sum": 0.0, "count": 0}
    )
    move_sl_records: List[Dict[str, Any]] = []
    skipped_count = 0
    move_sl_health_sum = 0.0
    move_sl_mfe_sum = 0.0
    move_sl_n = 0

    for r in records:
        action = r["decision"]["action"]
        symbol = r["symbol"]
        by_action[action] += 1
        by_symbol[symbol][action.lower()] += 1
        by_symbol[symbol]["pnl_sum"] += r["snapshot"]["pnl_pct"]
        by_symbol[symbol]["count"] += 1
        if action == "MOVE_SL":
            move_sl_records.append(r)
            move_sl_health_sum += float(r["snapshot"].get("health", 0.0) or 0.0)
            move_sl_mfe_sum += float(r["snapshot"].get("mfe_pct", 0.0) or 0.0)
            move_sl_n += 1
        if action == "SKIPPED":
            skipped_count += 1

    for sym, agg in by_symbol.items():
        agg["avg_pnl_pct"] = round(agg["pnl_sum"] / agg["count"], 4) if agg["count"] else 0.0
        del agg["pnl_sum"]

    fresh_count = total - skipped_count
    fresh_rate = (fresh_count / total) if total else 0.0
    stale_rate = (skipped_count / total) if total else 0.0
    data_quality_score = max(0, round(fresh_rate * 100 - stale_rate * 50, 1))

    first_ts = min((r["timestamp"] for r in records), default=None)
    last_ts = max((r["timestamp"] for r in records), default=None)

    return {
        "period_start": first_ts,
        "period_end": last_ts,
        "total_evaluations": total,
        "by_action": dict(by_action),
        "by_symbol": dict(by_symbol),
        "move_sl_count": by_action.get("MOVE_SL", 0),
        "move_sl_records": move_sl_records,
        "data_quality": {
            "fresh_count": fresh_count,
            "skipped_count": skipped_count,
            "fresh_rate": round(fresh_rate, 4),
            "stale_rate": round(stale_rate, 4),
            "data_quality_score": data_quality_score,
        },
        "move_sl_health_avg": round(move_sl_health_sum / move_sl_n, 1) if move_sl_n else None,
        "move_sl_mfe_avg": round(move_sl_mfe_sum / move_sl_n, 4) if move_sl_n else None,
    }


def print_text_report(stats: Dict[str, Any]) -> None:
    print("=" * 60)
    print("  POSITION GUARD — SHADOW REPORT")
    print("=" * 60)
    print(f"  Period start: {stats['period_start']}")
    print(f"  Period end:   {stats['period_end']}")
    print()
    print(f"  Total evaluations: {stats['total_evaluations']}")
    print()
    print("  Data Quality:")
    dq = stats["data_quality"]
    print(f"    Fresh rate:        {dq['fresh_rate']*100:5.1f}%  ({dq['fresh_count']} fresh / {dq['skipped_count']} skipped)")
    print(f"    Stale rate:        {dq['stale_rate']*100:5.1f}%")
    print(f"    Data quality score:{dq['data_quality_score']:.1f}/100")
    print()
    print("  Decisions:")
    for action, count in stats["by_action"].items():
        pct = (count / stats["total_evaluations"] * 100) if stats["total_evaluations"] else 0
        print(f"    {action:10s} {count:5d}  ({pct:5.1f}%)")
    print()
    if stats.get("move_sl_health_avg") is not None:
        print("  MOVE_SL signal quality:")
        print(f"    avg health: {stats['move_sl_health_avg']:.1f}")
        print(f"    avg MFE:    {stats['move_sl_mfe_avg']:+.2f}%")
        print()
    print(f"  Distinct symbols: {len(stats['by_symbol'])}")
    print()
    print("  Per-symbol MOVE_SL activity:")
    for sym, agg in sorted(stats["by_symbol"].items()):
        if agg.get("move_sl", 0) > 0 or agg.get("skipped", 0) > 0:
            print(
                f"    {sym:12s} MOVE_SL={agg.get('move_sl', 0):3d}  "
                f"HOLD={agg.get('hold', 0):3d}  "
                f"SKIPPED={agg.get('skipped', 0):3d}  "
                f"avg_pnl={agg['avg_pnl_pct']:+.3f}%"
            )
    print()
    if stats["move_sl_records"]:
        print(f"  Recent MOVE_SL signals (last {min(5, len(stats['move_sl_records']))}):")
        for r in stats["move_sl_records"][-5:]:
            print(
                f"    {r['timestamp']} | {r['symbol']:10s} {r['side']:5s} | "
                f"PnL={r['snapshot']['pnl_pct']:+.2f}% MFE={r['snapshot']['mfe_pct']:+.2f}% "
                f"Health={r['snapshot']['health']:.0f} -> proposed SL={r['decision']['proposed_stop']}"
            )
    print()
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Position Guard Shadow Report")
    parser.add_argument("--since", default="24h", help="Window (e.g. 24h, 6h, 30m)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    try:
        cutoff = parse_since(args.since)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    records = load_records(cutoff)
    if not records:
        print(f"No records found since {cutoff.isoformat()}")
        return 0

    stats = aggregate(records)

    if args.json:
        out = {k: v for k, v in stats.items() if k != "move_sl_records"}
        out["recent_move_sl"] = stats["move_sl_records"][-10:]
        print(json.dumps(out, indent=2, default=str))
    else:
        print_text_report(stats)

    return 0


if __name__ == "__main__":
    sys.exit(main())
