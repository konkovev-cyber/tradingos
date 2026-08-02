"""
tools/decision_review_report.py
Decision Review Engine v0.1 — single-command comprehensive review.

Read-only. Aggregates from:
  - position_guard_shadow.jsonl
  - position_action_shadow.jsonl
  - position_guard_outcome.py results
  - action_shadow_outcome.py results
  - capital data

Usage:
    python3 tools/decision_review_report.py
    python3 tools/decision_review_report.py --since 7d
    python3 tools/decision_review_report.py --json
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path("/root/tradingos")
PG_LOG = ROOT / "logs" / "position_guard_shadow.jsonl"
ACTION_LOG = ROOT / "logs" / "position_action_shadow.jsonl"
UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
PIE_DB = ROOT / "tradingos_data.db"

DEDUPE_WINDOW = 1800  # 30 min


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
    raise ValueError(f"Unsupported: {s}")


def load_jsonl(path: Path, since: datetime) -> List[Dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= since:
                    out.append(rec)
            except Exception:
                continue
    return out


def pg_review(records: List[Dict]) -> Dict:
    total = len(records)
    by_action: Dict[str, int] = defaultdict(int)
    valid_move_sl = 0
    stale_blocked = 0
    health_sum = 0.0
    mfe_sum = 0.0
    valid_count = 0

    for r in records:
        action = r.get("decision", {}).get("action", "?")
        by_action[action] += 1
        if action == "SKIPPED":
            stale_blocked += 1
        elif action == "MOVE_SL":
            v = r.get("validation", {})
            if v.get("reconciled", False):
                valid_move_sl += 1
                health_sum += float(r.get("snapshot", {}).get("health", 0))
                mfe_sum += float(r.get("snapshot", {}).get("mfe_pct", 0))
                valid_count += 1

    dq = 0.0
    if total > 0:
        fresh_rate = (total - stale_blocked) / total
        stale_rate = stale_blocked / total
        dq = round(fresh_rate * 100 - stale_rate * 50, 1)

    return {
        "total": total,
        "hold": by_action.get("HOLD", 0),
        "move_sl_raw": by_action.get("MOVE_SL", 0),
        "valid_move_sl": valid_move_sl,
        "skipped_stale": stale_blocked,
        "avg_health": round(health_sum / valid_count, 1) if valid_count else None,
        "avg_mfe": round(mfe_sum / valid_count, 4) if valid_count else None,
        "dq_score": dq,
    }


def action_shadow_review(records: List[Dict]) -> Dict:
    total = len(records)
    approved_raw = 0
    rejected = 0
    by_reject: Dict[str, int] = defaultdict(int)
    seen_keys: Dict[tuple, str] = {}
    unique = 0

    for r in records:
        a = r.get("action", {})
        verdict = a.get("verdict", "?")
        if verdict == "APPROVE":
            approved_raw += 1
            key = (r.get("symbol", ""), r.get("side", ""), a.get("type", "?"))
            ts = r.get("timestamp", "")
            if key not in seen_keys:
                seen_keys[key] = ts
            elif ts > seen_keys[key]:
                seen_keys[key] = ts
        elif verdict == "REJECT":
            rejected += 1
            for reason in a.get("reasons", []):
                base = reason.split(":")[0].split("=")[0]
                by_reject[base] += 1

    unique = len(seen_keys)
    pie_total = total

    return {
        "pie_recs_total": pie_total,
        "approved_raw": approved_raw,
        "rejected": rejected,
        "unique_after_dedupe": unique,
        "top_reject_reasons": dict(sorted(by_reject.items(), key=lambda x: -x[1])[:5]),
    }


def capital_review() -> Dict:
    if not UBOT_DB.exists():
        return {"open": 0, "unrealized": 0.0, "closed": 0, "realized": 0.0}
    try:
        uri = f"file:{UBOT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0.0) FROM trade_journal WHERE exit_price IS NULL"
        )
        n_open, unrealized = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0.0) FROM trade_journal WHERE exit_price IS NOT NULL"
        )
        n_closed, realized = cur.fetchone()
        conn.close()
        return {
            "open": int(n_open),
            "unrealized": round(float(unrealized), 4),
            "closed": int(n_closed),
            "realized": round(float(realized), 4),
        }
    except Exception:
        return {"open": 0, "unrealized": 0.0, "closed": 0, "realized": 0.0}


def gate_assessment(pg: Dict, action: Dict) -> Dict:
    reasons = []
    dq_ok = pg["dq_score"] >= 90
    pg_ok = pg["valid_move_sl"] >= 20
    action_ok = action["unique_after_dedupe"] >= 20

    if not dq_ok:
        reasons.append(f"DQ {pg['dq_score']}/100 (need ≥90)")
    if not pg_ok:
        reasons.append(f"PG valid {pg['valid_move_sl']}/20")
    if not action_ok:
        reasons.append(f"Action unique {action['unique_after_dedupe']}/20")

    if reasons:
        verdict = "COLLECTING"
    else:
        verdict = "READY_FOR_REVIEW"

    return {
        "verdict": verdict,
        "dq_pass": dq_ok,
        "pg_pass": pg_ok,
        "action_pass": action_ok,
        "blocking_reasons": reasons,
    }


def render(pg: Dict, action: Dict, cap: Dict, gate: Dict, since_str: str) -> str:
    lines = [
        "=" * 64,
        "  TRADINGOS — DECISION REVIEW ENGINE v0.1",
        "=" * 64,
        f"  Period: {since_str}",
        "",
        "  ── Gate Status ──",
        f"  Verdict:           {gate['verdict']}",
    ]
    if gate["blocking_reasons"]:
        for r in gate["blocking_reasons"]:
            lines.append(f"    BLOCKED: {r}")
    else:
        lines.append("    All gates PASS — ready for review")
    lines += [
        "",
        "  ── Data Quality ──",
        f"  DQ Score:          {pg['dq_score']}/100 (target ≥90)",
        f"  Fresh rate:        {100 - pg['skipped_stale'] * 100 // max(pg['total'], 1):.1f}%",
        f"  Stale rate:        {pg['skipped_stale'] * 100 // max(pg['total'], 1):.1f}%",
        "",
        "  ── Position Guard v1.1 ──",
        f"  Total evaluations: {pg['total']}",
        f"  HOLD:              {pg['hold']}",
        f"  MOVE_SL (raw):     {pg['move_sl_raw']}",
        f"  Valid MOVE_SL:     {pg['valid_move_sl']} / 20",
        f"  Stale blocked:     {pg['skipped_stale']}",
        f"  Avg Health:        {pg['avg_health']}",
        f"  Avg MFE:           {pg['avg_mfe']:+.2f}%" if pg['avg_mfe'] is not None else "  Avg MFE:           n/a",
        "",
        "  ── Action Shadow v0.1 ──",
        f"  PIE recs seen:     {action['pie_recs_total']}",
        f"  APPROVED (raw):    {action['approved_raw']}",
        f"  REJECTED:          {action['rejected']}",
        f"  Unique actions:    {action['unique_after_dedupe']} / 20",
    ]
    if action["top_reject_reasons"]:
        lines.append("  Top reject reasons:")
        for k, v in list(action["top_reject_reasons"].items())[:3]:
            lines.append(f"    {k:25s} {v}")
    lines += [
        "",
        "  ── Capital ──",
        f"  Open positions:    {cap['open']}",
        f"  Unrealized PnL:    {cap['unrealized']:+.4f} USDT",
        f"  Closed trades:     {cap['closed']}",
        f"  Realized PnL:      {cap['realized']:+.4f} USDT",
        "",
        "  ── Recommendation ──",
    ]
    if gate["verdict"] == "READY_FOR_REVIEW":
        lines.append("  ➡️  All data collected. Run full EV analysis before PROMOTE/REVISE/FREEZE.")
    else:
        lines.append("  ⏳  Still collecting evidence. No code changes needed.")
        lines.append("       Next check: run again when gate conditions are closer to pass.")
    lines += [
        "",
        "  Note: No orders executed. No code changed. Read-only analysis.",
        "=" * 64,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Decision Review Engine")
    parser.add_argument("--since", default="7d")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        cutoff = parse_since(args.since)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    pg_records = load_jsonl(PG_LOG, cutoff)
    action_records = load_jsonl(ACTION_LOG, cutoff)

    pg = pg_review(pg_records)
    action = action_shadow_review(action_records)
    cap = capital_review()
    gate = gate_assessment(pg, action)

    if args.json:
        print(json.dumps({
            "period": args.since,
            "gate": gate,
            "pg": pg,
            "action_shadow": action,
            "capital": cap,
        }, indent=2, default=str))
    else:
        print(render(pg, action, cap, gate, args.since))

    return 0


if __name__ == "__main__":
    sys.exit(main())
