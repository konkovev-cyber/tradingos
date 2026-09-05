"""
evidence/reality_bootstrap.py
Reality Bootstrap — reads Decision Journal, shows current stats.
Even with little data, shows what we have.
"""
import json, sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

JOURNAL_PATH = Path("/root/tradingos/evidence/decision_journal.jsonl")


def load_journal() -> list:
    if not JOURNAL_PATH.exists():
        return []
    entries = []
    with JOURNAL_PATH.open() as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def compute_stats(entries: list) -> dict:
    total = len(entries)
    decisions = {}
    trades = []
    sessions = {}
    reasons = {}

    for e in entries:
        d = e.get("decision", "UNKNOWN")
        decisions[d] = decisions.get(d, 0) + 1

        if d == "PROPOSED":
            trades.append(e)
            sess = e.get("context", {}).get("session", "?")
            sessions[sess] = sessions.get(sess, 0) + 1

        r = e.get("reason", "")
        if r:
            reasons[r[:40]] = reasons.get(r[:40], 0) + 1

    # Trade stats (for paper trades that would have been executed)
    trade_count = len(trades)
    confidence_sum = sum(t.get("confidence", 0) for t in trades)
    avg_confidence = confidence_sum / trade_count if trade_count else 0

    return {
        "total_entries": total,
        "decisions_breakdown": decisions,
        "trade_proposals": trade_count,
        "avg_confidence": round(avg_confidence, 2),
        "by_session": sessions,
        "recent_reasons": reasons,
    }


def print_report():
    entries = load_journal()
    if not entries:
        print("\n📭 Decision journal is empty.")
        print("   Run scanner first: python3 strategies/arc/live_scanner.py")
        return

    stats = compute_stats(entries)
    first_ts = entries[0].get("timestamp", "?")[:19]
    last_ts = entries[-1].get("timestamp", "?")[:19]

    print(f"\n{'='*55}")
    print(f"  EVIDENCE BOOTSTRAP — REALITY REPORT")
    print(f"{'='*55}")
    print(f"  Period:       {first_ts} → {last_ts}")
    print(f"  Total events: {stats['total_entries']}")
    print(f"  Trade props:  {stats['trade_proposals']}")
    print(f"  Avg conf:     {stats['avg_confidence']}")
    print(f"\n  Decisions:")
    for d, count in sorted(stats['decisions_breakdown'].items()):
        print(f"    {d:12s}: {count}")
    if stats['by_session']:
        print(f"\n  By session:")
        for s, count in sorted(stats['by_session'].items()):
            print(f"    {s:10s}: {count}")
    print(f"\n  Recent reasons (top 5):")
    for r, count in sorted(stats['recent_reasons'].items(), key=lambda x: -x[1])[:5]:
        print(f"    {count:3d}x  {r}")
    print(f"{'='*55}")


if __name__ == "__main__":
    print_report()
