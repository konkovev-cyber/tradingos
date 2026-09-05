"""
control_plane/decision/engine.py
Decision Engine v1 — reads Unified State, evaluates rules, produces recommendation.

Advisory only. No execution. No LIVE changes.
"""
import json
from pathlib import Path
from typing import Dict, List

from .models import RuleResult, SystemRecommendation
from .rules import ALL_RULES

STATE_PATH = Path("/root/tradingos/control_plane/snapshots/current_state.json")
REPORT_PATH = Path("/root/tradingos/control_plane/decision/decision_report.json")


def load_state() -> Dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open() as f:
            return json.load(f)
    except Exception:
        return {}


def evaluate_rules(state: Dict) -> List[RuleResult]:
    results = []
    for rule_fn in ALL_RULES:
        try:
            results.append(rule_fn(state))
        except Exception as e:
            results.append(RuleResult(
                rule_name=rule_fn.__name__,
                passed=False,
                score=0,
                reason=f"Rule error: {e}",
            ))
    return results


def determine_verdict(results: List[RuleResult]) -> SystemRecommendation:
    total_score = sum(r.score for r in results)
    max_possible = sum(20 for _ in results)  # approximate
    confidence = round(total_score / max(max_possible, 1), 2)

    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    reasons = [f"{r.rule_name}: {r.reason}" for r in results]
    allowed_actions = []

    if total_score >= 100 and len(failed) <= 1:
        decision = "APPROVE"
        allowed_actions = ["PROMOTE_TO_PAPER", "EXTEND_MEASUREMENT"]
    elif total_score >= 60:
        decision = "REVIEW"
        allowed_actions = ["EXTEND_MEASUREMENT", "ADJUST_POLICY"]
    elif total_score >= 30:
        decision = "WAIT"
        allowed_actions = ["CONTINUE_MEASUREMENT"]
    else:
        decision = "BLOCK"
        allowed_actions = ["FIX_DATA_QUALITY", "INVESTIGATE_ISSUES"]

    # Critical blocks always override
    critical_failures = [r for r in failed if r.score == 0]
    if critical_failures:
        decision = "BLOCK"
        allowed_actions = ["FIX_CRITICAL_ISSUES"]
        reasons.append(f"CRITICAL BLOCKERS: {[r.rule_name for r in critical_failures]}")

    return SystemRecommendation(
        decision=decision,
        confidence=confidence,
        reasons=reasons,
        allowed_actions=allowed_actions,
        rule_results=results,
        total_score=total_score,
    )


def run() -> SystemRecommendation:
    state = load_state()
    if not state:
        return SystemRecommendation(
            decision="BLOCK",
            confidence=0.0,
            reasons=["No state data available"],
            allowed_actions=["RUN_STATE_COLLECTOR"],
        )
    results = evaluate_rules(state)
    return determine_verdict(results)


def save_report(rec: SystemRecommendation) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "decision": rec.decision,
        "confidence": rec.confidence,
        "total_score": rec.total_score,
        "reasons": rec.reasons,
        "allowed_actions": rec.allowed_actions,
        "rules": [
            {
                "name": r.rule_name,
                "passed": r.passed,
                "score": r.score,
                "reason": r.reason,
            }
            for r in rec.rule_results
        ],
    }
    with REPORT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_PATH


def render_text(rec: SystemRecommendation) -> str:
    icon = {"APPROVE": "🟢", "REVIEW": "🟡", "WAIT": "⚪", "BLOCK": "🔴"}.get(rec.decision, "❓")
    lines = [
        "=" * 64,
        "  TRADINGOS — DECISION ENGINE v1",
        "=" * 64,
        f"  Decision:    {icon} {rec.decision}",
        f"  Confidence:  {rec.confidence:.0%}",
        f"  Total score: {rec.total_score}",
        "",
        "  ── Rule Results ──",
    ]
    for r in rec.rule_results:
        status = "✅" if r.passed else "❌"
        lines.append(f"    {status} {r.rule_name:25s} {r.score:3d}  {r.reason}")
    lines += [
        "",
        "  ── Allowed Actions ──",
    ]
    for a in rec.allowed_actions:
        lines.append(f"    → {a}")
    lines += [
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
