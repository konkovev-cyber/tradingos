"""
core/position/decision.py
Pure decision logic. No I/O, no logging, no side effects.
Input: PositionSnapshot + GuardConfig
Output: GuardDecision
"""
from .models import PositionSnapshot, GuardConfig, GuardDecision, Action, Side


def evaluate_position(
    snapshot: PositionSnapshot,
    config: GuardConfig,
) -> GuardDecision:
    """
    Evaluates a single position snapshot and returns a GuardDecision.
    This is a PURE FUNCTION. It must not access network, files, or global state.
    """
    if not config.enabled:
        return GuardDecision(
            action=Action.IGNORE,
            confidence=0.0,
            reasons=["guard_disabled"],
        )

    reasons = []
    score_components = 0
    total_components = 3

    if snapshot.pnl_pct >= config.pnl_trigger:
        reasons.append("PNL_TARGET_REACHED")
        score_components += 1
    else:
        reasons.append(f"PNL_BELOW_TRIGGER_{config.pnl_trigger}")

    if snapshot.mfe_pct >= config.mfe_trigger:
        reasons.append("MFE_CONFIRMED")
        score_components += 1
    else:
        reasons.append(f"MFE_BELOW_TRIGGER_{config.mfe_trigger}")

    if snapshot.health_score >= config.health_min:
        reasons.append("HEALTH_OK")
        score_components += 1
    else:
        reasons.append(f"HEALTH_BELOW_MIN_{config.health_min}")

    confidence = round(score_components / total_components, 2)

    if score_components == total_components:
        proposed_stop = _calculate_protection_stop(
            snapshot=snapshot,
            protect_pct=config.protect_profit_pct,
        )
        return GuardDecision(
            action=Action.MOVE_SL,
            confidence=confidence,
            reasons=reasons,
            current_stop=snapshot.stop_price,
            proposed_stop=proposed_stop,
        )

    return GuardDecision(
        action=Action.HOLD,
        confidence=confidence,
        reasons=reasons,
        current_stop=snapshot.stop_price,
        proposed_stop=None,
    )


def _calculate_protection_stop(
    snapshot: PositionSnapshot,
    protect_pct: float,
) -> float:
    """
    Calculates the new SL to protect profits.
    Moves SL to entry + protect_pct% (e.g., 0.5% profit locked in).
    """
    if snapshot.side == Side.LONG:
        return round(snapshot.entry_price * (1 + protect_pct / 100), 6)
    else:
        return round(snapshot.entry_price * (1 - protect_pct / 100), 6)
