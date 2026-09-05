"""
control_plane/risk_governor/models.py
Risk Governor models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import List, Optional


class GovernorVerdict(str):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    BLOCK = "BLOCK"


@dataclass
class RiskLimits:
    max_daily_loss_pct: float = 2.0
    max_position_pct: float = 5.0
    max_positions: int = 3
    max_drawdown_pct: float = 10.0
    kill_switch_loss_pct: float = 5.0


@dataclass
class GovernorDecision:
    verdict: str  # ALLOW | REDUCE | BLOCK
    confidence: float
    reasons: List[str] = field(default_factory=list)
    max_position_pct: Optional[float] = None
    max_positions: Optional[int] = None
    action_allowed: str = ""  # what action is permitted


@dataclass
class DailyState:
    date: str = ""
    trades_today: int = 0
    pnl_today: float = 0.0
    max_loss_today: float = 0.0
    consecutive_losses: int = 0
    kill_switch_active: bool = False
