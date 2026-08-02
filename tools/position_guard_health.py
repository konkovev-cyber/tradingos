"""
tools/position_guard_health.py
Read-only health check for Position Guard v1.1 service.

Does NOT touch Guard, PIE, or any LIVE component.
Just inspects:
  - systemd service status
  - last log timestamp
  - last decision recorded
  - JSONL growth rate

Usage:
    python3 tools/position_guard_health.py
    python3 tools/position_guard_health.py --json
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHADOW_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
SERVICE_NAME = "position-guard"


def get_service_status() -> dict:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        is_active = out.stdout.strip()
    except Exception as e:
        is_active = f"ERR:{e}"

    try:
        out = subprocess.run(
            ["systemctl", "show", SERVICE_NAME,
             "--property=ActiveEnterTimestamp,MainPID,MemoryCurrent,ExecMainStartTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        props = {}
        for line in out.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
    except Exception as e:
        props = {"error": str(e)}

    return {
        "is_active": is_active,
        "active_since": props.get("ActiveEnterTimestamp", "unknown"),
        "main_pid": props.get("MainPID", "unknown"),
        "memory_current": props.get("MemoryCurrent", "unknown"),
        "exec_main_start": props.get("ExecMainStartTimestamp", "unknown"),
    }


def get_log_stats() -> dict:
    if not SHADOW_LOG.exists():
        return {"exists": False}

    stat = SHADOW_LOG.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    last_decision = None
    last_decision_ts = None
    total_lines = 0
    last_5_actions = []
    try:
        with SHADOW_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    rec = json.loads(line)
                    ts_str = rec.get("timestamp")
                    action = rec.get("decision", {}).get("action", "?")
                    symbol = rec.get("symbol", "?")
                    last_5_actions.append({"action": action, "symbol": symbol})
                except Exception:
                    continue
        if last_5_actions:
            last_decision = last_5_actions[-1]
            with SHADOW_LOG.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        last_decision_ts = rec.get("timestamp")
                    except Exception:
                        continue
    except Exception as e:
        return {"exists": True, "error": str(e)}

    age_sec = (datetime.now(timezone.utc) - mtime).total_seconds()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "total_lines": total_lines,
        "last_modified_utc": mtime.isoformat(),
        "age_seconds": round(age_sec, 1),
        "last_decision": last_decision,
        "last_decision_ts": last_decision_ts,
        "last_5": last_5_actions[-5:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Position Guard Health Check")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    service = get_service_status()
    log_stats = get_log_stats()

    log_age = log_stats.get("age_seconds")
    log_fresh = log_age is not None and log_age < 180  # 3 minutes

    health = {
        "service": service,
        "log": log_stats,
        "log_fresh": log_fresh,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    if args.json:
        print(json.dumps(health, indent=2, default=str))
        return 0

    print("=" * 56)
    print("  POSITION GUARD — HEALTH CHECK")
    print("=" * 56)
    print(f"  Checked at:        {health['checked_at_utc']}")
    print()
    print("  Service:")
    print(f"    is_active:       {service.get('is_active')}")
    print(f"    active_since:    {service.get('active_since')}")
    print(f"    main_pid:        {service.get('main_pid')}")
    print(f"    memory_current:  {service.get('memory_current')}")
    print()
    print("  Shadow log:")
    if log_stats.get("exists"):
        print(f"    total_lines:     {log_stats.get('total_lines', 0)}")
        print(f"    last_modified:   {log_stats.get('last_modified_utc')}")
        print(f"    age_seconds:     {log_age}  ({'OK' if log_fresh else 'STALE'})")
        last = log_stats.get("last_decision") or {}
        print(f"    last_decision:   {last.get('action', '?')} on {last.get('symbol', '?')}")
        print(f"    last_decision_ts:{log_stats.get('last_decision_ts')}")
    else:
        print("    <no log file>")
    print()
    if log_stats.get("last_5"):
        print("  Last 5 decisions:")
        for d in log_stats["last_5"]:
            print(f"    {d['action']:8s} {d['symbol']}")
    print()
    print("=" * 56)

    return 0


if __name__ == "__main__":
    sys.exit(main())
