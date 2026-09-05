"""
core/position/position_guard.py
Orchestrator for the Position Guard cycle.
Wires together: PIE Bridge -> Reconciler -> Decision -> Shadow Log.
This is the Control Plane component.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from adapters.bingx.client import BingXAdapter
from core.position.models import (
    Action,
    GuardConfig,
    GuardDecision,
    PositionSnapshot,
    PositionStatus,
)
from core.position.decision import evaluate_position
from core.position.reconciler import PositionReconciler, ReconciledSnapshot

logger = logging.getLogger("tradingos.position_guard")


SHADOW_LOG_PATH = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
EXCLUDED_LOG_PATH = Path("/root/tradingos/logs/position_guard_excluded.jsonl")
DEFAULT_PIE_DB = Path("/root/tradingos/tradingos_data.db")


def _log_shadow_decision(
    snapshot: PositionSnapshot,
    decision: GuardDecision,
    evaluation_id: str,
    source: str = "PIE",
    validation: Optional[dict] = None,
) -> None:
    """
    Persists the decision in a structured JSONL format
    designed for later Evidence Ledger / Analytics.
    """
    record = {
        "evaluation_id": evaluation_id,
        "timestamp": decision.timestamp.isoformat(),
        "exchange": snapshot.exchange,
        "symbol": snapshot.symbol,
        "side": snapshot.side.value,

        "source": source,
        "snapshot_version": "v1",

        "validation": validation or {"pie": "ACTIVE", "bingx": "ACTIVE", "reconciled": True},

        "snapshot": {
            "pnl_pct": round(snapshot.pnl_pct, 4),
            "mfe_pct": round(snapshot.mfe_pct, 4),
            "health": round(snapshot.health_score, 2),
            "mark_price": snapshot.mark_price,
        },

        "decision": {
            "action": decision.action.value,
            "reasons": decision.reasons,
            "confidence": decision.confidence,
            "proposed_stop": decision.proposed_stop,
        },

        "execution": {
            "mode": "SHADOW",
            "executed": False,
        },
    }

    SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")

    logger.info(
        f"SHADOW ACTION: {snapshot.symbol} {snapshot.side.value} | "
        f"PnL: {snapshot.pnl_pct:+.2f}% MFE: {snapshot.mfe_pct:+.2f}% "
        f"Health: {snapshot.health_score:.0f} | "
        f"Decision: {decision.action.value} "
        f"Reasons: {','.join(decision.reasons)} | "
        f"Execution: BLOCKED"
    )


def _log_excluded(reconciled: ReconciledSnapshot, evaluation_id: str) -> None:
    """Log snapshots excluded by reconciler to a separate file."""
    snap = reconciled.snapshot
    record = {
        "evaluation_id": evaluation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": snap.symbol,
        "side": snap.side.value,
        "validation": {
            "pie": "ACTIVE" if reconciled.pie_active else "INACTIVE",
            "bingx": "ACTIVE" if reconciled.bingx_active else "CLOSED",
            "reconciled": False,
            "status": reconciled.validation.value,
            "reason": reconciled.reason,
        },
        "snapshot": {
            "pnl_pct": round(snap.pnl_pct, 4),
            "mfe_pct": round(snap.mfe_pct, 4),
            "health": round(snap.health_score, 2),
        },
        "excluded_from_guard": True,
    }
    EXCLUDED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXCLUDED_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    logger.info(
        f"EXCLUDED: {snap.symbol} validation={reconciled.validation.value} reason={reconciled.reason}"
    )


class PositionGuard:
    """
    The main orchestrator. One run() invocation = one observation cycle.
    Uses PIEBridge → Reconciler → Decision → Shadow Log.
    """

    def __init__(
        self,
        config: GuardConfig,
        adapter: BingXAdapter,
        bridge,
        reconciler: PositionReconciler,
    ):
        self.config = config
        self.adapter = adapter
        self.bridge = bridge
        self.reconciler = reconciler

    def run(self) -> None:
        """
        Executes a single cycle:
        1. Read PIE snapshots (via bridge) — already has status field
        2. Snapshots with status=STALE or CLOSED_CONFIRMED are skipped
           and logged as SKIPPED (decision.action=SKIPPED, reason given)
        3. Active snapshots go through Reconciler
        4. For VALID snapshots: evaluate via Decision Engine, log, route MOVE_SL to simulate
        5. For Reconciler-excluded: log to excluded file
        """
        pie_snapshots: List[PositionSnapshot] = self.bridge.get_positions()

        if not pie_snapshots:
            logger.info("No PIE snapshots to evaluate this cycle")
            return

        active_for_guard: List[PositionSnapshot] = []
        for snap in pie_snapshots:
            if snap.status in (PositionStatus.STALE, PositionStatus.CLOSED_CONFIRMED):
                self._log_skipped(snap, reason=snap.status.value)
            else:
                active_for_guard.append(snap)

        if not active_for_guard:
            logger.info("No ACTIVE positions after stale filter")
            return

        reconciled_list = self.reconciler.reconcile(active_for_guard)

        for rec in reconciled_list:
            evaluation_id = str(uuid.uuid4())
            snap = rec.snapshot

            if not rec.guard_allowed:
                _log_excluded(rec, evaluation_id)
                continue

            decision = evaluate_position(snap, self.config)
            validation_meta = {
                "pie": "ACTIVE" if rec.pie_active else "INACTIVE",
                "bingx": "ACTIVE" if rec.bingx_active else "CLOSED",
                "reconciled": True,
            }
            _log_shadow_decision(
                snap, decision, evaluation_id,
                source="PIE", validation=validation_meta,
            )

            if decision.action == Action.MOVE_SL:
                self.adapter.simulate_modify_stop(
                    symbol=snap.symbol,
                    proposed_stop=decision.proposed_stop,
                    current_stop=decision.current_stop,
                )

    def _log_skipped(self, snap: PositionSnapshot, reason: str) -> None:
        """
        Logs a SKIPPED evaluation for snapshots that the bridge marked as
        STALE or CLOSED_CONFIRMED. These never reach the Decision Engine.
        They go to the shadow log with action=SKIPPED for evidence/audit.
        """
        record = {
            "evaluation_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exchange": snap.exchange,
            "symbol": snap.symbol,
            "side": snap.side.value,

            "source": "PIE",
            "snapshot_version": "v1",

            "validation": {
                "pie": snap.status.value,
                "bingx": "UNKNOWN",
                "reconciled": False,
                "status": snap.status.value,
                "reason": reason,
            },

            "snapshot": {
                "pnl_pct": round(snap.pnl_pct, 4),
                "mfe_pct": round(snap.mfe_pct, 4),
                "health": round(snap.health_score, 2),
                "mark_price": snap.mark_price,
            },

            "decision": {
                "action": Action.SKIPPED.value,
                "reasons": [f"BRIDGE_STATUS_{snap.status.value}"],
                "confidence": 0.0,
                "proposed_stop": None,
            },

            "execution": {
                "mode": "SHADOW",
                "executed": False,
            },
        }
        SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SHADOW_LOG_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
        logger.info(
            f"SKIPPED: {snap.symbol} status={snap.status.value} reason={reason}"
        )
