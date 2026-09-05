"""
control_plane/risk_governor/classifier.py
Portfolio Classification — distinguishes legacy vs experiment positions.

Read-only. No execution. Labels positions by source/owner/status.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import RiskLimits, GovernorDecision, DailyState
from .evaluator import evaluate as evaluate_risk

CLASSIFICATION_PATH = Path("/root/tradingos/control_plane/risk_governor/portfolio_classification.json")


def classify_positions(positions: List[Dict], legacy_symbols: Optional[set] = None) -> List[Dict]:
    """
    Classify each position as LEGACY or EXPERIMENT.
    Legacy = opened before TradingOS (MT5 or manual).
    Experiment = opened by TradingOS control.
    """
    if legacy_symbols is None:
        legacy_symbols = set()  # empty = all are experiment

    classified = []
    for p in positions:
        sym = p.get("symbol", "")
        is_legacy = sym in legacy_symbols
        classified.append({
            "symbol": sym,
            "side": p.get("side", ""),
            "entry_price": p.get("entry_price", 0),
            "qty": p.get("qty", 0),
            "unrealized_pnl": p.get("unrealized_pnl", 0),
            "source": "MT5_LEGACY" if is_legacy else "UNKNOWN",
            "owner": "MT5_LEGACY" if is_legacy else "EXTERNAL",
            "status": "OBSERVE_ONLY" if is_legacy else "ACTIVE",
        })
    return classified


def save_classification(classified: List[Dict]) -> Path:
    CLASSIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(classified),
        "legacy": sum(1 for c in classified if c["source"] == "MT5_LEGACY"),
        "experiment": sum(1 for c in classified if c["source"] != "MT5_LEGACY"),
        "positions": classified,
    }
    with CLASSIFICATION_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return CLASSIFICATION_PATH


def evaluate_with_classification(
    positions: List[Dict],
    legacy_symbols: set,
    limits: RiskLimits,
) -> Dict:
    """
    Risk Governor with portfolio awareness.
    Legacy positions are excluded from new risk budget.
    """
    classified = classify_positions(positions, legacy_symbols)
    experiment = [c for c in classified if c["status"] == "ACTIVE"]
    legacy = [c for c in classified if c["status"] == "OBSERVE_ONLY"]

    # Evaluate risk on experiment positions only
    state = DailyState()
    experiment_count = len(experiment)
    experiment_notional = sum(c["entry_price"] * c["qty"] for c in experiment)

    decision = evaluate_risk(
        limits=limits,
        daily=state,
        proposed_position_pct=0.0,
        current_positions=experiment_count,
    )

    return {
        "classified": classified,
        "legacy_count": len(legacy),
        "experiment_count": experiment_count,
        "legacy_notional": sum(c["entry_price"] * c["qty"] for c in legacy),
        "experiment_notional": experiment_notional,
        "risk_decision": decision,
    }


def render_classification(classified: List[Dict]) -> str:
    lines = []
    for c in classified:
        icon = "📁" if c["source"] == "MT5_LEGACY" else "🆕"
        pnl = c["unrealized_pnl"]
        lines.append(
            f"  {icon} {c['symbol']:12s} {c['side']:5s} "
            f"source={c['source']:12s} status={c['status']:12s} "
            f"pnl={pnl:+.4f}"
        )
    return "\n".join(lines)
