"""
core/state_reconciliation/safety_gate.py
Reconciliation Safety Gate v1 — blocks actions when state is uncertain.

Rules:
  SYNCED → ALLOW
  DRIFT → ALLOW + WARNING
  CONFLICT → BLOCK
  POSITION_CLOSED → BLOCK
  UNKNOWN → BLOCK

Read-only. No execution. Advisory only.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List

from .models import ReconciliationResult, ReconciliationStatus

logger = logging.getLogger("tradingos.safety_gate")


class SafetyGateVerdict:
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"
    EMERGENCY = "EMERGENCY"


def evaluate_safety(results: List[ReconciliationResult]) -> Dict:
    """
    Evaluate safety of actions based on reconciliation results.
    Returns overall verdict + per-symbol details.
    """
    now = datetime.now(timezone.utc).isoformat()
    per_symbol = {}
    overall_verdict = SafetyGateVerdict.ALLOW
    blockers = []
    warnings = []

    for r in results:
        if r.status == ReconciliationStatus.SYNCED:
            per_symbol[r.symbol] = {"verdict": SafetyGateVerdict.ALLOW, "reason": "SYNCED"}
        elif r.status == ReconciliationStatus.DRIFT:
            per_symbol[r.symbol] = {"verdict": SafetyGateVerdict.WARN, "reason": "DRIFT detected"}
            warnings.append(r.symbol)
            if overall_verdict == SafetyGateVerdict.ALLOW:
                overall_verdict = SafetyGateVerdict.WARN
        elif r.status in (ReconciliationStatus.CONFLICT, ReconciliationStatus.POSITION_CLOSED):
            per_symbol[r.symbol] = {"verdict": SafetyGateVerdict.BLOCK, "reason": r.status.value}
            blockers.append(r.symbol)
            overall_verdict = SafetyGateVerdict.BLOCK
        elif r.status == ReconciliationStatus.POSITION_OPENED:
            per_symbol[r.symbol] = {"verdict": SafetyGateVerdict.WARN, "reason": "NEW position on exchange"}
            warnings.append(r.symbol)
        else:
            per_symbol[r.symbol] = {"verdict": SafetyGateVerdict.BLOCK, "reason": "UNKNOWN state"}
            blockers.append(r.symbol)
            overall_verdict = SafetyGateVerdict.BLOCK

    return {
        "verdict": overall_verdict,
        "per_symbol": per_symbol,
        "blockers": blockers,
        "warnings": warnings,
        "timestamp": now,
        "rule": "BLOCK on CONFLICT/UNKNOWN/POSITION_CLOSED; WARN on DRIFT; ALLOW on SYNCED",
    }


def render_safety(safety: Dict) -> str:
    icon = {"ALLOW": "🟢", "WARN": "🟡", "BLOCK": "🔴"}.get(safety["verdict"], "?")
    lines = [
        "=" * 64,
        "  RECONCILIATION SAFETY GATE v1",
        "=" * 64,
        f"  Verdict:  {icon} {safety['verdict']}",
        f"  Blockers: {', '.join(safety['blockers']) or 'none'}",
        f"  Warnings: {', '.join(safety['warnings']) or 'none'}",
        "",
        "  Rules:",
        "    SYNCED         → ALLOW",
        "    DRIFT          → WARN",
        "    CONFLICT       → BLOCK",
        "    POSITION_CLOSED → BLOCK",
        "    UNKNOWN        → BLOCK",
        "",
        "  Per-symbol:",
    ]
    for sym, info in safety["per_symbol"].items():
        v_icon = {"ALLOW": "🟢", "WARN": "🟡", "BLOCK": "🔴"}.get(info["verdict"], "?")
        lines.append(f"    {v_icon} {sym:12s} {info['verdict']:8s} {info['reason']}")
    lines += [
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
