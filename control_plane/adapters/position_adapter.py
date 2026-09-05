"""
control_plane/adapters/position_adapter.py
Read-only adapter for Position Intelligence Layer.
Collects data from PIE DB, PG log, Action Shadow log.
"""
import json
import sqlite3
from pathlib import Path
from typing import Dict

from ..models import PositionState

PIE_DB = Path("/root/tradingos/tradingos_data.db")
PG_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
ACTION_LOG = Path("/root/tradingos/logs/position_action_shadow.jsonl")


def _count_active_positions() -> int:
    if not PIE_DB.exists():
        return 0
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT position_id) FROM position_events "
            "WHERE id IN (SELECT MAX(id) FROM position_events GROUP BY position_id)"
        )
        n = cur.fetchone()[0]
        conn.close()
        return n or 0
    except Exception:
        return 0


def _pg_metrics() -> Dict:
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
                    if action == "MOVE_SL" and rec.get("validation", {}).get("reconciled", False):
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


def _action_shadow_metrics() -> Dict:
    if not ACTION_LOG.exists():
        return {"total": 0, "approved_raw": 0, "unique": 0}
    total = approved_raw = 0
    seen: Dict[tuple, str] = {}
    try:
        with ACTION_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    total += 1
                    if rec.get("action", {}).get("verdict") == "APPROVE":
                        approved_raw += 1
                        key = (rec.get("symbol", ""), rec.get("side", ""), rec.get("action", {}).get("type", "?"))
                        ts = rec.get("timestamp", "")
                        if key not in seen or ts > seen[key]:
                            seen[key] = ts
                except Exception:
                    continue
    except Exception:
        pass
    return {"total": total, "approved_raw": approved_raw, "unique": len(seen)}


def collect() -> PositionState:
    pg = _pg_metrics()
    action = _action_shadow_metrics()
    return PositionState(
        open_count=_count_active_positions(),
        guard_mode="SHADOW",
        guard_valid_signals=pg["valid_move_sl"],
        action_shadow_unique=action["unique"],
        pending_actions=action["unique"],
    )
