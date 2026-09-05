"""
TradingOS Signal Types — extracted from uBot_bingx core/decision_engine.py.

Action + Decision — единственные типы, нужные decision_engine_v2.py.
Весь остальной decision_engine.py (execution layer) НЕ копируется.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


class Direction(Enum):
    """Direction for signal aggregation (decision_engine_v2 compatibility)."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Decision:
    """Финальное решение Decision Engine.

    Совместимо с decision_engine_v2.py из uBot_bingx.
    Не имеет execution-зависимостей.
    """
    symbol: str
    action: Action = Action.NO_TRADE
    confidence: float = 0.0        # 0.0 – 1.0
    size: float = 0.0              # qty в базовой валюте
    stop_loss: float = 0.0
    take_profit: float = 0.0
    entry_price: float = 0.0

    # Audit chain
    reason: List[str] = field(default_factory=list)
    reject_stage: Optional[str] = None

    # Scores
    signal_score: float = 0.0
    regime_score: float = 0.0
    final_score: float = 0.0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_trade(self) -> bool:
        return self.action != Action.NO_TRADE

    @property
    def risk_reward(self) -> float:
        if self.entry_price <= 0 or self.stop_loss <= 0:
            return 0.0
        sl_dist = abs(self.entry_price - self.stop_loss)
        tp_dist = abs(self.entry_price - self.take_profit)
        return tp_dist / sl_dist if sl_dist > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "action": self.action.value,
            "confidence": round(self.confidence, 3),
            "size": round(self.size, 6),
            "entry_price": round(self.entry_price, 6),
            "stop_loss": round(self.stop_loss, 6),
            "take_profit": round(self.take_profit, 6),
            "reason": self.reason,
            "reject_stage": self.reject_stage,
            "signal_score": round(self.signal_score, 1),
            "regime_score": round(self.regime_score, 1),
            "final_score": round(self.final_score, 1),
            "timestamp": self.timestamp,
        }
