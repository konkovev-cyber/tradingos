"""
strategies/base.py
Base signal generator interface — template for all strategy modules.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: str  # BUY / SELL
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float = 0.0
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    strategy: str = ""

@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    previous_day_open: float = 0.0
    previous_day_high: float = 0.0
    previous_day_low: float = 0.0
    previous_day_close: float = 0.0
    swing_high: float = 0.0
    swing_low: float = 0.0

class StrategyBase(ABC):
    @abstractmethod
    def analyze(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        ...

    @abstractmethod
    def name(self) -> str:
        ...
