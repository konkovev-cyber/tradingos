"""
core/state_reconciliation/models.py
State Reconciliation models — immutable dataclasses for position state comparison.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class ReconciliationStatus(str, Enum):
    SYNCED = "SYNCED"
    DRIFT = "DRIFT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"


class PositionChangeType(str, Enum):
    SIDE_REVERSED = "SIDE_REVERSED"
    QTY_CHANGED = "QTY_CHANGED"
    ENTRY_CHANGED = "ENTRY_CHANGED"
    SL_CHANGED = "SL_CHANGED"
    TP_CHANGED = "TP_CHANGED"
    POSITION_CLOSED = "POSITION_CLOSED"
    POSITION_OPENED = "POSITION_OPENED"
    LEVERAGE_CHANGED = "LEVERAGE_CHANGED"


@dataclass(frozen=True)
class PositionState:
    """Immutable snapshot of position state from any source."""
    symbol: str
    side: str
    qty: float
    entry_price: float
    mark_price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: int = 1
    unrealized_pnl: float = 0.0
    source: str = ""
    position_id: str = ""
    timestamp: str = ""

    @staticmethod
    def from_bingx(pos: dict) -> "PositionState":
        return PositionState(
            symbol=pos.get("symbol", ""),
            side=pos.get("side", ""),
            qty=float(pos.get("qty", 0)),
            entry_price=float(pos.get("entry_price", 0)),
            mark_price=float(pos.get("mark_price", 0)),
            stop_loss=pos.get("stop_loss"),
            take_profit=pos.get("take_profit"),
            leverage=int(pos.get("leverage", 1)),
            unrealized_pnl=float(pos.get("unrealized_pnl", 0)),
            source="BINGX_API",
            position_id=pos.get("position_id", ""),
            timestamp=pos.get("timestamp", ""),
        )


@dataclass
class FieldChange:
    field_name: str
    expected: object
    actual: object
    change_type: PositionChangeType


@dataclass
class ReconciliationResult:
    symbol: str
    status: ReconciliationStatus
    expected: Optional[PositionState]
    actual: Optional[PositionState]
    changes: List[FieldChange] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "expected_side": self.expected.side if self.expected else None,
            "actual_side": self.actual.side if self.actual else None,
            "changes": [
                {"field": c.field_name, "expected": c.expected, "actual": c.actual, "type": c.change_type.value}
                for c in self.changes
            ],
            "timestamp": self.timestamp,
        }
