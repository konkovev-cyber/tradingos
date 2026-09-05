"""
Signal Scoring Engine v8.0 — Чистая логика с FeatureVector.

v8.0 BREAKING CHANGE:
-calculate_with_vectors() — принимает typed objects (H1Filter, H4Filter, Setup, Trigger, FeatureVector)
- calculate() — legacy dict interface (deprecated)
- ВСЕ весы и пороги неизменны относительно v7.3
- Regime bias: +/- 0.03-0.06 (мягкий, не killer)
- Integrity: risk ONLY (size/EV), НЕ probability
"""
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.strategies.regime_engine import RegimeProbabilityEngine
from tradingos.signals.signal_types import H1Filter, H4Filter, Setup, Trigger

log = logging.getLogger("SignalScoring")


@dataclass
class SignalScore:
    """Результат оценки сигнала."""
    total_score: float  # 0-100
    trend_score: float  # 0-30
    setup_score: float  # 0-30
    trigger_score: float  # 0-40
    
    base_probability: float  # Чистый edge (БЕЗ regime/integrity)
    final_probability: float  # После regime bias
    threshold: float  # Минимальный порог для сигнала
    quality: str  # "EXCELLENT", "GOOD", "MEDIUM", "WEAK"
    
    direction: str  # "BUY" или "SELL"
    reason: str  # Пояснение
    
    # v7.3: Clean separation
    regime: str
    regime_bias: float  # +/- 0.03-0.06
    integrity_multiplier: float  # Risk multiplier
    risk_multiplier: float
    position_size_fraction: float
    
    details: Dict[str, Any]


