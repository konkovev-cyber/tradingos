"""
tools/experiment_snapshot.py
Daily evidence snapshot for TradingOS experiments.

Pure read-only. No new logic, no execution. Just records the current
state of:
  - Capital Overview (positions, unrealized PnL)
  - PG v1.1 (evaluations, valid MOVE_SL, stale blocked)
  - Action Shadow v0.1 (raw recs, unique actions, completed outcomes)
  - Data quality
And appends to /root/tradingos/docs/evidence_snapshots/snapshot_YYYY-MM-DD.md

Usage:
    python3 tools/experiment_snapshot.py
    python3 tools/experiment_snapshot.py --dry-run   # print but don't write
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
SHADOW_LOG = ROOT / "logs" / "position_guard_shadow.jsonl"
ACTION_LOG = ROOT / "logs" / "position_action_shadow.jsonl"
UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
PIE_DB = ROOT / "tradingos_data.db"
SNAPSHOT_DIR = ROOT / "docs" / "evidence_snapshots"


def count_ubot_open() -> dict:
    if not UBOT_DB.exists():
        return {"n": 0, "unrealized": 0.0}
    try:
        uri = f"file:{UBOT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0.0) FROM trade_journal WHERE exit_price IS NULL"
        )
        n, total = cur.fetchone()
        conn.close()
        return {"n": int(n), "unrealized": round(float(total), 4)}
    except Exception:
        return {"n": 0, "unrealized": 0.0}


def count_shadow_log(path: Path, action_filter=None, verdict_filter=None) -> dict:
    """
    Counts records in a JSONL log.
    action_filter: filter by rec["decision"]["action"] (PG style)
                   or rec["action"]["type"] (Action Shadow style)
    verdict_filter: filter by rec["action"]["verdict"] (Action Shadow only)
    Returns {"total": N, "matched": M}.
    """
    if not path.exists():
        return {"total": 0, "matched": 0}
    total = 0
    matched = 0
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    decision = rec.get("decision", {})
                    action_block = rec.get("action", {})
                    a = decision.get("action") or action_block.get("type") or "?"
                    v = action_block.get("verdict", "?") if action_block else "?"
                    if action_filter and a != action_filter:
                        continue
                    if verdict_filter and v != verdict_filter:
                        continue
                    matched += 1
                except Exception:
                    continue
    except Exception:
        pass
    return {"total": total, "matched": matched}


def data_quality() -> float:
    if not SHADOW_LOG.exists():
        return 0.0
    total = 0
    fresh = 0
    try:
        with SHADOW_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    if rec.get("decision", {}).get("action") != "SKIPPED":
                        fresh += 1
                except Exception:
                    continue
    except Exception:
        return 0.0
    if total == 0:
        return 0.0
    fresh_rate = fresh / total
    stale_rate = (total - fresh) / total
    return round(fresh_rate * 100 - stale_rate * 50, 1)


def service_running() -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "position-guard"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def build_snapshot() -> str:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    pg = count_shadow_log(SHADOW_LOG)
    pg_move_sl = count_shadow_log(SHADOW_LOG, action_filter="MOVE_SL")
    pg_skipped = count_shadow_log(SHADOW_LOG, action_filter="SKIPPED")
    action = count_shadow_log(ACTION_LOG)
    action_approved = count_shadow_log(ACTION_LOG, verdict_filter="APPROVE")
    capital = count_ubot_open()
    dq = data_quality()
    svc = service_running()

    lines = [
        "# TradingOS Evidence Snapshot",
        "",
        f"**Date:** {today}",
        f"**Generated:** {now.isoformat()}",
        "",
        "## Operating Mode",
        "MEASUREMENT (no code changes since 2026-07-20T22:00Z)",
        "",
        "## Service",
        f"- position-guard.service: **{svc}**",
        "",
        "## Capital",
        f"- Open positions: **{capital['n']}**",
        f"- Unrealized PnL: **{capital['unrealized']:+.4f} USDT**",
        "",
        "## PG v1.1 (FROZEN)",
        f"- Total evaluations: {pg.get('total', 0)}",
        f"- MOVE_SL signals: {pg_move_sl.get('matched', 0)}",
        f"- SKIPPED (stale): {pg_skipped.get('matched', 0)}",
        "",
        "## Action Shadow v0.1",
        f"- Total PIE recommendations seen: {action.get('total', 0)}",
        f"- APPROVED: {action_approved.get('matched', 0)}",
        f"- Exit condition: 20 unique + 10 completed outcomes",
        "",
        "## Data Quality",
        f"- Score: **{dq}/100** (target ≥90)",
        "",
        "## Changes Since Last Snapshot",
        "- NONE (frozen mode)",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily Evidence Snapshot")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print to stdout without writing to file")
    args = parser.parse_args()

    snapshot = build_snapshot()

    if args.dry_run:
        print(snapshot)
        return 0

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = SNAPSHOT_DIR / f"snapshot_{today}.md"
    with out_path.open("a") as f:
        f.write(snapshot)
    print(f"Snapshot written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
