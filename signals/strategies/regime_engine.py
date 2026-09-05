"""
Regime-aware Probability Engine v7.3 — Clean Separation Fix.

КЛЮЧЕВАЯ ОШИБКА v7.2:
- Multiplicative over-conditioning: probability × regime × integrity
- Падение 0.69 → 0.44 — слишком агрессивное
- "Двойные штрафы" за одни и те же риски

ПРАВИЛЬНАЯ МОДЕЛЬ v7.3:
Каждый слой отвечает за ОДНУ вещь:

1. PROBABILITY CORE — чистый edge, БЕЗ regime/integrity
   base_probability = scoring_model(features, scores)
   
2. REGIME ENGINE — мягкий context shift (+/- 3-6%)
   final_probability = base_probability + regime_bias
   
3. INTEGRITY — риск ONLY (size/EV), НЕ probability
   risk_multiplier = integrity_risk(integrity)
   position_size *= risk_multiplier
   expected_value *= risk_multiplier

Архитектура:
    FEATURES
         ↓
    SCORING ENGINE
         ↓
    BASE PROBABILITY ← PURE EDGE MODEL
         ↓
    REGIME ADJUSTMENT ← SOFT BIAS (+/- 3-6%)
         ↓
    FINAL PROBABILITY
         ↓
    EV CALCULATION
         ↓
    INTEGRITY RISK SCALING ← ONLY SIZE + EV
         ↓
    POSITION SIZING
         ↓
    DECISION

Философия:
- Probability = "есть ли edge?"
- Regime = "какой тип рынка сейчас?"
- Integrity = "сколько мы готовы рискнуть?"
"""
import logging
from typing import Dict, Any
from dataclasses import dataclass

log = logging.getLogger("RegimeEngine")


@dataclass
class RegimeResult:
    """Результат regime-aware оценки."""
    regime: str
    regime_confidence: float
    base_probability: float       # Чистый edge из scoring
    regime_adjustment: float      # Мягкий bias (+/- 0.03-0.06)
    final_probability: float      # После regime adjustment
    integrity_multiplier: float    # Риск-множитель (НЕ влияет на probability)
    risk_multiplier: float        # Финальный risk scaling
    expected_value: float
    position_size_fraction: float  # Доля от базового size
    details: Dict[str, Any]


