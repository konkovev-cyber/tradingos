"""
tools/action_shadow_report.py
Daily report for Position Action Shadow v0.1.

Pure read-only. Reports how many actions were approved vs rejected,
and the unrealized PnL of positions that had APPROVED actions.
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_PATH = Path("/root/tradingos/logs/position_action_shadow.jsonl")


def parse_since(s: str) -> datetime:
    unit = s[-1]
    value = int(s[:-1])
    now = datetime.now(timezone.utc)
    if unit == "h":
        return now - timedelta(hours=value)
    if unit == "m":
        return now - timedelta(minutes=value)
    if unit == "d":
        return now - timedelta(days=value)
    raise ValueError(f"Unsupported duration: {s}")


def load_records(since: datetime) -> list:
    if not LOG_PATH.exists():
        return []
    out = []
    with LOG_PATH.open() as f:
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
                    out.append(rec)
            except Exception:
                continue
    return out


def aggregate(records: list) -> dict:
    total = len(records)
    by_action: dict = defaultdict(int)
    by_verdict: dict = defaultdict(int)
    by_reject_reason: dict = defaultdict(int)
    approved: list = []
    for r in records:
        atype = r["action"]["type"]
        verdict = r["action"]["verdict"]
        by_action[atype] += 1
        by_verdict[verdict] += 1
        if verdict == "APPROVE":
            approved.append(r)
        else:
            for reason in r["action"]["reasons"]:
                base_reason = reason.split(":")[0].split("=")[0]
                by_reject_reason[base_reason] += 1
    return {
        "total": total,
        "by_action": dict(by_action),
        "by_verdict": dict(by_verdict),
        "by_reject_reason": dict(by_reject_reason),
        "approved_count": len(approved),
        "approved": approved,
    }


def render(stats: dict) -> str:
    lines = [
        "=" * 64,
        "  POSITION ACTION SHADOW v0.1 — REPORT",
        "=" * 64,
        f"  Total evaluations: {stats['total']}",
        "",
        "  By action type:",
    ]
    for k, v in sorted(stats["by_action"].items(), key=lambda x: -x[1]):
        lines.append(f"    {k:20s} {v}")
    lines.append("")
    lines.append("  By verdict:")
    for k, v in sorted(stats["by_verdict"].items(), key=lambda x: -x[1]):
        lines.append(f"    {k:20s} {v}")
    if stats["by_reject_reason"]:
        lines.append("")
        lines.append("  Top reject reasons:")
        for k, v in sorted(stats["by_reject_reason"].items(), key=lambda x: -x[1])[:10]:
            lines.append(f"    {k:30s} {v}")
    if stats["approved"]:
        lines.append("")
        lines.append(f"  APPROVED actions ({stats['approved_count']}):")
        for r in stats["approved"][-10:]:
            pie = r["pie_recommendation"]
            a = r["action"]
            lines.append(
                f"    {r['timestamp'][:19]} | {r['symbol']:10s} {r['side']:5s} | "
                f"{a['type']:15s} | PnL={pie['pnl_pct']:+.2f}% MFE={pie['mfe_pct']:+.2f}% "
                f"Health={pie['health']:.0f}"
            )
    lines += [
        "",
        "  Note: This is SHADOW ONLY. No orders sent to exchange.",
        "=" * 64,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Action Shadow Report")
    parser.add_argument("--since", default="24h")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        cutoff = parse_since(args.since)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    records = load_records(cutoff)
    if not records:
        print(f"No records since {cutoff.isoformat()}")
        return 0

    stats = aggregate(records)

    if args.json:
        out = {k: v for k, v in stats.items() if k != "approved"}
        out["approved_recent"] = stats["approved"][-20:]
        print(json.dumps(out, indent=2, default=str))
    else:
        print(render(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
