"""
Signal Types — shared between SignalGenerator and SignalScoringEngine.

v8.0: Breaks circular import by moving typed context objects
to a shared module that neither depends on the other.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class H1Filter:
    """1H timeframe context — derived from FeatureVector."""
    ema20: float
    ema50: float
    rsi: float
    adx: float
    volume_ratio: float
    trend_up: bool
    price: float
    momentum: float
    trend_score: float = 0.0
    alignment: str = "NEUTRAL"
    aligned: bool = False


@dataclass
class H4Filter:
    """4H timeframe context — derived from HTFCollector."""
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    trend_up: Optional[bool] = None
    score: float = 0.0
    aligned: bool = False


@dataclass
class Setup:
    """L2: Pullback + RSI + Volume context."""
    direction: str  # BUY or SELL
    ema_distance_pct: float  # distance to nearest EMA
    rsi: float
    volume_ratio: float
    is_pullback: bool
    score: float = 0.0


@dataclass
class Trigger:
    """L3: Volume spike + pattern + momentum."""
    volume_spike: bool
    volume_ratio: float
    pattern: Optional[str]
    momentum: float
    momentum_ok: bool
    rsi_exit: bool
    score: float = 0.0
