"""
core/rts_governor/models.py
RTS Decision Model — the unified decision layer for position management.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class PositionLifecycleState(str, Enum):
    NEW = "NEW"
    OPEN = "OPEN"
    PROTECTED = "PROTECTED"
    PROFIT_ZONE = "PROFIT_ZONE"
    WARNING = "WARNING"
    DEFENSIVE = "DEFENSIVE"
    EMERGENCY = "EMERGENCY"
    CLOSED = "CLOSED"


class SystemRiskState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    DEFENSIVE = "DEFENSIVE"
    EMERGENCY = "EMERGENCY"


class AllowedAction(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    MOVE_SL = "MOVE_SL"
    RESTORE_PROTECTION = "RESTORE_PROTECTION"
    FREEZE = "FREEZE"
    ALERT = "ALERT"
    CLOSE = "CLOSE"


@dataclass
class ProtectionStatus:
    has_sl: bool
    has_tp: bool
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    fully_protected: bool = False


@dataclass
class RTSDecision:
    """The result of evaluating one position through the RTS Governor."""
    position_symbol: str
    position_side: str
    lifecycle_state: PositionLifecycleState
    risk_state: SystemRiskState
    protection: ProtectionStatus
    allowed_actions: List[AllowedAction] = field(default_factory=list)
    blocked_actions: List[AllowedAction] = field(default_factory=list)
    reason: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.position_symbol,
            "side": self.position_side,
            "lifecycle_state": self.lifecycle_state.value,
            "risk_state": self.risk_state.value,
            "protection": {
                "has_sl": self.protection.has_sl,
                "has_tp": self.protection.has_tp,
                "sl_price": self.protection.sl_price,
                "tp_price": self.protection.tp_price,
                "fully_protected": self.protection.fully_protected,
            },
            "allowed": [a.value for a in self.allowed_actions],
            "blocked": [b.value for b in self.blocked_actions],
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass
class DecisionLogEntry:
    """A single step in the position lifecycle with full context."""
    timestamp: str
    position: str
    action: str
    reason: str
    previous_state: str
    new_state: str
    risk_change: str = ""
    pnl: float = 0.0
    notes: str = ""

    def to_line(self) -> str:
        return (f"[{self.timestamp}] {self.position}: {self.action} "
                f"({self.previous_state} → {self.new_state}) | "
                f"reason={self.reason} risk={self.risk_change}")
