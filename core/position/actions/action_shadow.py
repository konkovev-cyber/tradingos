"""
core/position/actions/action_shadow.py
Position Action Shadow v0.1 — orchestrator.

Wires together: PIE Source -> Policy -> Shadow Log.
No execution, no exchange, no HMAC. Pure observation.

Logs to /root/tradingos/logs/position_action_shadow.jsonl
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .models import ActionPolicy, PIERecommendation, PolicyVerdict, ShadowAction
from .policy import evaluate_action
from .pie_source import PIESource

logger = logging.getLogger("tradingos.action_shadow")

SHADOW_LOG_PATH = Path("/root/tradingos/logs/position_action_shadow.jsonl")


def _log_shadow_action(action: ShadowAction, rec: PIERecommendation) -> None:
    record = {
        "evaluation_id": str(uuid.uuid4()),
        "timestamp": action.timestamp.isoformat(),
        "rec_id": rec.rec_id,
        "symbol": rec.symbol,
        "side": rec.side,

        "source": "PIE",
        "snapshot_version": "v0.1",

        "pie_recommendation": {
            "type": rec.recommendation,
            "reason": rec.reason,
            "price": rec.price_at_recommendation,
            "pnl_pct": round(rec.pnl_at_recommendation * 100, 4),
            "mfe_pct": round(rec.mfe_at_recommendation * 100, 4),
            "health": round(rec.health_at_recommendation, 2),
            "rec_timestamp_utc": rec.timestamp.isoformat(),
        },

        "action": {
            "type": action.action.value,
            "verdict": action.verdict.value,
            "reasons": action.reasons,
            "proposed_quantity_pct": action.proposed_quantity_pct,
            "proposed_new_stop": action.proposed_new_stop,
        },

        "execution": {
            "mode": "SHADOW",
            "executed": False,
        },
    }

    SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHADOW_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")

    icon = "✅" if action.verdict == PolicyVerdict.APPROVE else "⛔"
    logger.info(
        f"{icon} {action.action.value} {action.verdict.value} | "
        f"{action.symbol} {action.side} | "
        f"PnL={rec.pnl_at_recommendation*100:+.2f}% MFE={rec.mfe_at_recommendation*100:+.2f}% "
        f"Health={rec.health_at_recommendation:.0f} | "
        f"Reasons: {','.join(action.reasons)}"
    )


class ActionShadow:
    """
    Orchestrator: one run() = one cycle over recent PIE recommendations.
    """

    def __init__(self, policy: ActionPolicy, pie_source: PIESource):
        self.policy = policy
        self.pie_source = pie_source

    def run(self, since_minutes: int = 60) -> List[ShadowAction]:
        recs = self.pie_source.fetch_recent(since_minutes=since_minutes)
        if not recs:
            logger.info("No PIE recommendations in the last "
                        f"{since_minutes} minutes")
            return []
        results: List[ShadowAction] = []
        for rec in recs:
            action = evaluate_action(rec, self.policy)
            _log_shadow_action(action, rec)
            results.append(action)
        n_approved = sum(1 for a in results if a.verdict == PolicyVerdict.APPROVE)
        logger.info(
            f"Action Shadow cycle complete: {len(recs)} recs, "
            f"{n_approved} approved, {len(recs) - n_approved} rejected"
        )
        return results
