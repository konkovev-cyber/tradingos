"""
control_plane/ceo/health_score.py
Overall system health calculation.
"""
from typing import Dict


def calculate_health_score(state: Dict, capital: Dict, decision: Dict) -> Dict:
    """
    Calculate overall system health (0-100).
    Higher = healthier.

    Components:
    - Capital health (30 points)
    - Risk management (25 points)
    - Experiment progress (25 points)
    - Data quality (20 points)
    """
    score = 0.0
    breakdown = {}

    # Capital health (30 points)
    cap_status = capital.get("health", "UNKNOWN")
    if cap_status == "HEALTHY":
        cap_score = 30
    elif cap_status == "WARNING":
        cap_score = 15
    else:
        cap_score = 0
    score += cap_score
    breakdown["capital"] = cap_score

    # Risk management (25 points)
    risk_score_val = capital.get("risk_score", 50)
    risk_score = max(0, 25 - (risk_score_val / 100 * 25))
    score += risk_score
    breakdown["risk"] = round(risk_score, 1)

    # Experiment progress (25 points)
    positions = state.get("positions", {})
    pg_signals = positions.get("guard_valid_signals", 0)
    action_unique = positions.get("action_shadow_unique", 0)
    pg_progress = min(pg_signals / 20, 1.0) * 15
    action_progress = min(action_unique / 20, 1.0) * 10
    exp_score = pg_progress + action_progress
    score += exp_score
    breakdown["experiments"] = round(exp_score, 1)

    # Data quality (20 points)
    dq = state.get("research", {}).get("evidence_score", 0)
    dq_score = min(dq / 100, 1.0) * 20
    score += dq_score
    breakdown["data_quality"] = round(dq_score, 1)

    total = round(score, 1)

    if total >= 70:
        health = "HEALTHY"
    elif total >= 40:
        health = "WARNING"
    else:
        health = "CRITICAL"

    return {
        "score": total,
        "health": health,
        "breakdown": breakdown,
    }
