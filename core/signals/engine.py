"""T4 Signal Engine — переход от features к probability space.

Не принимает торговых решений. Не содержит стратегий.
Преобразует FeatureSnapshot → SignalVector.

Каждый сигнал — группа математических проекций features.
Результат: числовые значения 0.0–1.0 для каждого типа сигнала.
"""
import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Signal Vector ──────────────────────────────────────────

@dataclass
class SignalVector:
    """Вектор сигналов — результат интерпретации features."""
    timestamp: str
    symbol: str

    # Core signals (0.0–1.0)
    trend: float = 0.0
    reversal: float = 0.0
    momentum: float = 0.0
    volatility_pressure: float = 0.0
    market_trust: float = 0.0

    # Regime context
    regime: str = "UNKNOWN"
    regime_strength: float = 0.0

    # Composite
    directional_bias: float = 0.0    # -1.0 (bearish) .. 0.0 .. +1.0 (bullish)
    confidence: float = 0.0          # общая уверенность в сигнале
    signal_quality: float = 0.0      # качество данных, на которых основан сигнал

    # Metadata
    feature_count: int = 0
    normalization_method: str = "ema_decay"

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"trend={self.trend:.2f} reversal={self.reversal:.2f} "
            f"momentum={self.momentum:.2f} vol_pressure={self.volatility_pressure:.2f} "
            f"trust={self.market_trust:.2f} bias={self.directional_bias:+.2f} "
            f"conf={self.confidence:.2f} regime={self.regime}"
        )


# ── Signal Mappers ─────────────────────────────────────────
# Каждый маппер: features → один сигнал

class TrendMapper:
    """Trend signal: ADX + trend_consistency + regime."""
    def compute(self, features: dict) -> float:
        adx = features.get("adx_trend_strength", 0)
        consistency = features.get("trend_consistency", 0)
        regime_str = features.get("regime_strength", 0)
        regime_name = ""
        # Extract regime from metadata
        rs = features.get("regime_strength_obj")
        if rs and isinstance(rs, dict):
            regime_name = rs.get("metadata", {}).get("regime", "")

        # ADX: 0-100 → 0-1
        adx_norm = min(adx / 50.0, 1.0)
        # Consistency: 0-100 → 0-1
        cons_norm = consistency / 100.0
        # Regime boost
        regime_boost = 1.2 if regime_name == "TREND" else 1.0

        raw = (adx_norm * 0.5 + cons_norm * 0.3 + regime_str * 0.2) * regime_boost
        return clamp(raw)


class ReversalMapper:
    """Reversal signal: volatility spike + density anomaly + regime instability."""
    def compute(self, features: dict) -> float:
        atr = features.get("atr_smoothed", 0)
        density = features.get("event_density_1h", 0)
        regime_str = features.get("regime_strength", 0)

        # ATR spike: high ATR relative to price → reversal potential
        price = features.get("price_last", 1)
        atr_ratio = atr / price if price > 0 else 0
        vol_score = min(atr_ratio * 100, 1.0)

        # Density anomaly: high event density → market stress
        density_score = min(density / 50.0, 1.0)

        # Regime instability: low regime confidence → reversal potential
        instability = 1.0 - regime_str

        raw = vol_score * 0.4 + density_score * 0.3 + instability * 0.3
        return clamp(raw)


class MomentumMapper:
    """Momentum quality: volume + cycle_speed + ADX direction."""
    def compute(self, features: dict) -> float:
        vol = features.get("volume_notional", 0)
        cycle = features.get("cycle_speed", 60)
        adx = features.get("adx_trend_strength", 0)

        # Volume: normalize to 0-1 (assume 1B = max)
        vol_score = min(vol / 1_000_000_000, 1.0) if vol > 0 else 0

        # Cycle speed: faster cycles = stronger momentum
        # 60s = normal, 0s = very fast
        speed_score = max(0, 1.0 - cycle / 60.0) if cycle >= 0 else 0.5

        # ADX contributes to momentum strength
        adx_score = min(adx / 50.0, 1.0)

        raw = vol_score * 0.4 + speed_score * 0.3 + adx_score * 0.3
        return clamp(raw)


class VolatilityPressureMapper:
    """Volatility pressure: ATR + event density + price range."""
    def compute(self, features: dict) -> float:
        atr = features.get("atr_smoothed", 0)
        price = features.get("price_last", 1)
        density = features.get("event_density_1h", 0)

        # ATR relative to price
        atr_pct = (atr / price * 100) if price > 0 else 0
        vol_score = min(atr_pct / 2.0, 1.0)  # 2% ATR = max pressure

        # High event density = market stress
        density_score = min(density / 30.0, 1.0)

        raw = vol_score * 0.6 + density_score * 0.4
        return clamp(raw)


class MarketTrustMapper:
    """Market trust: regime stability + data quality + consistency."""
    def compute(self, features: dict) -> float:
        regime_str = features.get("regime_strength", 0)
        consistency = features.get("trend_consistency", 0)
        density = features.get("event_density_1h", 0)

        # High regime confidence = trust
        regime_score = regime_str

        # Consistency = trust
        cons_score = consistency / 100.0

        # Normal density = trust (too high or too low = distrust)
        if density > 0:
            density_norm = min(density / 20.0, 1.0)
        else:
            density_norm = 0.5

        raw = regime_score * 0.4 + cons_score * 0.3 + density_norm * 0.3
        return clamp(raw)