class SignalScoringEngine:
    """Scoring engine v8.0 — принимает typed objects из SignalGenerator."""
    
    def __init__(self):
        self.regime_engine = RegimeProbabilityEngine()
        
        self.thresholds = {
            "EXCELLENT": 80,
            "GOOD": 65,
            "MEDIUM": 50,
            "WEAK": 0,
        }
        
        self.weights = {
            "trend": 30,
            "setup": 30,
            "trigger": 40,
        }
    
    def calculate_with_vectors(
        self,
        symbol: str,
        h1_filter: "H1Filter",
        h4_filter: "H4Filter",
        setup: "Setup",
        trigger: "Trigger",
        fv: FeatureVector,
        direction: str,
        bar_idx: int = 0,
    ) -> Optional[SignalScore]:
        """
        Calculate signal score from typed objects.
        
        v8.0: Pure scoring — NO indicator calculations.
        
        Args:
            h1_filter: 1H context from FeatureVector
            h4_filter: 4H context from HTFCollector
            setup: L2 setup context
            trigger: L3 trigger context
            fv: Full feature vector
            direction: BUY or SELL
            
        Returns:
            SignalScore or None (rejected)
        """
        # ==================== LAYER 1: TREND (30 points) ====================
        trend_score, trend_details = self._score_trend(h1_filter, h4_filter)
        
        # Critical: if HTF data exists, require minimum 10 points
        if h4_filter.trend_up is not None and trend_score < 10:
            log.debug(f"{symbol}: Trend score too low ({trend_score:.1f}/30), rejecting")
            return None
        
        # ==================== LAYER 2: SETUP (30 points) ====================
        setup_score, setup_details = self._score_setup(setup, direction, fv)
        
        # ==================== LAYER 3: TRIGGER (40 points) ====================
        trigger_score, trigger_details = self._score_trigger(trigger, fv)
        
        # ==================== TOTAL ====================
        total_score = trend_score + setup_score + trigger_score
        
        # Quality
        if total_score >= self.thresholds["EXCELLENT"]:
            quality = "EXCELLENT"
        elif total_score >= self.thresholds["GOOD"]:
            quality = "GOOD"
        elif total_score >= self.thresholds["MEDIUM"]:
            quality = "MEDIUM"
        else:
            quality = "WEAK"
        
        # ==================== PROBABILITY ====================
        base_probability = total_score / 100.0
        
        score_breakdown = {
            "trend_score": trend_score,
            "setup_score": setup_score,
            "trigger_score": trigger_score,
        }
        
        regime_result = self.regime_engine.evaluate(
            features={
                "rsi": fv.rsi,
                "volume_ratio": fv.volume_ratio,
                "momentum": (fv.close - fv.open) / fv.open if fv.open > 0 else 0,
                "adx": fv.adx,
                "volatility": fv.atr / fv.close if fv.close > 0 else 0,
            },
            score_breakdown=score_breakdown,
            integrity_score=fv.integrity_score,
            equity=1000,
        )
        
        final_probability = max(0.0, min(1.0, regime_result["final_probability"]))
        
        reason = self._build_reason(trend_details, setup_details, trigger_details, quality, regime_result["regime"])
        
        # Threshold configurable via env: SG_CONF_THRESHOLD (default 0.70)
        # УЖЕСТОЧЕНИЕ 2026-09-01: было 0.55 — слишком много ложных сигналов
        import os as _os
        _thr = float(_os.environ.get("SG_CONF_THRESHOLD", "0.70"))
        
        return SignalScore(
            total_score=total_score,
            trend_score=trend_score,
            setup_score=setup_score,
            trigger_score=trigger_score,
            base_probability=base_probability,
            final_probability=final_probability,
            threshold=_thr,  # Configurable (default 0.55)
            quality=quality,
            direction=direction,
            reason=reason,
            regime=regime_result["regime"],
            regime_bias=regime_result["regime_bias"],
            integrity_multiplier=regime_result["integrity_multiplier"],
            risk_multiplier=regime_result["integrity_multiplier"],
            position_size_fraction=regime_result["position_size_fraction"],
            details={
                "trend": trend_details,
                "setup": setup_details,
                "trigger": trigger_details,
                "regime": regime_result,
            }
        )
    
    # ==================== SCORING METHODS ====================
    
    def _score_trend(self, h1: H1Filter, h4: H4Filter) -> tuple:
        """
        Score trend alignment (30 points).
        - 1H + 4H alignment: +15
        - ADX strength: +15
        """
        score = 0
        details = {}
        
        # 1H data always available
        h1_trend_up = h1.trend_up
        h1_adx = h1.adx
        details["1H"] = {"trend_up": h1_trend_up, "adx": h1_adx}
        
        # 4H data if available
        if h4.trend_up is not None:
            h4_trend_up = h4.trend_up
            h4_adx = h1.adx  # We don't have H4 ADX in v8.0, use 1H proxy
            details["4H"] = {"trend_up": h4_trend_up}
            
            # Alignment
            if h1_trend_up == h4_trend_up:
                score += 15
                details["trend_alignment"] = "YES"
            else:
                score += 5
                details["trend_alignment"] = "PARTIAL"
            
            # ADX (using 1H as proxy for both)
            adx_score = 0
            if h1_adx >= 20:
                adx_score += 7.5
            if h4_adx >= 20:
                adx_score += 7.5
            score += adx_score
            details["adx_score"] = adx_score
        else:
            # No HTF data — neutral score
            score += 15
            details["trend_alignment"] = "NO_HTF"
        
        return min(score, 30), details
    
    def _score_setup(self, setup: Setup, direction: str, fv: FeatureVector) -> tuple:
        """
        Score setup (30 points).
        - Pullback depth: +10
        - RSI zone: +10
        - Volume contraction: +10
        """
        score = 0
        details = {}
        
        # Pullback depth
        dist = setup.ema_distance_pct
        if dist <= 0.5:
            score += 10
            details["pullback"] = "EXCELLENT"
        elif dist <= 1.0:
            score += 7
            details["pullback"] = "GOOD"
        elif dist <= 2.0:
            score += 4
            details["pullback"] = "MEDIUM"
        else:
            score += 0
            details["pullback"] = "WEAK"
        
        details["ema_distance_pct"] = dist
        
        # RSI zone
        rsi = fv.rsi
        if direction == "BUY":
            if rsi < 30:
                score += 10
                details["rsi_zone"] = "OVERSOLD"
            elif rsi < 40:
                score += 7
                details["rsi_zone"] = "GOOD"
            elif rsi < 50:
                score += 4
                details["rsi_zone"] = "NEUTRAL"
            else:
                score += 0
                details["rsi_zone"] = "WEAK"
        else:  # SELL
            if rsi > 70:
                score += 10
                details["rsi_zone"] = "OVERBOUGHT"
            elif rsi > 60:
                score += 7
                details["rsi_zone"] = "GOOD"
            elif rsi > 50:
                score += 4
                details["rsi_zone"] = "NEUTRAL"
            else:
                score += 0
                details["rsi_zone"] = "WEAK"
        
        details["rsi"] = rsi
        
        # Volume contraction
        vr = setup.volume_ratio
        if vr < 0.7:
            score += 10
            details["volume"] = "CONTRACTION"
        elif vr < 1.0:
            score += 6
            details["volume"] = "LOW"
        elif vr < 1.3:
            score += 3
            details["volume"] = "NEUTRAL"
        else:
            score += 0
            details["volume"] = "HIGH"
        
        details["volume_ratio"] = vr
        
        return min(score, 30), details
    
    def _score_trigger(self, trigger: Trigger, fv: FeatureVector) -> tuple:
        """
        Score trigger (40 points).
        - Volume spike: 0-15
        - Pattern: 0-15
        - Momentum: 0-10
        """
        score = 0
        details = {}
        
        # Volume
        vs = trigger.volume_spike
        vr = trigger.volume_ratio
        if vs and vr >= 2.0:
            score += 15
            details["volume"] = "EXCELLENT"
        elif vs and vr >= 1.5:
            score += 12
            details["volume"] = "GOOD"
        elif vs:
            score += 10
            details["volume"] = "ACCEPTABLE"
        elif vr >= 1.1:
            score += 5
            details["volume"] = "NEUTRAL"
        else:
            score += 0
            details["volume"] = "LOW"
        
        details["volume_ratio"] = vr
        details["volume_spike"] = vs
        
        # Pattern
        pattern = trigger.pattern
        if pattern:
            strong = ["pin_bar_bullish", "hammer", "pin_bar_bearish", "shooting_star"]
            if pattern in strong:
                score += 15
                details["pattern"] = f"STRONG_{pattern.upper()}"
            else:
                score += 10
                details["pattern"] = pattern.upper()
        else:
            score += 0
            details["pattern"] = "NONE"
        
        # Momentum
        mom = abs(trigger.momentum) * 100
        if mom >= 0.5:
            score += 10
            details["momentum"] = "STRONG"
        elif mom >= 0.3:
            score += 7
            details["momentum"] = "GOOD"
        elif mom >= 0.1:
            score += 4
            details["momentum"] = "WEAK"
        else:
            score += 0
            details["momentum"] = "NONE"
        
        details["momentum_pct"] = mom
        
        return min(score, 40), details
    
    def _build_reason(self, trend_d, setup_d, trigger_d, quality, regime):
        """Build explanation string."""
        parts = []
        regime_names = {
            "trending": "TRENDING", "ranging": "RANGING",
            "high_volatility": "HIGH_VOL", "low_liquidity": "LOW_LIQ",
            "normal": "NORMAL",
        }
        parts.append(f"[{regime_names.get(regime, 'NORMAL')}]")
        
        if trend_d.get("trend_alignment") == "YES":
            parts.append("Trend aligned")
        elif trend_d.get("trend_alignment") == "PARTIAL":
            parts.append("Trend partial")
        
        if setup_d.get("pullback"):
            parts.append(f"Pullback {setup_d['pullback']}")
        
        if trigger_d.get("volume"):
            parts.append(f"Vol {trigger_d['volume']}")
        
        if trigger_d.get("pattern"):
            parts.append(f"Pattern {trigger_d['pattern']}")
        
        return f"{quality}: {' | '.join(parts)}"
    
    # Legacy dict-based interface (deprecated)
    def calculate(self, symbol, htf_filter, setup, trigger, features, direction, integrity_score=100):
        """Legacy interface — converts dict to typed objects internally."""
        # This is a compatibility shim, not the main path
        raise NotImplementedError("Use calculate_with_vectors() instead")
