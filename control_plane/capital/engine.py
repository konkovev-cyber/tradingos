"""
control_plane/capital/engine.py
Capital Intelligence Engine v2 — reads Unified State + lab CapitalEngine.

Advisory only. No execution. No position management.
v2 adds: CapitalEngine risk scoring, stability scoring, comprehensive analysis.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import CapitalIntelligence, RiskAlert
from .adapter import CapitalEngineAdapter

STATE_PATH = Path("/root/tradingos/control_plane/snapshots/current_state.json")
REPORT_PATH = Path("/root/tradingos/control_plane/capital/capital_intelligence.json")
REPORT_V2_PATH = Path("/root/tradingos/control_plane/capital/capital_intelligence_v2.json")


def load_state() -> Dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open() as f:
            return json.load(f)
    except Exception:
        return {}


def analyze_risk_concentration(capital: Dict) -> List[RiskAlert]:
    alerts = []
    biggest_sym = capital.get("biggest_risk_symbol", "")
    biggest_pct = capital.get("biggest_risk_pct", 0)

    if biggest_pct >= 70:
        alerts.append(RiskAlert(
            alert_type="CONCENTRATION_RISK",
            symbol=biggest_sym,
            severity="CRITICAL",
            value=biggest_pct,
            description=f"{biggest_sym} = {biggest_pct}% of total risk. Single position dependency.",
        ))
    elif biggest_pct >= 50:
        alerts.append(RiskAlert(
            alert_type="CONCENTRATION_RISK",
            symbol=biggest_sym,
            severity="HIGH",
            value=biggest_pct,
            description=f"{biggest_sym} = {biggest_pct}% of risk. Monitor closely.",
        ))
    elif biggest_pct >= 30:
        alerts.append(RiskAlert(
            alert_type="CONCENTRATION_RISK",
            symbol=biggest_sym,
            severity="MEDIUM",
            value=biggest_pct,
            description=f"{biggest_sym} = {biggest_pct}% of risk.",
        ))
    return alerts


def analyze_pnl_state(capital: Dict) -> List[RiskAlert]:
    alerts = []
    unrealized = capital.get("total_unrealized", 0)
    status = capital.get("status", "UNKNOWN")

    if status == "DRAWDOWN" and unrealized < -20:
        alerts.append(RiskAlert(
            alert_type="DRAWDOWN",
            symbol="PORTFOLIO",
            severity="CRITICAL",
            value=unrealized,
            description=f"Portfolio in deep drawdown: {unrealized:+.2f} USDT",
        ))
    elif status == "DRAWDOWN":
        alerts.append(RiskAlert(
            alert_type="DRAWDOWN",
            symbol="PORTFOLIO",
            severity="HIGH",
            value=unrealized,
            description=f"Portfolio in drawdown: {unrealized:+.2f} USDT",
        ))

    open_pos = capital.get("open_positions", 0)
    if open_pos > 15:
        alerts.append(RiskAlert(
            alert_type="OVEREXPOSURE",
            symbol="PORTFOLIO",
            severity="MEDIUM",
            value=float(open_pos),
            description=f"{open_pos} open positions. Consider reducing exposure.",
        ))

    return alerts


def generate_recommendations(capital: Dict, alerts: List[RiskAlert]) -> List[str]:
    recs = []
    critical = [a for a in alerts if a.severity == "CRITICAL"]
    high = [a for a in alerts if a.severity == "HIGH"]

    if critical:
        recs.append("IMMEDIATE: Address critical risk alerts before any new actions")
    if high:
        recs.append("MONITOR: High-severity alerts require attention")

    biggest_pct = capital.get("biggest_risk_pct", 0)
    if biggest_pct > 70:
        recs.append("REDUCE: Single position dependency > 70%")
    elif biggest_pct > 50:
        recs.append("WATCH: Single position > 50% of risk")

    unrealized = capital.get("total_unrealized", 0)
    if unrealized > 20:
        recs.append("PROTECT: Consider profit protection on largest winner")
    elif unrealized < -10:
        recs.append("REVIEW: Portfolio losing — assess position quality")

    if not recs:
        recs.append("HEALTHY: No immediate capital concerns")

    return recs


def calculate_risk_score(capital: Dict, alerts: List[RiskAlert]) -> float:
    """0-100, higher = more risk."""
    score = 0.0

    biggest_pct = capital.get("biggest_risk_pct", 0)
    if biggest_pct >= 70:
        score += 40
    elif biggest_pct >= 50:
        score += 25
    elif biggest_pct >= 30:
        score += 10

    status = capital.get("status", "UNKNOWN")
    if status == "DRAWDOWN":
        score += 30
    elif status == "NEUTRAL":
        score += 10

    open_pos = capital.get("open_positions", 0)
    if open_pos > 15:
        score += 15
    elif open_pos > 10:
        score += 5

    n_critical = sum(1 for a in alerts if a.severity == "CRITICAL")
    n_high = sum(1 for a in alerts if a.severity == "HIGH")
    score += n_critical * 10 + n_high * 5

    return min(score, 100.0)


def determine_health(risk_score: float) -> str:
    if risk_score >= 60:
        return "CRITICAL"
    elif risk_score >= 30:
        return "WARNING"
    else:
        return "HEALTHY"


def analyze() -> CapitalIntelligence:
    state = load_state()
    capital = state.get("capital", {})

    alerts = analyze_risk_concentration(capital) + analyze_pnl_state(capital)
    risk_score = calculate_risk_score(capital, alerts)
    health = determine_health(risk_score)
    recs = generate_recommendations(capital, alerts)

    return CapitalIntelligence(
        health=health,
        risk_score=round(risk_score, 1),
        unrealized_pnl=capital.get("total_unrealized", 0),
        realized_pnl=capital.get("total_realized", 0),
        open_positions=capital.get("open_positions", 0),
        closed_trades=capital.get("closed_trades", 0),
        biggest_risk_symbol=capital.get("biggest_risk_symbol", ""),
        biggest_risk_pct=capital.get("biggest_risk_pct", 0),
        alerts=alerts,
        recommendations=recs,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def save_report(ci: CapitalIntelligence) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": ci.timestamp,
        "health": ci.health,
        "risk_score": ci.risk_score,
        "unrealized_pnl": ci.unrealized_pnl,
        "realized_pnl": ci.realized_pnl,
        "open_positions": ci.open_positions,
        "closed_trades": ci.closed_trades,
        "biggest_risk_symbol": ci.biggest_risk_symbol,
        "biggest_risk_pct": ci.biggest_risk_pct,
        "alerts": [
            {"type": a.alert_type, "symbol": a.symbol, "severity": a.severity,
             "value": a.value, "description": a.description}
            for a in ci.alerts
        ],
        "recommendations": ci.recommendations,
    }
    with REPORT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_PATH


def render_text(ci: CapitalIntelligence) -> str:
    icon = {"HEALTHY": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(ci.health, "⚪")
    lines = [
        "=" * 64,
        "  TRADINGOS — CAPITAL INTELLIGENCE v1",
        "=" * 64,
        f"  Health:      {icon} {ci.health}",
        f"  Risk score:  {ci.risk_score}/100",
        f"  Timestamp:   {ci.timestamp}",
        "",
        "  ── Portfolio ──",
        f"  Unrealized:  {ci.unrealized_pnl:+.4f} USDT",
        f"  Realized:    {ci.realized_pnl:+.4f} USDT",
        f"  Positions:   {ci.open_positions}",
        f"  Closed:      {ci.closed_trades}",
        "",
        "  ── Risk ──",
        f"  Biggest risk: {ci.biggest_risk_symbol} ({ci.biggest_risk_pct}%)",
        "",
    ]
    if ci.alerts:
        lines.append("  ── Alerts ──")
        for a in ci.alerts:
            sev_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}.get(a.severity, "❓")
            lines.append(f"    {sev_icon} [{a.severity:8s}] {a.alert_type}: {a.description}")
        lines.append("")
    lines.append("  ── Recommendations ──")
    for r in ci.recommendations:
        lines.append(f"    → {r}")
    lines += [
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)


# === v2: CapitalEngine integration ===

def analyze_v2() -> Dict:
    """Enhanced analysis using lab CapitalEngine."""
    state = load_state()
    if not state:
        return {"error": "No state data"}

    capital = state.get("capital", {})
    execution = state.get("execution", {})

    adapter = CapitalEngineAdapter()

    risk_score = adapter.score_risk_from_state(capital)
    stability_score = adapter.score_stability_from_state(execution)
    v2_score = adapter.score_v2_from_state(state)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_score": {
            "total": round(v2_score.total, 1) if v2_score else None,
            "status": v2_score.status if v2_score else "NO_DATA",
            "risk_score": round(risk_score, 1) if risk_score is not None else None,
            "stability_score": round(stability_score, 1) if stability_score is not None else None,
            "breakdown": v2_score.breakdown if v2_score else {},
            "lab_available": adapter._engine is not None,
        },
        "portfolio": {
            "unrealized_pnl": capital.get("total_unrealized", 0),
            "open_positions": capital.get("open_positions", 0),
            "biggest_risk_symbol": capital.get("biggest_risk_symbol", ""),
            "biggest_risk_pct": capital.get("biggest_risk_pct", 0),
            "status": capital.get("status", "UNKNOWN"),
        },
        "alerts": [],
        "recommendations": [],
    }

    # Add alerts based on CapitalEngine scores
    if risk_score is not None and risk_score < 30:
        result["alerts"].append({
            "type": "LOW_RISK_SCORE",
            "severity": "HIGH",
            "value": round(risk_score, 1),
            "description": f"CapitalEngine risk score {risk_score:.1f}/100 (below 30)",
        })

    if v2_score and v2_score.status == "NOT_READY":
        result["alerts"].append({
            "type": "CAPITAL_NOT_READY",
            "severity": "MEDIUM",
            "value": v2_score.total,
            "description": f"CapitalEngine status: NOT_READY (score {v2_score.total:.1f})",
        })

    biggest_pct = capital.get("biggest_risk_pct", 0)
    if biggest_pct > 70:
        result["alerts"].append({
            "type": "CONCENTRATION_RISK",
            "severity": "CRITICAL",
            "value": biggest_pct,
            "description": f"{capital.get('biggest_risk_symbol', '')} = {biggest_pct}% of risk",
        })

    # Recommendations
    if risk_score is not None and risk_score < 50:
        result["recommendations"].append("IMPROVE_RISK_PROFILE")
    if biggest_pct > 70:
        result["recommendations"].append("REDUCE_CONCENTRATION")
    if stability_score is not None and stability_score < 80:
        result["recommendations"].append("IMPROVE_STABILITY")
    if not result["recommendations"]:
        result["recommendations"].append("MONITOR")

    return result


def save_report_v2(data: Dict) -> Path:
    REPORT_V2_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_V2_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_V2_PATH


def render_text_v2(data: Dict) -> str:
    score = data.get("capital_score", {})
    port = data.get("portfolio", {})
    alerts = data.get("alerts", [])
    recs = data.get("recommendations", [])

    total = score.get("total")
    status = score.get("status", "NO_DATA")
    risk = score.get("risk_score")
    stability = score.get("stability_score")

    status_icon = {"READY": "🟢", "CONDITIONAL": "🟡", "NOT_READY": "🔴"}.get(status, "⚪")

    lines = [
        "=" * 64,
        "  TRADINGOS — CAPITAL INTELLIGENCE v2",
        "  (with lab CapitalEngine integration)",
        "=" * 64,
        f"  Timestamp:  {data.get('timestamp', '')}",
        f"  Lab engine: {'available' if score.get('lab_available') else 'NOT AVAILABLE'}",
        "",
        "  ── CapitalEngine Score ──",
        f"  Total:       {total if total is not None else 'n/a'}/100",
        f"  Status:      {status_icon} {status}",
        f"  Risk:        {risk if risk is not None else 'n/a'}/100",
        f"  Stability:   {stability if stability is not None else 'n/a'}/100",
    ]
    if score.get("breakdown"):
        lines.append("  Breakdown:")
        for k, v in score["breakdown"].items():
            lines.append(f"    {k:25s} {v}")
    lines += [
        "",
        "  ── Portfolio ──",
        f"  Unrealized:  {port.get('unrealized_pnl', 0):+.4f} USDT",
        f"  Positions:   {port.get('open_positions', 0)}",
        f"  Biggest:     {port.get('biggest_risk_symbol', '')} ({port.get('biggest_risk_pct', 0)}%)",
        f"  Status:      {port.get('status', 'UNKNOWN')}",
        "",
    ]
    if alerts:
        lines.append("  ── Alerts ──")
        for a in alerts:
            sev = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}.get(a.get("severity", ""), "❓")
            lines.append(f"    {sev} [{a.get('severity', ''):8s}] {a.get('description', '')}")
        lines.append("")
    lines.append("  ── Recommendations ──")
    for r in recs:
        lines.append(f"    → {r}")
    lines += [
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
