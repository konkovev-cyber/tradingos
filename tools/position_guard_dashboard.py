"""
tools/position_guard_dashboard.py
Control Plane View for Position Guard.

Pure read-only. No service interaction, no log writes.
Aggregates from:
  - systemd (position-guard.service status)
  - /root/tradingos/logs/position_guard_shadow.jsonl (decisions)
  - /root/tradingos/tradingos_data.db (PIE active count)

Usage:
    python3 tools/position_guard_dashboard.py
    python3 tools/position_guard_dashboard.py --json
    python3 tools/position_guard_dashboard.py --watch 30   # refresh every 30s
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SHADOW_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
PIE_DB = Path("/root/tradingos/tradingos_data.db")
SERVICE = "position-guard"
EXPERIMENT_ID = "PG-v1.1-SHADOW-001"


def service_status() -> dict:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        is_active = out.stdout.strip()
    except Exception:
        is_active = "unknown"

    try:
        out = subprocess.run(
            ["systemctl", "show", SERVICE,
             "--property=ActiveEnterTimestamp,MainPID"],
            capture_output=True, text=True, timeout=5,
        )
        props = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    except Exception:
        props = {}

    return {
        "is_active": is_active,
        "active_since": props.get("ActiveEnterTimestamp", "unknown"),
        "main_pid": props.get("MainPID", "unknown"),
    }


def pie_active_count() -> int:
    if not PIE_DB.exists():
        return -1
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(DISTINCT position_id) FROM position_events
            WHERE id IN (SELECT MAX(id) FROM position_events GROUP BY position_id)
            """
        )
        n = cur.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return -1


def shadow_log_summary(since_minutes: int = 1440) -> dict:
    """Суммаризация JSONL за последние N минут (по умолчанию 24ч)."""
    if not SHADOW_LOG.exists():
        return {"exists": False, "total": 0, "by_action": {}, "fresh": 0, "skipped": 0}
    cutoff = datetime.now(timezone.utc).timestamp() - since_minutes * 60
    by_action: dict = {}
    fresh = 0
    skipped = 0
    total = 0
    try:
        with SHADOW_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rec_ts = rec.get("timestamp", "")
                    rec_dt = datetime.fromisoformat(rec_ts.replace("Z", "+00:00"))
                    if rec_dt.timestamp() < cutoff:
                        continue
                    total += 1
                    action = rec.get("decision", {}).get("action", "?")
                    by_action[action] = by_action.get(action, 0) + 1
                    if action == "SKIPPED":
                        skipped += 1
                    else:
                        fresh += 1
                except Exception:
                    continue
    except Exception:
        pass

    fresh_rate = (fresh / total) if total else 0.0
    return {
        "exists": True,
        "total": total,
        "fresh": fresh,
        "skipped": skipped,
        "fresh_rate": round(fresh_rate, 4),
        "by_action": by_action,
    }


def data_quality_score(s: dict) -> float:
    if s.get("total", 0) == 0:
        return 0.0
    return round(s["fresh_rate"] * 100 - (s["skipped"] / s["total"]) * 50, 1)


def render(view: dict) -> str:
    svc = view["service"]
    log = view["log"]
    dq = view["data_quality_score"]
    pie = view["pie_active_positions"]
    move_sl = log["by_action"].get("MOVE_SL", 0)
    hold = log["by_action"].get("HOLD", 0)
    skipped = log["by_action"].get("SKIPPED", 0)

    service_emoji = "🟢" if svc["is_active"] == "active" else "🔴"

    return f"""
================================================================
 POSITION GUARD — CONTROL PLANE VIEW
================================================================
 Status:            {service_emoji} {svc['is_active'].upper()}  (pid={svc['main_pid']})
 Active since:      {svc['active_since']}

 Experiment:        {EXPERIMENT_ID}
 Mode:              SHADOW (no execution)

 Data quality:      {dq}/100
 PIE positions:     {pie} active in DB
 Log total (24h):   {log['total']}

 Decisions:
   HOLD:            {hold}
   MOVE_SL:         {move_sl}
   SKIPPED:         {skipped}

 Next milestone:
   20 valid MOVE_SL
   OR
   5 closed-after-MOVE_SL outcomes

 Generated:         {view['generated_at_utc']}
================================================================
""".strip()


def build_view() -> dict:
    log = shadow_log_summary(since_minutes=1440)
    return {
        "service": service_status(),
        "pie_active_positions": pie_active_count(),
        "log": log,
        "data_quality_score": data_quality_score(log),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Position Guard Dashboard")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                        help="Refresh every N seconds (Ctrl-C to exit)")
    args = parser.parse_args()

    if args.watch > 0:
        try:
            while True:
                view = build_view()
                if args.json:
                    print(json.dumps(view, indent=2))
                else:
                    print(render(view))
                print()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0

    view = build_view()
    if args.json:
        print(json.dumps(view, indent=2))
    else:
        print(render(view))
    return 0


if __name__ == "__main__":
    sys.exit(main())
