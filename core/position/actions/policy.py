"""
core/position/actions/policy.py
Pure policy decision: should we (virtually) act on this PIE recommendation?

No I/O. No logs. Just PIERecommendation + ActionPolicy -> ShadowAction.
"""
from typing import List

from .models import (
    ActionType,
    ActionPolicy,
    PIERecommendation,
    PolicyVerdict,
    ShadowAction,
)


def evaluate_action(
    rec: PIERecommendation,
    policy: ActionPolicy,
) -> ShadowAction:
    """
    Pure function. Returns ShadowAction with verdict (APPROVE/REJECT)
    and proposed action parameters.
    """
    base = ShadowAction(
        action=ActionType.NO_ACTION,
        verdict=PolicyVerdict.REJECT,
        rec_id=rec.rec_id,
        symbol=rec.symbol,
        side=rec.side,
        reasons=[],
    )

    if not policy.enabled:
        base.reasons.append("POLICY_DISABLED")
        return base

    rec_type = (rec.recommendation or "").upper()
    if rec_type not in ("MOVE_SL_BE", "TAKE_PARTIAL"):
        base.reasons.append(f"UNSUPPORTED_REC_TYPE_{rec_type}")
        return base

    if rec.age_hours > policy.max_recommendation_age_hours:
        base.reasons.append(
            f"REC_TOO_OLD: age={rec.age_hours:.1f}h > {policy.max_recommendation_age_hours}h"
        )
        return base

    if rec.health_at_recommendation < policy.min_health:
        base.reasons.append(
            f"LOW_HEALTH: {rec.health_at_recommendation:.0f} < {policy.min_health}"
        )
        return base

    pnl_pct = rec.pnl_at_recommendation * 100.0
    if pnl_pct < policy.min_pnl_pct_to_act:
        base.reasons.append(
            f"PNL_TOO_LOW: {pnl_pct:+.2f}% < {policy.min_pnl_pct_to_act}%"
        )
        return base

    reasons: List[str] = [
        f"REC_TYPE_{rec_type}",
        f"AGE_OK_{rec.age_hours:.1f}h",
        f"HEALTH_OK_{rec.health_at_recommendation:.0f}",
        f"PNL_OK_{pnl_pct:+.2f}%",
    ]

    if rec_type == "MOVE_SL_BE":
        return ShadowAction(
            action=ActionType.MOVE_SL_BREAKEVEN,
            verdict=PolicyVerdict.APPROVE,
            rec_id=rec.rec_id,
            symbol=rec.symbol,
            side=rec.side,
            reasons=reasons,
            proposed_quantity_pct=None,
            proposed_new_stop=rec.price_at_recommendation * (
                1.0 + policy.breakeven_buffer_pct / 100.0
            ) if rec.side == "BUY" else rec.price_at_recommendation * (
                1.0 - policy.breakeven_buffer_pct / 100.0
            ),
        )

    if rec_type == "TAKE_PARTIAL":
        return ShadowAction(
            action=ActionType.TAKE_PARTIAL,
            verdict=PolicyVerdict.APPROVE,
            rec_id=rec.rec_id,
            symbol=rec.symbol,
            side=rec.side,
            reasons=reasons,
            proposed_quantity_pct=policy.partial_close_pct,
            proposed_new_stop=None,
        )

    base.reasons.append(f"UNHANDLED_REC_TYPE_{rec_type}")
    return base
