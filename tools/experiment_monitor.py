"""
tools/experiment_monitor.py
Single-command view of all experiment progress and Decision Gate status.

Pure read-only. No logic changes. Just answers:
"Are we ready for Decision Review?"

Usage:
    python3 tools/experiment_monitor.py
    python3 tools/experiment_monitor.py --json
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
PG_LOG = ROOT / "logs" / "position_guard_shadow.jsonl"
ACTION_LOG = ROOT / "logs" / "position_action_shadow.jsonl"
PIE_DB = ROOT / "tradingos_data.db"
UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
SNAPSHOT_DIR = ROOT / "docs" / "evidence_snapshots"


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open() as f:
            for line in f:
                if line.strip():
                    n += 1
    except Exception:
        pass
    return n


def pg_metrics() -> dict:
    if not PG_LOG.exists():
        return {"total": 0, "valid_move_sl": 0, "skipped": 0, "hold": 0}
    total = valid = skipped = hold = 0
    try:
        with PG_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    action = rec.get("decision", {}).get("action", "?")
                    validation = rec.get("validation", {})
                    if action == "MOVE_SL" and validation.get("reconciled", False):
                        valid += 1
                    elif action == "SKIPPED":
                        skipped += 1
                    elif action == "HOLD":
                        hold += 1
                except Exception:
                    continue
    except Exception:
        pass
    return {"total": total, "valid_move_sl": valid, "skipped": skipped, "hold": hold}


def action_metrics() -> dict:
    if not ACTION_LOG.exists():
        return {"total": 0, "approved_raw": 0, "unique": 0, "completed": 0}
    total = approved_raw = 0
    seen_keys: dict = {}
    completed = 0
    try:
        with ACTION_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    verdict = rec.get("action", {}).get("verdict", "?")
                    if verdict != "APPROVE":
                        continue
                    approved_raw += 1
                    a = rec.get("action", {})
                    key = (rec["symbol"], rec["side"], a.get("type", "?"))
                    ts_str = rec.get("timestamp", "")
                    if key not in seen_keys:
                        seen_keys[key] = ts_str
                    elif ts_str > seen_keys[key]:
                        seen_keys[key] = ts_str
                except Exception:
                    continue
        unique = len(seen_keys)
    except Exception:
        unique = 0
    return {"total": total, "approved_raw": approved_raw, "unique": unique, "completed": completed}


def data_quality() -> dict:
    if not PG_LOG.exists():
        return {"score": 0, "fresh": 0, "skipped": 0, "total": 0}
    total = fresh = skipped = 0
    try:
        with PG_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    if rec.get("decision", {}).get("action") == "SKIPPED":
                        skipped += 1
                    else:
                        fresh += 1
                except Exception:
                    continue
    except Exception:
        pass
    fresh_rate = fresh / total if total else 0
    stale_rate = skipped / total if total else 0
    score = round(fresh_rate * 100 - stale_rate * 50, 1)
    return {"score": score, "fresh": fresh, "skipped": skipped, "total": total}


def capital_summary() -> dict:
    if not UBOT_DB.exists():
        return {"open": 0, "unrealized": 0.0}
    try:
        uri = f"file:{UBOT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0.0) FROM trade_journal WHERE exit_price IS NULL"
        )
        n, total = cur.fetchone()
        conn.close()
        return {"open": int(n), "unrealized": round(float(total), 4)}
    except Exception:
        return {"open": 0, "unrealized": 0.0}


def service_status() -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "position-guard"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def gate_check(pg: dict, action: dict, dq: dict) -> dict:
    pg_pass = pg["valid_move_sl"] >= 20
    action_pass = action["unique"] >= 20 and action["completed"] >= 10
    dq_pass = dq["score"] >= 90
    ready = (pg_pass or action_pass) and dq_pass
    reasons = []
    if not dq_pass:
        reasons.append(f"DQ={dq['score']}/100 (need ≥90)")
    if not pg_pass:
        reasons.append(f"PG valid_move_sl={pg['valid_move_sl']}/20")
    if not action_pass:
        reasons.append(f"Action unique={action['unique']}/20, completed={action['completed']}/10")
    return {"ready": ready, "reasons": reasons, "pg_pass": pg_pass, "action_pass": action_pass, "dq_pass": dq_pass}


def render(pg: dict, action: dict, dq: dict, cap: dict, gate: dict, svc: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    gate_emoji = "🟢 READY" if gate["ready"] else "🔴 NOT READY"
    lines = [
        "=" * 60,
        "  TRADINGOS — EXPERIMENT MONITOR",
        "=" * 60,
        f"  Generated: {now}",
        f"  Service:   {svc}",
        "",
        "  ── Decision Gate ──",
        f"  Status:     {gate_emoji}",
    ]
    if gate["reasons"]:
        for r in gate["reasons"]:
            lines.append(f"    BLOCKED: {r}")
    else:
        lines.append("    All conditions met")
    lines += [
        "",
        "  ── PG v1.1 ──",
        f"    Total evaluations:     {pg['total']}",
        f"    Valid MOVE_SL:          {pg['valid_move_sl']} / 20",
        f"    HOLD:                   {pg['hold']}",
        f"    SKIPPED (stale):        {pg['skipped']}",
        "",
        "  ── Action Shadow v0.1 ──",
        f"    PIE recs seen:          {action['total']}",
        f"    APPROVED (raw):         {action['approved_raw']}",
        f"    Unique actions:         {action['unique']} / 20",
        f"    Completed outcomes:     {action['completed']} / 10",
        "",
        "  ── Data Quality ──",
        f"    Score:                  {dq['score']} / 100  (target ≥90)",
        f"    Fresh:                  {dq['fresh']}",
        f"    Skipped:                {dq['skipped']}",
        "",
        "  ── Capital ──",
        f"    Open positions:         {cap['open']}",
        f"    Unrealized PnL:         {cap['unrealized']:+.4f} USDT",
        "",
        "=" * 60,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="TradingOS Experiment Monitor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    pg = pg_metrics()
    action = action_metrics()
    dq = data_quality()
    cap = capital_summary()
    svc = service_status()
    gate = gate_check(pg, action, dq)

    if args.json:
        print(json.dumps({
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "service": svc,
            "gate": gate,
            "pg": pg,
            "action": action,
            "data_quality": dq,
            "capital": cap,
        }, indent=2))
    else:
        print(render(pg, action, dq, cap, gate, svc))

    return 0


if __name__ == "__main__":
    sys.exit(main())
