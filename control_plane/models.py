"""
control_plane/models.py
Unified State Models — pure dataclasses, no logic.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class ResearchState:
    status: str = "UNKNOWN"
    active_experiments: int = 0
    evidence_score: float = 0.0
    experiments: List[str] = field(default_factory=list)


@dataclass
class PositionState:
    open_count: int = 0
    unrealized_pnl: float = 0.0
    in_profit: int = 0
    in_loss: int = 0
    guard_mode: str = "UNKNOWN"
    guard_valid_signals: int = 0
    action_shadow_unique: int = 0
    pending_actions: int = 0
    risk_concentration: Dict[str, float] = field(default_factory=dict)


@dataclass
class CapitalState:
    total_unrealized: float = 0.0
    total_realized: float = 0.0
    closed_trades: int = 0
    open_positions: int = 0
    exposure_pct: float = 0.0
    biggest_risk_symbol: str = ""
    biggest_risk_pct: float = 0.0
    status: str = "UNKNOWN"


@dataclass
class ExecutionState:
    ubot_status: str = "UNKNOWN"
    pie_status: str = "UNKNOWN"
    guard_status: str = "UNKNOWN"
    live_permission: str = "NONE"
    services_running: int = 0
    services_total: int = 0


@dataclass
class EvidenceState:
    pg_total: int = 0
    pg_valid_move_sl: int = 0
    pg_skipped: int = 0
    action_total: int = 0
    action_approved_raw: int = 0
    action_unique: int = 0
    action_completed: int = 0
    dq_score: float = 0.0


@dataclass
class TradingOSState:
    timestamp: str = ""
    system_status: str = "UNKNOWN"
    research: ResearchState = field(default_factory=ResearchState)
    positions: PositionState = field(default_factory=PositionState)
    capital: CapitalState = field(default_factory=CapitalState)
    execution: ExecutionState = field(default_factory=ExecutionState)
    evidence: EvidenceState = field(default_factory=EvidenceState)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
