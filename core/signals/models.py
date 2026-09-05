"""
core/signals/models.py
Signal Engine models — standard data structures for trading signals.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

@dataclass(frozen=True)
class Signal:
    """A validated trading signal ready for risk assessment."""
    id: str
    timestamp: datetime
    symbol: str
    timeframe: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    strategy_name: str
    reason: str
    confidence_score: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
