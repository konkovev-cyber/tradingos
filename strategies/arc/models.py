"""
strategies/arc/models.py
ARC-specific data models (Area, Range, Candle patterns).
"""
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class AreaLevel:
    name: str          # "PDH", "PDL", "SWING_HIGH", "SWING_LOW"
    price: float
    strength: float    # 0.0-1.0 confidence in level importance

@dataclass
class RangeZone:
    top: float
    bottom: float
    mid: float
    size_pips: float
    breakout_direction: Optional[str] = None  # "BUY", "SELL", None

@dataclass
class CandlePattern:
    name: str           # "HAMMER", "INV_HAMMER", "REJECTION_WICK", "BREAKOUT"
    direction: str      # "BUY", "SELL"
    price: float
    confidence: float   # 0.0-1.0

@dataclass
class ARCSetup:
    symbol: str
    timeframe: str
    direction: str
    area: AreaLevel
    range_zone: RangeZone
    candle: CandlePattern
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: float
    reasons: List[str] = field(default_factory=list)
