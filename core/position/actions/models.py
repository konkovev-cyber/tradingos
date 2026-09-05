"""
core/position/actions/models.py
Models for Position Action Shadow v0.1.

Two action types only (forbidden: CLOSE, REVERSE, ADD).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class ActionType(str, Enum):
    MOVE_SL_BREAKEVEN = "MOVE_SL_BE"
    TAKE_PARTIAL = "TAKE_PARTIAL"
    NO_ACTION = "NO_ACTION"


class PolicyVerdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


@dataclass(frozen=True)
class PIERecommendation:
    """
    Snapshot of a PIE recommendation at a given moment.
    PIE is the source of truth for recommendations.
    """
    rec_id: int
    timestamp: datetime
    symbol: str
    side: str
    recommendation: str
    price_at_recommendation: float
    pnl_at_recommendation: float
    mfe_at_recommendation: float
    health_at_recommendation: float
    reason: str

    @property
    def age_hours(self) -> float:
        now = datetime.now(timezone.utc)
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() / 3600.0


@dataclass(frozen=True)
class ActionPolicy:
    """
    Tunable policy for the Position Action Engine.
    Conservative v0.1 defaults: require fresh recommendation + sane PnL/MFE.
    """
    enabled: bool = True

    max_recommendation_age_hours: float = 4.0
    min_health: float = 60.0
    min_pnl_pct_to_act: float = 0.5

    partial_close_pct: float = 25.0
    breakeven_buffer_pct: float = 0.05


@dataclass
class ShadowAction:
    """
    What we WOULD do if we had execution rights.
    Pure observation artifact, never sent to exchange.
    """
    action: ActionType
    verdict: PolicyVerdict
    rec_id: int
    symbol: str
    side: str

    reasons: List[str]

    proposed_quantity_pct: Optional[float] = None
    proposed_new_stop: Optional[float] = None

    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc))
