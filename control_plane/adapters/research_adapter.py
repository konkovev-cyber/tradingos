"""
control_plane/adapters/research_adapter.py
Read-only adapter for Research State.
Collects data from tradingos_data.db and experiment state.
"""
import json
import sqlite3
from pathlib import Path

from ..models import ResearchState

PIE_DB = Path("/root/tradingos/tradingos_data.db")
PG_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
ACTION_LOG = Path("/root/tradingos/logs/position_action_shadow.jsonl")


def _dq_score() -> float:
    if not PG_LOG.exists():
        return 0.0
    total = fresh = 0
    try:
        with PG_LOG.open() as f:
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


def _pie_rec_count() -> int:
    if not PIE_DB.exists():
        return 0
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM live_assist_log")
        n = cur.fetchone()[0]
        conn.close()
        return n or 0
    except Exception:
        return 0


def collect() -> ResearchState:
    dq = _dq_score()
    pie_recs = _pie_rec_count()

    experiments = []
    if PG_LOG.exists():
        experiments.append("PG-v1.1-SHADOW-001")
    if ACTION_LOG.exists():
        experiments.append("Action-Shadow-v0.1")

    return ResearchState(
        status="COLLECTING",
        active_experiments=len(experiments),
        evidence_score=dq,
        experiments=experiments,
    )
