"""
strategies/reality_adapter.py
Reality Discovery Adapter v1.
Calculates bias-based signals for controlled execution.
"""
import logging
from typing import Optional, NamedTuple
from .base import MarketSnapshot

logger = logging.getLogger("reality.adapter")

class RealitySignal(NamedTuple):
    symbol: str
    direction: str  # "BUY", "SELL"
    confidence: float
    direction_score: int
    reason: str
    source: str = "reality_adapter"

def get_reality_signal(snapshot: MarketSnapshot) -> Optional[RealitySignal]:
    """
    Senses market bias to find actionable opportunities.
    Does NOT use candle patterns.
    """
    # 1. Basic Bias Calculation
    # Simplified EMA20/Momentum since we only have current snapshot
    # In a real scenario, we'd fetch a window. Here we use the snapshot's basic data.
    
    price = snapshot.close
    # Mocking a simple bias since snapshot doesn't have EMA.
    # In implementation, we would ideally calculate EMA from data.
    # For v1, we use a proxy: price relative to mid-range of the snapshot window
    # or we assume the bridge provides EMA. Since it doesn't, we'll use close vs open.
    
    momentum = price - snapshot.open
    
    # We need a a bit more data for a real bias. 
    # If snapshot has previous_day_high/low, we can use those as a proxy for trend.
    trend_bias = 0
    if price > (snapshot.previous_day_high + snapshot.previous_day_low) / 2:
        trend_bias = 25 # Bullish
    elif price < (snapshot.previous_day_high + snapshot.previous_day_low) / 2:
        trend_bias = -25 # Bearish

    # Score components (0-25 each)
    # Prob bias (if ARC provided a probability, we'd use it. Here we use a default 0.40)
    prob_score = 25 if trend_bias != 0 else 0
    
    # Momentum score
    mom_score = 25 if (momentum > 0 and trend_bias > 0) or (momentum < 0 and trend_bias < 0) else 0
    
    # Trend score
    trend_score = 25 if trend_bias != 0 else 0
    
    # Pressure score (proxy: close vs high/low)
    pressure_score = 0
    if trend_bias > 0 and price > (snapshot.high + snapshot.low) / 2:
        pressure_score = 25
    elif trend_bias < 0 and price < (snapshot.high + snapshot.low) / 2:
        pressure_score = 25

    total_score = abs(trend_bias) + mom_score + prob_score + pressure_score
    
    # Final confidence is a baseline for Reality Mode
    confidence = 0.40 + (total_score / 400) # scaled 0.40 - 0.65
    
    direction = "BUY" if trend_bias > 0 else "SELL" if trend_bias < 0 else None
    
    if direction and total_score >= 75:
        return RealitySignal(
            symbol=snapshot.symbol,
            direction=direction,
            confidence=round(confidence, 2),
            direction_score=total_score,
            reason=f"Bias: {direction}, Score: {total_score}, Mom: {momentum:.2f}"
        )
    
    return None