# ── Signal Fusion ──────────────────────────────────────────

class SignalFusion:
    """Объединение сигналов с учётом regime context."""

    def fuse(
        self,
        trend: float,
        reversal: float,
        momentum: float,
        vol_pressure: float,
        trust: float,
        regime: str,
        regime_strength: float,
    ) -> tuple[float, float]:
        """Возвращает (directional_bias, confidence)."""
        # Trend-dominated regime: boost trend, suppress reversal
        if regime == "TREND":
            dir_bias = trend * regime_strength - reversal * (1 - regime_strength)
            conf = (trend * 0.4 + momentum * 0.3 + trust * 0.3) * regime_strength
        # Range regime: boost reversal, suppress trend
        elif regime == "RANGE":
            dir_bias = reversal * 0.3 - trend * 0.1  # slight mean-reversion bias
            conf = (reversal * 0.4 + trust * 0.3 + vol_pressure * 0.3) * regime_strength
        # Crash/Recovery: high vol, low confidence
        elif regime in ("CRASH", "RECOVERY"):
            dir_bias = 0.0  # no directional bias in chaos
            conf = trust * 0.3  # very low confidence
        else:
            dir_bias = (trend - reversal) * 0.5
            conf = (trend + momentum + trust) / 3.0

        # Volatility pressure reduces confidence
        conf *= (1.0 - vol_pressure * 0.3)

        return clamp(dir_bias, -1.0, 1.0), clamp(conf)


# ── Signal Engine ──────────────────────────────────────────

class SignalEngine:
    """Основной engine: FeatureSnapshot → SignalVector."""

    def __init__(self):
        self._trend = TrendMapper()
        self._reversal = ReversalMapper()
        self._momentum = MomentumMapper()
        self._volatility = VolatilityPressureMapper()
        self._trust = MarketTrustMapper()
        self._fusion = SignalFusion()

        # EMA smoothing state
        self._prev_signals: Optional[SignalVector] = None
        self._ema_alpha = 0.3  # smoothing factor

    def compute(self, feature_snapshot: dict) -> SignalVector:
        """Вычислить signal vector из feature snapshot."""
        features = feature_snapshot.get("features", {})

        # Extract regime info
        regime_obj = features.get("regime_strength", {})
        if isinstance(regime_obj, dict):
            regime = regime_obj.get("metadata", {}).get("regime", "UNKNOWN")
            regime_strength = regime_obj.get("value", 0)
        else:
            regime = "UNKNOWN"
            regime_strength = 0

        # Extract raw feature values
        raw_features = {}
        for name, fval in features.items():
            if isinstance(fval, dict):
                raw_features[name] = fval.get("value", 0)
                if name == "regime_strength":
                    raw_features["regime_strength_obj"] = fval
            else:
                raw_features[name] = fval

        # Compute signals
        trend = self._trend.compute(raw_features)
        reversal = self._reversal.compute(raw_features)
        momentum = self._momentum.compute(raw_features)
        vol_pressure = self._volatility.compute(raw_features)
        trust = self._trust.compute(raw_features)

        # Fuse
        dir_bias, conf = self._fusion.fuse(
            trend, reversal, momentum, vol_pressure, trust,
            regime, regime_strength,
        )

        # Signal quality: based on how many features contributed
        feature_count = len([v for v in raw_features.values() if v != 0])
        signal_quality = min(feature_count / 8.0, 1.0)

        # Build vector
        vec = SignalVector(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=feature_snapshot.get("symbol", "BTCUSDT"),
            trend=trend,
            reversal=reversal,
            momentum=momentum,
            volatility_pressure=vol_pressure,
            market_trust=trust,
            regime=regime,
            regime_strength=regime_strength,
            directional_bias=dir_bias,
            confidence=conf,
            signal_quality=signal_quality,
            feature_count=feature_count,
        )

        # EMA smoothing
        if self._prev_signals:
            vec = self._smooth(vec)

        self._prev_signals = vec
        return vec

    def _smooth(self, current: SignalVector) -> SignalVector:
        """EMA smoothing между текущим и предыдущим сигналом."""
        prev = self._prev_signals
        a = self._ema_alpha

        current.trend = a * current.trend + (1 - a) * prev.trend
        current.reversal = a * current.reversal + (1 - a) * prev.reversal
        current.momentum = a * current.momentum + (1 - a) * prev.momentum
        current.volatility_pressure = a * current.volatility_pressure + (1 - a) * prev.volatility_pressure
        current.market_trust = a * current.market_trust + (1 - a) * prev.market_trust
        current.directional_bias = a * current.directional_bias + (1 - a) * prev.directional_bias
        current.confidence = a * current.confidence + (1 - a) * prev.confidence

        return current

    def reset(self):
        """Сброс состояния (для backtest)."""
        self._prev_signals = None


# ── Helpers ────────────────────────────────────────────────

def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    return max(min_val, min(max_val, value))
