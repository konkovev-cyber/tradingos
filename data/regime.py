"""
Market Regime Detector — классификация рынка по свечным данным.

v1.0: Определяет режим на основе ADX, ATR, EMA slope, Volume.
Не требует pandas — работает с numpy массивами.

Режимы:
  - BULL: EMA20 > EMA50, ADX > 20, цена выше EMA50
  - BEAR: EMA20 < EMA50, ADX > 20, цена ниже EMA50
  - SIDEWAYS: ADX < 20, цена между EMA20/EMA50
  - HIGH_VOL: ATR > 90-й перцентиль
  - LOW_VOL: ATR < 10-й перцентиль
  - EXPANSION: BB width растёт
  - COMPRESSION: BB width падает
"""

from __future__ import annotations
import numpy as np
from typing import Optional


class MarketRegimeDetector:
    """
    Классифицирует рыночный режим для FeatureVector.
    
    Usage:
        detector = MarketRegimeDetector()
        regime = detector.classify(fv)
    """
    
    def __init__(self):
        self._atr_history: list[float] = []
        self._bb_history: list[float] = []
    
    def classify(self, fv) -> str:
        """
        Определить режим рынка по FeatureVector.
        
        Returns:
            Один из: BULL, BEAR, SIDEWAYS, HIGH_VOL, LOW_VOL
        """
        # Trend direction
        if fv.ema_bullish and fv.price_above_ema50:
            trend = "BULL"
        elif not fv.ema_bullish and not fv.price_above_ema50:
            trend = "BEAR"
        else:
            trend = "SIDEWAYS"
        
        # ADX strength
        adx = fv.adx
        if adx >= 25:
            trend_strength = "STRONG"
        elif adx >= 20:
            trend_strength = "MODERATE"
        else:
            trend_strength = "WEAK"
        
        # Volatility
        atr_pct = (fv.atr / fv.close * 100) if fv.close > 0 else 0
        self._atr_history.append(atr_pct)
        if len(self._atr_history) > 200:
            self._atr_history.pop(0)
        
        if len(self._atr_history) >= 20:
            atr_percentile = self._percentile(self._atr_history, atr_pct)
            if atr_percentile > 0.9:
                vol = "HIGH"
            elif atr_percentile < 0.1:
                vol = "LOW"
            else:
                vol = "NORMAL"
        else:
            vol = "NORMAL"
        
        # BB compression/expansion
        bb_middle = getattr(fv, 'bb_middle', (fv.bb_upper + fv.bb_lower) / 2 if fv.bb_upper and fv.bb_lower else 0)
        bb_width = (fv.bb_upper - fv.bb_lower) / bb_middle if bb_middle > 0 else 0
        self._bb_history.append(bb_width)
        if len(self._bb_history) > 200:
            self._bb_history.pop(0)
        
        if len(self._bb_history) >= 20:
            bb_trend = self._bb_history[-1] - self._bb_history[-5] if len(self._bb_history) >= 5 else 0
            if bb_trend < -0.001:
                bb_state = "COMPRESSION"
            elif bb_trend > 0.001:
                bb_state = "EXPANSION"
            else:
                bb_state = "NEUTRAL"
        else:
            bb_state = "NEUTRAL"
        
        # Composite regime
        if vol == "HIGH":
            return "HIGH_VOL"
        if vol == "LOW":
            return "LOW_VOL"
        
        if trend_strength == "STRONG" and trend == "BULL":
            return "BULL"
        if trend_strength == "STRONG" and trend == "BEAR":
            return "BEAR"
        
        if trend_strength == "WEAK":
            return "SIDEWAYS"
        
        return trend  # BULL/BEAR with moderate strength
    
    def reset(self):
        """Reset history."""
        self._atr_history.clear()
        self._bb_history.clear()
    
    @staticmethod
    def _percentile(data: list[float], value: float) -> float:
        """Calculate percentile of value in data."""
        if not data:
            return 0.5
        count = sum(1 for x in data if x < value)
        return count / len(data)
