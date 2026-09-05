"""
core/rts_governor/controller.py
RTS Governor Controller — single decision engine for position management.

Assembles existing components into one decision loop:
  Reconciliation → Safety Gate → RTS Guard → Protection State → Risk Governor → KPI

Dry Run Mode: No real execution. Shows "what RTS would do and why".
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import (
    AllowedAction, DecisionLogEntry, PositionLifecycleState,
    ProtectionStatus, RTSDecision, SystemRiskState,
)

LOG_PATH = Path("/root/tradingos/control_plane/rts_governor/decision_log.jsonl")


def _log_entry(entry: DecisionLogEntry) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps({
            "timestamp": entry.timestamp,
            "position": entry.position,
            "action": entry.action,
            "reason": entry.reason,
            "previous_state": entry.previous_state,
            "new_state": entry.new_state,
            "risk_change": entry.risk_change,
            "pnl": entry.pnl,
            "notes": entry.notes,
        }) + "\n")


def determine_lifecycle(pnl: float, protection: ProtectionStatus, drawdown: float = 0) -> PositionLifecycleState:
    """Determine position lifecycle state from current metrics."""
    if drawdown > 5:
        return PositionLifecycleState.EMERGENCY
    if drawdown > 3:
        return PositionLifecycleState.DEFENSIVE
    if drawdown > 1:
        return PositionLifecycleState.WARNING
    if pnl > 2:
        return PositionLifecycleState.PROFIT_ZONE
    if protection.fully_protected:
        return PositionLifecycleState.PROTECTED
    return PositionLifecycleState.OPEN


def determine_risk_state(
    reconciliation_status: str,
    has_conflict: bool,
    has_protection: bool,
    daily_loss: float,
) -> SystemRiskState:
    """Determine system risk state."""
    if has_conflict or daily_loss < -5:
        return SystemRiskState.EMERGENCY
    if daily_loss < -2 or not has_protection:
        return SystemRiskState.DEFENSIVE
    if daily_loss < -1 or reconciliation_status in ("DRIFT", "POSITION_CLOSED"):
        return SystemRiskState.WARNING
    return SystemRiskState.NORMAL


def decide_action(state: RTSDecision) -> RTSDecision:
    """
    Decide what actions are allowed/blocked based on position + risk state.
    Pure logic — no side effects.
    """
    allowed = []
    blocked = []

    if state.risk_state in (SystemRiskState.EMERGENCY, SystemRiskState.DEFENSIVE):
        blocked.extend([AllowedAction.ALLOW, AllowedAction.MOVE_SL])
        if state.risk_state == SystemRiskState.EMERGENCY:
            allowed.append(AllowedAction.CLOSE)
        else:
            allowed.append(AllowedAction.ALERT)
        state.reason = f"Risk state {state.risk_state.value}: only protection actions"
        state.allowed_actions = allowed
        state.blocked_actions = blocked
        return state

    if state.lifecycle_state == PositionLifecycleState.NEW:
        if state.risk_state == SystemRiskState.WARNING:
            blocked.append(AllowedAction.ALLOW)
            allowed.append(AllowedAction.ALERT)
            state.reason = "System in WARNING: new positions blocked"
        else:
            allowed.append(AllowedAction.ALLOW)
            state.reason = "System ready: ALLOW"
        state.allowed_actions = allowed
        state.blocked_actions = blocked
        return state

    if not state.protection.fully_protected:
        allowed.append(AllowedAction.RESTORE_PROTECTION)
        allowed.append(AllowedAction.ALERT)
        blocked.append(AllowedAction.ALLOW)
        state.reason = "Protection missing: need SL/TP restore"
    else:
        allowed.append(AllowedAction.ALLOW)
        state.reason = "Position fully protected: ALLOW normal management"

    if state.lifecycle_state in (PositionLifecycleState.WARNING, PositionLifecycleState.DEFENSIVE):
        allowed.append(AllowedAction.MOVE_SL)
        allowed.append(AllowedAction.ALERT)

    state.allowed_actions = allowed
    state.blocked_actions = blocked
    return state


def evaluate_position(
    symbol: str,
    side: str,
    pnl: float,
    drawdown: float,
    has_sl: bool,
    has_tp: bool,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    reconciliation_status: str = "SYNCED",
    has_conflict: bool = False,
    daily_loss: float = 0.0,
) -> RTSDecision:
    """
    Evaluate one position through the entire RTS Governor pipeline.
    Dry-run safe: no side effects, no execution.
    """
    protection = ProtectionStatus(
        has_sl=has_sl, has_tp=has_tp,
        sl_price=sl_price, tp_price=tp_price,
        fully_protected=has_sl and has_tp,
    )

    lifecycle = determine_lifecycle(pnl, protection, drawdown)
    risk = determine_risk_state(reconciliation_status, has_conflict, protection.fully_protected, daily_loss)

    decision = RTSDecision(
        position_symbol=symbol,
        position_side=side,
        lifecycle_state=lifecycle,
        risk_state=risk,
        protection=protection,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return decide_action(decision)


async def full_evaluation_cycle(
    positions_data: List[Dict],
    reconciliation_results: Optional[List[Dict]] = None,
    daily_loss: float = 0.0,
) -> List[RTSDecision]:
    """
    Run full evaluation cycle for all positions.
    This is the main entry point for the RTS Governor.
    """
    decisions = []
    for pos in positions_data:
        symbol = pos.get("symbol", "")
        side = pos.get("side", "LONG")

        # Find reconciliation result for this symbol
        has_conflict = False
        recon_status = "SYNCED"
        if reconciliation_results:
            for r in reconciliation_results:
                if r.get("symbol") == symbol:
                    if r.get("status") in ("CONFLICT", "POSITION_CLOSED"):
                        has_conflict = True
                        recon_status = r.get("status", "CONFLICT")
                    elif r.get("status") == "DRIFT":
                        recon_status = "DRIFT"

        decision = evaluate_position(
            symbol=symbol,
            side=side,
            pnl=pos.get("pnl", 0.0),
            drawdown=pos.get("drawdown", 0.0),
            has_sl=pos.get("has_sl", False),
            has_tp=pos.get("has_tp", False),
            sl_price=pos.get("sl_price"),
            tp_price=pos.get("tp_price"),
            reconciliation_status=recon_status,
            has_conflict=has_conflict,
            daily_loss=daily_loss,
        )
        decisions.append(decision)

        # Log decision
        entry = DecisionLogEntry(
            timestamp=decision.timestamp,
            position=decision.position_symbol,
            action=decision.allowed_actions[0].value if decision.allowed_actions else "NONE",
            reason=decision.reason,
            previous_state="",
            new_state=decision.lifecycle_state.value,
            risk_change=f"{decision.risk_state.value}",
            pnl=pos.get("pnl", 0.0),
        )
        _log_entry(entry)

    return decisions


def render_decision(decision: RTSDecision) -> str:
    icons = {
        "NORMAL": "🟢", "WARNING": "🟡",
        "DEFENSIVE": "🔴", "EMERGENCY": "⛔",
        "NEW": "🆕", "OPEN": "📂", "PROTECTED": "🛡️",
        "PROFIT_ZONE": "💰", "CLOSED": "✅",
    }
    risk_icon = icons.get(decision.risk_state.value, "?")
    life_icon = icons.get(decision.lifecycle_state.value, "?")
    allowed = ", ".join(a.value for a in decision.allowed_actions) or "NONE"
    blocked = ", ".join(b.value for b in decision.blocked_actions) or "none"
    prot = "✅" if decision.protection.fully_protected else "❌"
    return (
        f"  {risk_icon} {decision.position_symbol:12s} {life_icon} lifecycle={decision.lifecycle_state.value:12s} "
        f"risk={decision.risk_state.value:10s} prot={prot} | "
        f"ALLOWED: {allowed:20s} BLOCKED: {blocked} | {decision.reason}"
    )


def render_governor_status(decisions: List[RTSDecision]) -> str:
    lines = [
        "=" * 70,
        "  RTS GOVERNOR v0.1 — Position Evaluation",
        "=" * 70,
        f"  Timestamp: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for d in decisions:
        lines.append(render_decision(d))

    blocks = [d for d in decisions if AllowedAction.BLOCK in d.blocked_actions or AllowedAction.ALLOW not in d.allowed_actions]
    if blocks:
        lines += ["", f"  ⚠️  {len(blocks)} position(s) BLOCKED:", ""]
        for b in blocks:
            lines.append(f"    🔴 {b.position_symbol}: {b.reason}")
    else:
        lines.append("")

    lines += [
        "  Mode: DRY RUN — no real execution",
        "  All decisions are advisory only",
        "=" * 70,
    ]
    return "\n".join(lines)
