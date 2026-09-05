"""
LiquiditySweep Strategy — catches liquidity sweeps (stop hunts).

Architecture:
  CandleClosed events → LiquiditySweepStrategy → StrategySignal

Entry logic:
  - Price breaks above recent high + threshold, then closes back below → BUY (long squeeze)
  - Price breaks below recent low - threshold, then closes back above → SELL (short squeeze)

Based on: trading_brain_v4/strategies/liquidity_sweep.py
Adapted for: TradingOS Data Lake pipeline (self-contained, no external deps)
"""

import hashlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("LiquiditySweep")

STRATEGY_VERSION = "LiquiditySweep-v1.0.0"


@dataclass
class StrategySignal:
    """Standard signal emitted by the strategy."""
    symbol: str
    direction: str  # "BUY" | "SELL"
    confidence: float
    timestamp: float
    metadata: dict = field(default_factory=dict)


def _params_hash(lookback: int, threshold: float) -> str:
    raw = f"lookback={lookback}|threshold={threshold}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class LiquiditySweepStrategy:
    """Catches liquidity sweeps (stop hunts) in crypto markets.

    Detects when price briefly breaks a key level then reverses —
    indicating stop runs that often precede counter-moves.
    """

    def __init__(self, lookback: int = 50, sweep_threshold: float = 0.0015):
        self.lookback = lookback
        self.sweep_threshold = sweep_threshold
        self._highs = deque(maxlen=lookback)
        self._lows = deque(maxlen=lookback)
        self._sweeps_detected = 0
        self._stats = {
            "total_analyzed": 0,
            "accepted": 0,
            "rejected": 0,
        }

    def get_name(self) -> str:
        return "liquidity_sweep"

    def analyze(self, market_data: dict) -> Optional[StrategySignal]:
        self._stats["total_analyzed"] += 1
        symbol = market_data.get("symbol", "")
        high = market_data.get("high", 0)
        low = market_data.get("low", 0)
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        regime = market_data.get("regime", "UNKNOWN")
        atr = market_data.get("atr", 0)

        if high == 0 or low == 0:
            self._stats["rejected"] += 1
            return None

        if len(self._highs) < 10:
            self._highs.append(high)
            self._lows.append(low)
            self._stats["rejected"] += 1
            return None

        recent_high = max(self._highs)
        recent_low = min(self._lows)

        self._highs.append(high)
        self._lows.append(low)

        # Entry quality metrics
        candle_range = high - low
        range_atr_ratio = round(candle_range / atr, 2) if atr > 0 else 0

        # Sweep UP: price breaks above recent high, then closes back below
        up_breakout = recent_high * (1 + self.sweep_threshold)
        if high > up_breakout and close < recent_high:
            self._sweeps_detected += 1
            self._stats["accepted"] += 1
            strength = (high - recent_high) / recent_high
            distance_from_sweep = round(close - recent_high, 2)
            return StrategySignal(
                symbol=symbol,
                direction="BUY",
                confidence=min(1.0, volume / 1000),
                timestamp=time.time(),
                metadata={
                    "sweep_type": "long_squeeze",
                    "sweep_level": recent_high,
                    "strength": round(strength, 6),
                    "current_price": close,
                    "candle_range": round(candle_range, 2),
                    "range_atr_ratio": range_atr_ratio,
                    "distance_from_sweep": distance_from_sweep,
                    "regime": regime,
                    "strategy_version": STRATEGY_VERSION,
                    "params_hash": _params_hash(self.lookback, self.sweep_threshold),
                },
            )

        # Sweep DOWN: price breaks below recent low, then closes back above
        down_breakout = recent_low * (1 - self.sweep_threshold)
        if low < down_breakout and close > recent_low:
            self._sweeps_detected += 1
            self._stats["accepted"] += 1
            strength = (recent_low - low) / recent_low
            distance_from_sweep = round(recent_low - close, 2)
            return StrategySignal(
                symbol=symbol,
                direction="SELL",
                confidence=min(1.0, volume / 1000),
                timestamp=time.time(),
                metadata={
                    "sweep_type": "short_squeeze",
                    "sweep_level": recent_low,
                    "strength": round(strength, 6),
                    "current_price": close,
                    "candle_range": round(candle_range, 2),
                    "range_atr_ratio": range_atr_ratio,
                    "distance_from_sweep": distance_from_sweep,
                    "regime": regime,
                    "strategy_version": STRATEGY_VERSION,
                    "params_hash": _params_hash(self.lookback, self.sweep_threshold),
                },
            )

        self._stats["rejected"] += 1
        return None

    def get_stats(self) -> dict:
        total = self._stats["total_analyzed"]
        return {
            "strategy_version": STRATEGY_VERSION,
            "params_hash": _params_hash(self.lookback, self.sweep_threshold),
            "lookback": self.lookback,
            "sweep_threshold": self.sweep_threshold,
            "sweeps_detected": self._sweeps_detected,
            "total_analyzed": total,
            "accepted": self._stats["accepted"],
            "rejected": self._stats["rejected"],
            "accept_rate": round(self._stats["accepted"] / total, 4) if total else 0,
        }

    def reset(self):
        self._highs.clear()
        self._lows.clear()
        self._sweeps_detected = 0
        self._stats = {"total_analyzed": 0, "accepted": 0, "rejected": 0}
