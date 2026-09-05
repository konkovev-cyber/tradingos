"""
control_plane/risk_governor/evaluator.py
Risk Governor — evaluates whether action is allowed.

Read-only. No execution. Advisory only.
Inspired by MT5 LiveGuard + TradingOS v2 Governor rules.
"""
from typing import List
from .models import RiskLimits, GovernorDecision, DailyState


def evaluate(
    limits: RiskLimits,
    daily: DailyState,
    proposed_position_pct: float = 0.0,
    current_positions: int = 0,
) -> GovernorDecision:
    """
    Core risk governor evaluation.
    Returns ALLOW / REDUCE / BLOCK.
    """
    reasons: List[str] = []

    # Kill switch check
    if daily.kill_switch_active:
        return GovernorDecision(
            verdict="BLOCK",
            confidence=1.0,
            reasons=["KILL_SWITCH_ACTIVE"],
            max_positions=0,
        )

    # Daily loss check
    if abs(daily.pnl_today) >= limits.kill_switch_loss_pct:
        return GovernorDecision(
            verdict="BLOCK",
            confidence=1.0,
            reasons=[f"DAILY_LOSS_KILL: {daily.pnl_today:+.2f}% >= {limits.kill_switch_loss_pct}%"],
            max_positions=0,
        )

    # Consecutive losses check (MT5 pattern: 3 consecutive → block)
    if daily.consecutive_losses >= 3:
        reasons.append(f"CONSECUTIVELOSSES: {daily.consecutive_losses} >= 3")
        return GovernorDecision(
            verdict="BLOCK",
            confidence=0.9,
            reasons=reasons,
            max_positions=0,
        )

    # Daily loss warning
    if abs(daily.pnl_today) >= limits.max_daily_loss_pct * 0.8:
        reasons.append(f"DAILY_LOSS_WARNING: {daily.pnl_today:+.2f}% near limit {limits.max_daily_loss_pct}%")

    # Position count check
    if current_positions >= limits.max_positions:
        reasons.append(f"MAX_POSITIONS: {current_positions} >= {limits.max_positions}")
        return GovernorDecision(
            verdict="BLOCK",
            confidence=0.9,
            reasons=reasons,
            max_positions=limits.max_positions,
        )

    # Position size check
    if proposed_position_pct > limits.max_position_pct:
        reasons.append(f"POSITION_SIZE: {proposed_position_pct:.1f}% > {limits.max_position_pct}%")
        return GovernorDecision(
            verdict="REDUCE",
            confidence=0.8,
            reasons=reasons,
            max_position_pct=limits.max_position_pct,
            max_positions=limits.max_positions,
        )

    # All checks passed
    if reasons:
        return GovernorDecision(
            verdict="ALLOW",
            confidence=0.9,
            reasons=reasons,
            max_position_pct=limits.max_position_pct,
            max_positions=limits.max_positions,
        )

    return GovernorDecision(
        verdict="ALLOW",
        confidence=1.0,
        reasons=["ALL_CHECKS_PASSED"],
        max_position_pct=limits.max_position_pct,
        max_positions=limits.max_positions,
    )
