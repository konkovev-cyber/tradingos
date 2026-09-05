"""
control_plane/decision/rules.py
Rule-based evaluation of TradingOS state.
Each rule checks one aspect and returns a RuleResult.
"""
from typing import Dict, List

from .models import RuleResult


def rule_data_quality(state: Dict) -> RuleResult:
    """DQ must be ≥90 for any decision."""
    dq = state.get("research", {}).get("evidence_score", 0)
    if dq >= 90:
        return RuleResult("DATA_QUALITY", True, 20, f"DQ {dq}/100 ≥ 90")
    elif dq >= 80:
        return RuleResult("DATA_QUALITY", False, 10, f"DQ {dq}/100 (need ≥90)")
    else:
        return RuleResult("DATA_QUALITY", False, 0, f"DQ {dq}/100 (critical)")


def rule_pg_progress(state: Dict) -> RuleResult:
    """PG must have ≥20 valid MOVE_SL signals."""
    pg = state.get("positions", {}).get("guard_valid_signals", 0)
    if pg >= 20:
        return RuleResult("PG_PROGRESS", True, 20, f"PG valid {pg}/20 ✓")
    elif pg >= 15:
        return RuleResult("PG_PROGRESS", False, 10, f"PG valid {pg}/20 (close)")
    else:
        return RuleResult("PG_PROGRESS", False, 5, f"PG valid {pg}/20 (far)")


def rule_action_shadow(state: Dict) -> RuleResult:
    """Action Shadow must have ≥20 unique + ≥10 completed."""
    unique = state.get("positions", {}).get("action_shadow_unique", 0)
    if unique >= 20:
        return RuleResult("ACTION_SHADOW", True, 20, f"Action unique {unique}/20 ✓")
    elif unique >= 10:
        return RuleResult("ACTION_SHADOW", False, 10, f"Action unique {unique}/20 (close)")
    else:
        return RuleResult("ACTION_SHADOW", False, 5, f"Action unique {unique}/20 (far)")


def rule_capital_health(state: Dict) -> RuleResult:
    """Capital must not be in severe drawdown."""
    cap = state.get("capital", {})
    status = cap.get("status", "UNKNOWN")
    unrealized = cap.get("total_unrealized", 0)
    if status == "PROFITABLE":
        return RuleResult("CAPITAL_HEALTH", True, 20, f"Capital profitable ({unrealized:+.2f})")
    elif status == "NEUTRAL":
        return RuleResult("CAPITAL_HEALTH", True, 15, f"Capital neutral ({unrealized:+.2f})")
    else:
        return RuleResult("CAPITAL_HEALTH", False, 5, f"Capital {status} ({unrealized:+.2f})")


def rule_risk_concentration(state: Dict) -> RuleResult:
    """No single position should be >70% of risk."""
    cap = state.get("capital", {})
    biggest_pct = cap.get("biggest_risk_pct", 0)
    biggest_sym = cap.get("biggest_risk_symbol", "")
    if biggest_pct <= 50:
        return RuleResult("RISK_CONCENTRATION", True, 20, f"Risk balanced (max {biggest_sym} {biggest_pct}%)")
    elif biggest_pct <= 70:
        return RuleResult("RISK_CONCENTRATION", False, 10, f"Risk concentration {biggest_sym} {biggest_pct}% (watch)")
    else:
        return RuleResult("RISK_CONCENTRATION", False, 0, f"CRITICAL: {biggest_sym} = {biggest_pct}% of risk")


def rule_execution_permission(state: Dict) -> RuleResult:
    """LIVE permission must be explicitly granted."""
    perm = state.get("execution", {}).get("live_permission", "NONE")
    if perm == "NONE":
        return RuleResult("EXECUTION_PERMISSION", False, 0, "Live permission: NONE")
    else:
        return RuleResult("EXECUTION_PERMISSION", True, 10, f"Live permission: {perm}")


def rule_services_healthy(state: Dict) -> RuleResult:
    """All services must be running."""
    exec_state = state.get("execution", {})
    running = exec_state.get("services_running", 0)
    total = exec_state.get("services_total", 1)
    if running == total:
        return RuleResult("SERVICES_HEALTHY", True, 10, f"All {total} services running")
    else:
        return RuleResult("SERVICES_HEALTHY", False, 0, f"Only {running}/{total} services running")


ALL_RULES = [
    rule_data_quality,
    rule_pg_progress,
    rule_action_shadow,
    rule_capital_health,
    rule_risk_concentration,
    rule_execution_permission,
    rule_services_healthy,
]
