"""
control_plane/review/aggregator.py
Reads all Control Plane outputs and produces unified review.
Uses canonical KPI Framework v2 (K75) terminology.

Decision Review is a CONSUMER of TE/ESR/ERG.
It reads canonical sample counts from KPI Collector (Phase 6).
It does NOT compute or store TE/ESR/ERG values itself.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import KPISampleCounts, LocalSignal

STATE_PATH = Path("/root/tradingos/control_plane/snapshots/current_state.json")
CAPITAL_V2_PATH = Path("/root/tradingos/control_plane/capital/capital_intelligence_v2.json")
PORTFOLIO_PATH = Path("/root/tradingos/control_plane/portfolio/portfolio_intelligence.json")
DECISION_PATH = Path("/root/tradingos/control_plane/decision/decision_report.json")
APPROVAL_PATH = Path("/root/tradingos/control_plane/approval/approval.json")
PAPER_PATH = Path("/root/tradingos/control_plane/paper/paper_simulation.json")
REALITY_PATH = Path("/root/tradingos/control_plane/reality/reality_result.json")
REVIEW_PATH = Path("/root/tradingos/control_plane/review/decision_review_v2.json")
KPI_COLLECTOR_STATE = Path("/root/tradingos/control_plane/kpi_collector/state.json")
PG_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
ACTION_LOG = Path("/root/tradingos/logs/position_action_shadow.jsonl")


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return {}


def collect_action_stats() -> Dict:
    stats = {
        "pg_total": 0,
        "pg_valid_move_sl": 0,
        "pg_skipped": 0,
        "action_total": 0,
        "action_approved_raw": 0,
        "action_unique": 0,
    }

    if PG_LOG.exists():
        try:
            with PG_LOG.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    stats["pg_total"] += 1
                    action = rec.get("decision", {}).get("action", "?")
                    if action == "MOVE_SL" and rec.get("validation", {}).get("reconciled", False):
                        stats["pg_valid_move_sl"] += 1
                    elif action == "SKIPPED":
                        stats["pg_skipped"] += 1
        except Exception:
            pass

    if ACTION_LOG.exists():
        try:
            with ACTION_LOG.open() as f:
                seen = set()
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    stats["action_total"] += 1
                    if rec.get("action", {}).get("verdict") == "APPROVE":
                        stats["action_approved_raw"] += 1
                        key = (rec.get("symbol", ""), rec.get("side", ""), rec.get("action", {}).get("type", "?"))
                        seen.add(key)
            stats["action_unique"] = len(seen)
        except Exception:
            pass

    return stats


def collect_data_quality() -> Dict:
    state = _load_json(STATE_PATH)
    return {
        "score": state.get("research", {}).get("evidence_score", 0),
        "experiments": state.get("research", {}).get("active_experiments", 0),
    }


def collect_kpi_samples() -> KPISampleCounts:
    """
    READ sample counts from canonical KPI Collector state (Phase 6).
    Decision Review is a CONSUMER — it does NOT compute TE/ESR/ERG.
    If collector state doesn't exist, fall back to legacy action count.
    """
    collector = _load_json(KPI_COLLECTOR_STATE)
    if collector and "te" in collector and "esr" in collector and "erg" in collector:
        return KPISampleCounts(
            te=collector["te"].get("samples", 0),
            esr=collector["esr"].get("samples", 0),
            erg=collector["erg"].get("samples", 0),
        )
    stats = collect_action_stats()
    return KPISampleCounts(te=0, esr=0, erg=stats.get("action_unique", 0))


def collect_local_signals() -> Dict[str, LocalSignal]:
    """
    Per-decision LOCAL signal from Paper + Reality.
    NOT a system KPI — just a signal for the current position review.
    """
    paper = _load_json(PAPER_PATH)
    reality = _load_json(REALITY_PATH)
    signals = {}

    if not paper.get("scenarios"):
        return signals

    best_paper = max(paper.get("scenarios", []), key=lambda s: s.get("action_pnl", 0))
    paper_delta = best_paper.get("delta", 0)

    if not reality.get("available"):
        signals["current"] = LocalSignal(
            paper_delta=paper_delta,
            net_vs_hold=0,
            local_recommendation=best_paper.get("verdict", "NEUTRAL"),
        )
        return signals

    net_vs_hold = reality.get("vs_hold", 0)
    if net_vs_hold > 0.10:
        rec = "ACTION_BETTER"
    elif net_vs_hold < -0.10:
        rec = "HOLD_BETTER"
    else:
        rec = "NEUTRAL"

    signals[reality.get("symbol", "current")] = LocalSignal(
        paper_delta=paper_delta,
        net_vs_hold=net_vs_hold,
        local_recommendation=rec,
    )
    return signals


def collect_approval_stats() -> Dict:
    approvals = _load_json(APPROVAL_PATH)
    if not approvals:
        return {"total": 0, "approved": 0, "rejected": 0, "pending": 0}
    by_status = {}
    for r in approvals:
        s = r.get("status", "UNKNOWN")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "total": len(approvals),
        "approved": by_status.get("APPROVED", 0),
        "rejected": by_status.get("REJECTED", 0),
        "pending": by_status.get("PENDING", 0),
    }