class RegimeProbabilityEngine:
    """
    Regime-aware Probability Engine v7.3.
    
    КРИТИЧЕСКИЕ ИЗМЕНЕНИЯ v7.3:
    ============================
    1. Regime = soft bias (+/- 3-6%), НЕ multiplier
       - trending: +3% probability
       - ranging: -1% probability
       - high_volatility: -4% probability
       - low_liquidity: -6% probability
    
    2. Integrity = risk ONLY (size/EV)
       - integrity >= 85: 1.0x risk
       - integrity >= 70: 0.85x risk
       - integrity >= 50: 0.65x risk
       - integrity < 50: 0.4x risk
    
    3. Probability остаётся стабильной
       - Больше нет падения 0.69 → 0.44
       - Только мягкий shift 0.69 → 0.66
    
    ПРИНЦИПЫ:
    - Probability отвечает за "есть ли edge?"
    - Regime отвечает за "какой рынок сейчас?"
    - Integrity отвечает за "сколько рискнуть?"
    """
    
    # ==================== REGIME DEFINITIONS ====================
    
    # МЯГКИЕ ADJUSTMENTS (НЕ multipliers!)
    # Философия: regime = context shift, НЕ probability killer
    
    REGIME_CONFIGS = {
        "trending": {
            "description": "Сильный тренд, направленное движение",
            "probability_bias": 0.03,      # +3% к probability (ADDITIVE)
            "ev_confidence": 1.0,          # Полная уверенность в EV
            "weight_adjustments": {
                "trend": 1.6,
                "setup": 1.0,
                "trigger": 1.5,
                "momentum": 1.4,
                "volume": 1.1,
            }
        },
        "ranging": {
            "description": "Боковик, mean-reversion работает",
            "probability_bias": -0.01,     # -1% к probability (ADDITIVE)
            "ev_confidence": 0.95,         # Немного снижаем EV уверенность
            "weight_adjustments": {
                "trend": 0.7,
                "setup": 1.6,
                "trigger": 1.2,
                "momentum": 0.8,
                "volume": 1.3,
            }
        },
        "high_volatility": {
            "description": "Высокая волатильность, резкие движения",
            "probability_bias": -0.04,     # -4% к probability (ADDITIVE)
            "ev_confidence": 0.90,         # Снижаем EV уверенность
            "weight_adjustments": {
                "trend": 1.2,
                "setup": 1.0,
                "trigger": 1.8,
                "momentum": 1.5,
                "volume": 1.6,
            }
        },
        "low_liquidity": {
            "description": "Низкая ликвидность, данные ненадёжны",
            "probability_bias": -0.06,     # -6% к probability (ADDITIVE, MAX)
            "ev_confidence": 0.80,         # Существенно снижаем EV
            "weight_adjustments": {
                "trend": 1.0,
                "setup": 0.8,
                "trigger": 0.9,
                "momentum": 0.7,
                "volume": 0.5,
            }
        },
        "normal": {
            "description": "Нормальные рыночные условия",
            "probability_bias": 0.0,       # Без изменения
            "ev_confidence": 1.0,
            "weight_adjustments": {
                "trend": 1.3,
                "setup": 1.2,
                "trigger": 1.3,
                "momentum": 1.0,
                "volume": 1.0,
            }
        }
    }
    
    # ==================== INTEGRITY RISK MULTIPLIERS ====================
    
    # Integrity влияет ТОЛЬКО на risk (size/EV), НЕ на probability
    
    INTEGRITY_RISK = {
        85: 1.0,    # >= 85: полный риск
        70: 0.85,   # >= 70: 85% риска
        50: 0.65,   # >= 50: 65% риска
        30: 0.50,   # >= 30: 50% риска
        0: 0.40,    # < 30: минимальный риск
    }
    
    # ==================== DETECTION THRESHOLDS ====================
    
    DETECTION_THRESHOLDS = {
        "adx_trending": 25,
        "adx_ranging": 15,
        "momentum_trending": 0.003,
        "volatility_high": 0.04,
        "volume_low": 0.3,
        "integrity_low": 50,
    }
    
    def __init__(self):
        """Инициализация Regime Engine."""
        self.regime_history = {}
    
    # ==================== MAIN API ====================
    
    def evaluate(
        self,
        features: Dict,
        score_breakdown: Dict,
        integrity_score: float = 100,
        equity: float = 1000,
    ) -> Dict[str, Any]:
        """
        Вычислить probability и risk с учётом regime и integrity.
        
        КЛЮЧЕВОЕ ИЗМЕНЕНИЕ v7.3:
        - Probability: base + regime_bias (ADDITIVE)
        - Risk: integrity_multiplier (MULTIPLICATIVE)
        
        Args:
            features: Признаки рынка (adx, volatility, volume_ratio, momentum)
            score_breakdown: {trend_score, setup_score, trigger_score}
            integrity_score: Качество данных (0-100)
            equity: Размер счёта
            
        Returns:
            {
                base_probability: float,      # Чистый edge
                regime: str,                  # Рыночный режим
                regime_bias: float,           # Мягкий shift (+/- 0.03-0.06)
                final_probability: float,     # После regime adjustment
                integrity_multiplier: float,   # Risk scaling
                expected_value: float,
                position_size_fraction: float,
            }
        """
        # ========== STEP 1: DETECT REGIME ==========
        regime_state = self.detect_regime(features, integrity_score)
        regime = regime_state.regime
        regime_confidence = regime_state.confidence
        
        # ========== STEP 2: BASE PROBABILITY (UNCHANGED) ==========
        # Чистый edge из scoring, БЕЗ regime/integrity
        base_probability = self._compute_base_probability(score_breakdown)
        
        # ========== STEP 3: REGIME ADJUSTMENT (SOFT BIAS) ==========
        # Мягкий shift, НЕ multiplier
        regime_config = self.REGIME_CONFIGS.get(regime, self.REGIME_CONFIGS["normal"])
        regime_bias = regime_config["probability_bias"]
        
        # Apply regime bias (ADDITIVE!)
        final_probability = base_probability + regime_bias
        
        # Clamp probability [0, 1]
        final_probability = max(0.0, min(1.0, final_probability))
        
        # ========== STEP 4: INTEGRITY RISK SCALING ==========
        # Integrity влияет ТОЛЬКО на risk, НЕ на probability
        integrity_multiplier = self._get_integrity_risk(integrity_score)
        
        # ========== STEP 5: EV CALCULATION ==========
        # EV зависит от probability и regime confidence
        expected_value = self._compute_ev(
            final_probability,
            regime_config["ev_confidence"]
        )
        
        # ========== STEP 6: POSITION SIZE ==========
        # Размер позиции умножается на risk_multiplier
        position_size_fraction = self._compute_size_fraction(
            final_probability,
            integrity_multiplier
        )
        
        # ========== LOG ==========
        log.debug(
            f"Regime={regime} ({regime_confidence:.0%}) | "
            f"Prob: {base_probability:.2f} + {regime_bias:+.2f} = {final_probability:.2f} | "
            f"Integrity={integrity_score:.0f} → risk={integrity_multiplier:.2f} | "
            f"Size={position_size_fraction:.0%}"
        )
        
        return {
            "base_probability": base_probability,
            "regime": regime,
            "regime_confidence": regime_confidence,
            "regime_bias": regime_bias,
            "final_probability": final_probability,
            "integrity_multiplier": integrity_multiplier,
            "expected_value": expected_value,
            "position_size_fraction": position_size_fraction,
            "details": {
                "adx": regime_state.adx,
                "volatility": regime_state.volatility,
                "volume_ratio": regime_state.volume_ratio,
                "momentum": regime_state.momentum,
                "integrity_score": integrity_score,
            }
        }
    
    def detect_regime(self, features: Dict, integrity_score: float = 100) -> "RegimeState":
        """
        Определить рыночный режим.
        
        Args:
            features: Признаки рынка
            integrity_score: Качество данных
            
        Returns:
            RegimeState с режимом и уверенностью
        """
        from dataclasses import dataclass as dc_dataclass
        
        adx = features.get("adx", 20)
        volatility = features.get("volatility", 0.02)
        volume_ratio = features.get("volume_ratio", 1.0)
        momentum = abs(features.get("momentum", 0))
        ema_distance = features.get("ema_distance", 0)
        
        # ========== LOW LIQUIDITY (первичный фильтр) ==========
        if integrity_score < 50 or volume_ratio < 0.3:
            confidence = 0.5
            if integrity_score < 30:
                confidence = 0.8
            elif integrity_score < 50:
                confidence = 0.6
            
            return RegimeState(
                regime="low_liquidity",
                confidence=confidence,
                adx=adx,
                volatility=volatility,
                volume_ratio=volume_ratio,
                momentum=momentum,
                integrity_score=integrity_score
            )
        
        # ========== HIGH VOLATILITY ==========
        if volatility > 0.04:
            confidence = self._calc_confidence(
                volatility > 0.04,
                momentum > 0.3
            )
            
            return RegimeState(
                regime="high_volatility",
                confidence=confidence,
                adx=adx,
                volatility=volatility,
                volume_ratio=volume_ratio,
                momentum=momentum,
                integrity_score=integrity_score
            )
        
        # ========== TRENDING ==========
        if adx > 25 and (momentum > 0.3 or abs(ema_distance) > 0.02):
            confidence = self._calc_confidence(
                adx > 25,
                momentum > 0.3,
                abs(ema_distance) > 0.02
            )
            
            return RegimeState(
                regime="trending",
                confidence=confidence,
                adx=adx,
                volatility=volatility,
                volume_ratio=volume_ratio,
                momentum=momentum,
                integrity_score=integrity_score
            )
        
        # ========== RANGING ==========
        if adx < 15 and momentum < 0.2:
            confidence = self._calc_confidence(
                adx < 15,
                momentum < 0.2,
                volatility < 0.02
            )
            
            return RegimeState(
                regime="ranging",
                confidence=confidence,
                adx=adx,
                volatility=volatility,
                volume_ratio=volume_ratio,
                momentum=momentum,
                integrity_score=integrity_score
            )
        
        # ========== NORMAL (default) ==========
        return RegimeState(
            regime="normal",
            confidence=0.5,
            adx=adx,
            volatility=volatility,
            volume_ratio=volume_ratio,
            momentum=momentum,
            integrity_score=integrity_score
        )
    
    # ==================== PRIVATE METHODS ====================
    
    def _compute_base_probability(self, score_breakdown: Dict) -> float:
        """
        Вычислить чистый edge из scoring.
        
        БЕЗ regime/integrity — только scoring!
        """
        trend_score = score_breakdown.get("trend_score", 0)
        setup_score = score_breakdown.get("setup_score", 0)
        trigger_score = score_breakdown.get("trigger_score", 0)
        
        total = trend_score + setup_score + trigger_score
        
        # Normalise to [0, 1]
        return total / 100.0
    
    def _get_integrity_risk(self, integrity_score: float) -> float:
        """
        Получить risk multiplier на основе integrity.
        
        Влияет ТОЛЬКО на size/EV, НЕ на probability!
        """
        for threshold, multiplier in sorted(self.INTEGRITY_RISK.items(), reverse=True):
            if integrity_score >= threshold:
                return multiplier
        return 0.4
    
    def _compute_ev(self, probability: float, ev_confidence: float) -> float:
        """
        Вычислить expected value.
        
        EV = probability × ev_confidence
        """
        # Базовый EV: win_rate × reward - (1 - win_rate) × risk
        # Упрощённая формула: probability × 2 - 1 (risk:reward = 1:2)
        base_ev = (probability * 2 - 1) if probability > 0.5 else -(1 - probability)
        
        return base_ev * ev_confidence
    
    def _compute_size_fraction(self, probability: float, risk_multiplier: float) -> float:
        """
        Вычислить долю позиции.
        
        Kelly criterion упрощённый: 
        position_size = min(probability - 0.5, 0.3) × risk_multiplier
        """
        if probability < 0.55:
            return 0.0
        
        # Базовый Kelly: probability - 0.5
        base_fraction = min(probability - 0.5, 0.3)  # Max 30%
        
        # Apply risk multiplier
        return base_fraction * risk_multiplier
    
    def _calc_confidence(self, *conditions) -> float:
        """
        Вычислить уверенность в режиме.
        
        More conditions = higher confidence.
        """
        return min(len([c for c in conditions if c]) / max(len(conditions), 1), 1.0)
    
    def get_regime_weights(self, regime: str) -> Dict[str, float]:
        """Получить веса для режима."""
        config = self.REGIME_CONFIGS.get(regime, self.REGIME_CONFIGS["normal"])
        return config["weight_adjustments"]


@dataclass
class RegimeState:
    """Состояние рыночного режима."""
    regime: str
    confidence: float
    adx: float
    volatility: float
    volume_ratio: float
    momentum: float
    integrity_score: float