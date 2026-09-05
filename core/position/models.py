"""
core/position/models.py
Data models for Position Guard v1.
Strict immutability (frozen=True) is enforced to prevent accidental
side effects on input data within the Decision Engine.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Action(str, Enum):
    HOLD = "HOLD"
    MOVE_SL = "MOVE_SL"
    IGNORE = "IGNORE"
    SKIPPED = "SKIPPED"


class PositionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    CLOSED_CONFIRMED = "CLOSED_CONFIRMED"


@dataclass(frozen=True)
class PositionSnapshot:
    """
    Immutable snapshot of a position's state at a given moment.
    PIE is the source of truth for MFE and Health Score.
    """
    timestamp: datetime
    exchange: str
    symbol: str
    side: Side

    entry_price: float
    mark_price: float

    pnl_pct: float
    mfe_pct: float
    health_score: float

    qty: float
    stop_price: Optional[float] = None

    position_id: str = ""
    status: PositionStatus = PositionStatus.UNKNOWN

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class GuardConfig:
    """
    Tunable parameters for the Guard Decision Engine.
    Allows running different versions (conservative, aggressive) without code changes.
    """
    enabled: bool = True

    pnl_trigger: float = 1.0
    mfe_trigger: float = 1.5
    health_min: float = 80.0

    protect_profit_pct: float = 0.5


@dataclass
class GuardDecision:
    """
    The output of the pure Decision Engine.
    Contains full context for Evidence Ledger / Shadow Analysis.
    """
    action: Action
    confidence: float

    reasons: List[str] = field(default_factory=list)

    current_stop: Optional[float] = None
    proposed_stop: Optional[float] = None

    timestamp: datetime = field(default_factory=PositionSnapshot.now_utc)
