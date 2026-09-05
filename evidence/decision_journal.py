"""
evidence/decision_journal.py
Decision Journal — records every TradingOS decision with full context.
TRADE, NO_TRADE, NO_SIGNAL — all decisions logged.
"""
import json, logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("evidence.journal")

JOURNAL_PATH = Path("/root/tradingos/evidence/decision_journal.jsonl")
CONTEXT_PATH = Path("/root/tradingos/evidence/context_snapshot.jsonl")

def record_decision(
    decision_id: str,        # Required: Unique ID for this decision
    decision: str,           # NO_SIGNAL / APPROVED / REJECTED / INVALID
    strategy: str,
    symbol: str = "XAUUSD",
    direction: str = "",
    entry: float = 0,
    sl: float = 0,
    tp: float = 0,
    confidence: float = 0,
    rr: float = 0,
    reason: str = "",
    session: str = "",
    atr: float = 0,
    spread: float = 0,
    guardian_state: str = "",
    result_r: float = 0,
    result_pnl: float = 0,
    mae: float = 0,
    mfe: float = 0,
    exit_reason: str = "",
    paper: bool = True,
    parent_decision_id: Optional[str] = None,
    decision_version: int = 1,
    decision_role: str = "PRIMARY",
    parameter_profile: str = "DEFAULT",
    experiment_id: Optional[str] = None,
):
    entry = {
        "schema_version": 1,
        "decision_version": decision_version,
        "decision_id": decision_id,
        "decision_role": decision_role,
        "parameter_profile": parameter_profile,
        "experiment_id": experiment_id,
        "parent_decision_id": parent_decision_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "strategy": strategy,
        "symbol": symbol,
        "direction": direction,
        "entry": round(entry, 2) if entry else 0,
        "sl": round(sl, 2) if sl else 0,
        "tp": round(tp, 2) if tp else 0,
        "confidence": round(confidence, 2),
        "rr": round(rr, 2),
        "reason": reason,
        "context": {
            "session": session,
            "atr": round(atr, 2),
            "spread": round(spread, 2),
            "guardian_state": guardian_state,
        },
        "result": {
            "r": round(result_r, 2),
            "pnl": round(result_pnl, 4),
            "mae": round(mae, 2),
            "mfe": round(mfe, 2),
            "exit_reason": exit_reason,
        },
        "mode": "PAPER" if paper else "LIVE",
    }

    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.debug(f"[{decision}] {strategy} {direction} @ {entry.get('entry', 0):.2f} | {reason}")


def record_no_signal(
    decision_id: str,
    strategy: str,
    symbol: str = "XAUUSD",
    reason: str = "",
    session: str = "",
    atr: float = 0,
    spread: float = 0,
    parent_decision_id: Optional[str] = None,
    decision_version: int = 1,
    decision_role: str = "PRIMARY",
    parameter_profile: str = "DEFAULT",
    experiment_id: Optional[str] = None,
):
    record_decision(
        decision_id=decision_id,
        decision="NO_SIGNAL",
        strategy=strategy,
        symbol=symbol,
        reason=reason or "No ARC pattern detected",
        session=session,
        atr=atr,
        spread=spread,
        parent_decision_id=parent_decision_id,
        decision_version=decision_version,
        decision_role=decision_role,
        parameter_profile=parameter_profile,
        experiment_id=experiment_id,
    )


def get_journal_path() -> Path:
    return JOURNAL_PATH


def count_decisions() -> dict:
    """Quick stats on journal contents."""
    counts = {}
    total = 0
    if not JOURNAL_PATH.exists():
        return {"total": 0, "decisions": {}}
    with JOURNAL_PATH.open() as f:
        for line in f:
            if line.strip():
                total += 1
                entry = json.loads(line)
                d = entry.get("decision", "UNKNOWN")
                counts[d] = counts.get(d, 0) + 1
    return {"total": total, "decisions": counts}
