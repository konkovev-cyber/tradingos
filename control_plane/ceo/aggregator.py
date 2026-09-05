"""
control_plane/ceo/aggregator.py
CEO Control Plane — reads all JSON reports, produces unified CEO state.

Advisory only. No execution. No LIVE changes.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .models import CEOState, ExperimentStatus
from .health_score import calculate_health_score

STATE_PATH = Path("/root/tradingos/control_plane/snapshots/current_state.json")
CAPITAL_PATH = Path("/root/tradingos/control_plane/capital/capital_intelligence.json")
DECISION_PATH = Path("/root/tradingos/control_plane/decision/decision_report.json")
CEO_PATH = Path("/root/tradingos/control_plane/ceo/ceo_state.json")


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return json.load(f)
    except Exception:
        return {}


def _extract_blockers(decision: Dict) -> List[str]:
    blockers = []
    for rule in decision.get("rules", []):
        if not rule.get("passed", True) and rule.get("score", 0) == 0:
            blockers.append(rule.get("name", "UNKNOWN"))
    return blockers


def _build_experiments(state: Dict) -> List[ExperimentStatus]:
    experiments = []
    positions = state.get("positions", {})
    research = state.get("research", {})

    pg_signals = positions.get("guard_valid_signals", 0)
    pg_status = "COLLECTING"
    if pg_signals >= 20:
        pg_status = "READY"
    experiments.append(ExperimentStatus(
        name="PG v1.1",
        progress=f"{pg_signals}/20",
        target="20 valid MOVE_SL",
        status=pg_status,
    ))

    action_unique = positions.get("action_shadow_unique", 0)
    action_status = "COLLECTING"
    if action_unique >= 20:
        action_status = "READY"
    experiments.append(ExperimentStatus(
        name="Action Shadow v0.1",
        progress=f"{action_unique}/20",
        target="20 unique + 10 completed",
        status=action_status,
    ))

    return experiments


def aggregate() -> CEOState:
    state = _load_json(STATE_PATH)
    capital = _load_json(CAPITAL_PATH)
    decision = _load_json(DECISION_PATH)

    if not state:
        return CEOState(
            system_status="NO_DATA",
            summary="No state data. Run state_collector first.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    blockers = _extract_blockers(decision)
    experiments = _build_experiments(state)
    health = calculate_health_score(state, capital, decision)

    cap = state.get("capital", {})
    positions = state.get("positions", {})

    cap_pnl = f"{cap.get('total_unrealized', 0):+.4f} USDT"
    cap_status = cap.get("status", "UNKNOWN")
    biggest = f"{cap.get('biggest_risk_symbol', '')} ({cap.get('biggest_risk_pct', 0)}%)"

    risk_level = "LOW"
    if capital.get("risk_score", 0) >= 60:
        risk_level = "CRITICAL"
    elif capital.get("risk_score", 0) >= 30:
        risk_level = "HIGH"

    next_step = decision.get("allowed_actions", ["UNKNOWN"])[0] if decision.get("allowed_actions") else "UNKNOWN"

    summary = (
        f"Capital: {cap_pnl} ({cap_status}) | "
        f"Positions: {positions.get('open_count', 0)} | "
        f"Risk: {risk_level} | "
        f"Health: {health['health']} ({health['score']}/100)"
    )

    return CEOState(
        system_status=decision.get("decision", "UNKNOWN"),
        overall_health=health["health"],
        capital_pnl=cap_pnl,
        capital_status=cap_status,
        positions_count=positions.get("open_count", 0),
        risk_level=risk_level,
        biggest_exposure=biggest,
        experiments=experiments,
        blockers=blockers,
        next_allowed_step=next_step,
        summary=summary,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def save_ceo_state(ceo: CEOState) -> Path:
    CEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": ceo.timestamp,
        "system_status": ceo.system_status,
        "overall_health": ceo.overall_health,
        "summary": ceo.summary,
        "capital": {
            "pnl": ceo.capital_pnl,
            "status": ceo.capital_status,
        },
        "positions": ceo.positions_count,
        "risk": {
            "level": ceo.risk_level,
            "biggest_exposure": ceo.biggest_exposure,
        },
        "experiments": [
            {"name": e.name, "progress": e.progress, "target": e.target, "status": e.status}
            for e in ceo.experiments
        ],
        "blockers": ceo.blockers,
        "next_allowed_step": ceo.next_allowed_step,
    }
    with CEO_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return CEO_PATH


def render_text(ceo: CEOState) -> str:
    status_icon = {"BLOCK": "🔴", "REVIEW": "🟡", "APPROVE": "🟢"}.get(ceo.system_status, "❓")
    health_icon = {"HEALTHY": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(ceo.overall_health, "⚪")
    risk_icon = {"LOW": "🟢", "HIGH": "🟠", "CRITICAL": "🔴"}.get(ceo.risk_level, "⚪")

    lines = [
        "=" * 64,
        "  TRADINGOS — CEO CONTROL PLANE v1",
        "=" * 64,
        f"  Timestamp:     {ceo.timestamp}",
        f"  System status: {status_icon} {ceo.system_status}",
        f"  Health:        {health_icon} {ceo.overall_health}",
        "",
        "  ── Summary ──",
        f"  {ceo.summary}",
        "",
        "  ── Capital ──",
        f"  PnL:           {ceo.capital_pnl}",
        f"  Status:        {ceo.capital_status}",
        f"  Positions:     {ceo.positions_count}",
        "",
        "  ── Risk ──",
        f"  Level:         {risk_icon} {ceo.risk_level}",
        f"  Biggest:       {ceo.biggest_exposure}",
        "",
        "  ── Experiments ──",
    ]
    for e in ceo.experiments:
        icon = "✅" if e.status == "READY" else "⏳"
        lines.append(f"    {icon} {e.name:20s} {e.progress:8s} (target: {e.target})")
    lines += [
        "",
        "  ── Blockers ──",
    ]
    if ceo.blockers:
        for b in ceo.blockers:
            lines.append(f"    ❌ {b}")
    else:
        lines.append("    None")
    lines += [
        "",
        "  ── Next Step ──",
        f"  → {ceo.next_allowed_step}",
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
