"""T5 Strategy Router — policy selection engine.

Не принимает торговых решений. Не открывает ордера.
Определяет: как система должна мыслить в текущем рынке.

Вход: SignalVector + FeatureSnapshot
Выход: StrategyContext (policy space)
"""
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Enums ──────────────────────────────────────────────────

class StrategyMode:
    TREND = "TREND"
    SCALP = "SCALP"
    GRID = "GRID"
    IDLE = "IDLE"
    ALL = [TREND, SCALP, GRID, IDLE]


class RiskMode:
    AGGRESSIVE = "AGGRESSIVE"
    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"
    NO_TRADE = "NO_TRADE"
    ALL = [AGGRESSIVE, NORMAL, DEFENSIVE, NO_TRADE]


class PositionIntent:
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    LONG_SHORT = "LONG_SHORT"
    FLAT = "FLAT"


class EntryType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    BREAKOUT = "BREAKOUT"


# ── Strategy Context (выход T5) ───────────────────────────

@dataclass
class StrategyContext:
    """Результат policy selection — что система должна делать."""
    timestamp: str
    symbol: str

    # Mode
    strategy_mode: str = StrategyMode.IDLE
    mode_scores: dict = field(default_factory=dict)     # {TREND: 0.72, SCALP: 0.31, ...}
    mode_confidence: float = 0.0

    # Risk
    risk_mode: str = RiskMode.NORMAL
    risk_score: float = 0.0                             # 0.0 (safe) .. 1.0 (dangerous)

    # Position
    position_intent: str = PositionIntent.FLAT
    max_positions: int = 0
    position_size_pct: float = 0.0                      # % of capital per position

    # Entry
    entry_type: str = EntryType.MARKET
    cooldown_seconds: int = 300

    # Capital
    capital_fraction: float = 0.0                       # total capital to deploy
    base_allocation: float = 0.0

    # Constraints
    allow_reversal_entries: bool = False
    max_frequency: str = "normal"                       # low / normal / high
    tp_style: str = "standard"                          # tight / standard / wide
    sl_style: str = "standard"

    # Meta
    reasoning: str = ""
    signal_quality: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"mode={self.strategy_mode} risk={self.risk_mode} "
            f"intent={self.position_intent} capital={self.capital_fraction:.0%} "
            f"size={self.position_size_pct:.0%} entry={self.entry_type} "
            f"reason={self.reasoning}"
        )


# ── Mode Scorer ───────────────────────────────────────────

class ModeScorer:
    """Scoring-based mode selection (не if-else)."""

    def score(self, signal: dict, features: dict) -> dict[str, float]:
        """Возвращает {mode: score} для каждого режима."""
        trend = signal.get("trend", 0)
        reversal = signal.get("reversal", 0)
        momentum = signal.get("momentum", 0)
        vol_pressure = signal.get("volatility_pressure", 0)
        trust = signal.get("market_trust", 0)
        bias = abs(signal.get("directional_bias", 0))

        # Extract feature values
        adx = features.get("adx_trend_strength", {}).get("value", 0) / 100.0
        consistency = features.get("trend_consistency", {}).get("value", 0) / 100.0
        cycle = features.get("cycle_speed", {}).get("value", 60)
        density = features.get("event_density_1h", {}).get("value", 0)
        regime_str = features.get("regime_strength", {}).get("value", 0)

        # Cycle speed normalized (faster = higher)
        speed = max(0, 1.0 - cycle / 60.0) if cycle >= 0 else 0.5

        scores = {}

        # TREND: сильный тренд, высокая consistency, низкий noise
        scores[StrategyMode.TREND] = (
            trend * 0.30 +
            adx * 0.25 +
            consistency * 0.20 +
            regime_str * 0.15 +
            (1 - vol_pressure) * 0.10
        )

        # SCALP: высокая волатильность, быстрые циклы, низкий trust
        scores[StrategyMode.SCALP] = (
            vol_pressure * 0.35 +
            speed * 0.25 +
            momentum * 0.20 +
            (1 - trust) * 0.10 +
            (density / 20.0 if density > 0 else 0.5) * 0.10
        )

        # GRID: низкая волатильность, стабильный режим
        scores[StrategyMode.GRID] = (
            (1 - vol_pressure) * 0.30 +
            regime_str * 0.25 +
            (1 - adx) * 0.20 +
            trust * 0.15 +
            (1 - speed) * 0.10
        )

        # IDLE: нет ясного сигнала, низкий confidence
        scores[StrategyMode.IDLE] = (
            (1 - trust) * 0.30 +
            (1 - bias) * 0.25 +
            (1 - vol_pressure) * 0.20 +
            (1 - trend) * 0.15 +
            (1 - momentum) * 0.10
        )

        return scores


# ── Risk Engine ────────────────────────────────────────────

class RiskEngine:
    """Определяет risk mode на основе market conditions."""

    def score(self, signal: dict, features: dict) -> tuple[str, float]:
        """Возвращает (risk_mode, risk_score 0..1)."""
        trust = signal.get("market_trust", 0)
        vol_pressure = signal.get("volatility_pressure", 0)
        regime_str = features.get("regime_strength", {}).get("value", 0)
        density = features.get("event_density_1h", {}).get("value", 0)

        # Risk components
        trust_risk = 1.0 - trust                    # low trust = high risk
        vol_risk = vol_pressure                      # high vol = high risk
        regime_risk = 1.0 - regime_str              # unstable regime = high risk
        density_risk = min(density / 30.0, 1.0)     # high density = stress

        # Weighted risk score
        risk = (
            trust_risk * 0.30 +
            vol_risk * 0.30 +
            regime_risk * 0.20 +
            density_risk * 0.20
        )

        # Map to risk mode
        if risk < 0.25:
            mode = RiskMode.AGGRESSIVE
        elif risk < 0.50:
            mode = RiskMode.NORMAL
        elif risk < 0.75:
            mode = RiskMode.DEFENSIVE
        else:
            mode = RiskMode.NO_TRADE

        return mode, round(risk, 3)


# ── Capital Allocator ─────────────────────────────────────

class CapitalAllocator:
    """Определяет долю капитала для использования."""

    # Base allocations per mode
    BASE = {
        StrategyMode.TREND: 0.40,
        StrategyMode.SCALP: 0.25,
        StrategyMode.GRID: 0.30,
        StrategyMode.IDLE: 0.0,
    }

    # Risk multipliers
    RISK_MULT = {
        RiskMode.AGGRESSIVE: 1.2,
        RiskMode.NORMAL: 1.0,
        RiskMode.DEFENSIVE: 0.6,
        RiskMode.NO_TRADE: 0.0,
    }

    def allocate(
        self,
        strategy_mode: str,
        risk_mode: str,
        confidence: float,
        signal_quality: float,
    ) -> tuple[float, float]:
        """Возвращает (capital_fraction, position_size_pct)."""
        base = self.BASE.get(strategy_mode, 0)
        risk_mult = self.RISK_MULT.get(risk_mode, 0)

        # Capital fraction
        capital = base * risk_mult * confidence * signal_quality
        capital = min(capital, 0.6)  # max 60% capital

        # Position size = capital / max_positions (assume 2 for now)
        max_pos = 2 if strategy_mode != StrategyMode.IDLE else 0
        size = capital / max_pos if max_pos > 0 else 0

        return round(capital, 3), round(size, 3)


# ── Mode Constraints ──────────────────────────────────────

class ModeConstraints:
    """Soft constraints для каждого mode."""

    CONSTRAINTS = {
        StrategyMode.TREND: {
            "max_positions": 2,
            "allow_reversal_entries": False,
            "max_frequency": "normal",
            "tp_style": "wide",
            "sl_style": "standard",
            "entry_type": EntryType.BREAKOUT,
            "cooldown_seconds": 120,
        },
        StrategyMode.SCALP: {
            "max_positions": 3,
            "allow_reversal_entries": True,
            "max_frequency": "high",
            "tp_style": "tight",
            "sl_style": "tight",
            "entry_type": EntryType.MARKET,
            "cooldown_seconds": 30,
        },
        StrategyMode.GRID: {
            "max_positions": 5,
            "allow_reversal_entries": False,
            "max_frequency": "low",
            "tp_style": "standard",
            "sl_style": "wide",
            "entry_type": EntryType.LIMIT,
            "cooldown_seconds": 300,
        },
        StrategyMode.IDLE: {
            "max_positions": 0,
            "allow_reversal_entries": False,
            "max_frequency": "low",
            "tp_style": "standard",
            "sl_style": "standard",
            "entry_type": EntryType.MARKET,
            "cooldown_seconds": 600,
        },
    }

    def get(self, mode: str) -> dict:
        return self.CONSTRAINTS.get(mode, self.CONSTRAINTS[StrategyMode.IDLE])


# ── Strategy Router (ядро T5) ─────────────────────────────

class StrategyRouter:
    """Policy selection engine: SignalVector → StrategyContext."""

    def __init__(self):
        self._scorer = ModeScorer()
        self._risk = RiskEngine()
        self._allocator = CapitalAllocator()
        self._constraints = ModeConstraints()

    def route(
        self,
        signal_vector: dict,
        feature_snapshot: dict,
        symbol: str = "BTCUSDT",
    ) -> StrategyContext:
        """Выбрать policy для текущего состояния рынка."""
        signal = signal_vector
        features = feature_snapshot.get("features", {})

        # 1. Score all modes
        mode_scores = self._scorer.score(signal, features)
        best_mode = max(mode_scores, key=mode_scores.get)
        mode_conf = mode_scores[best_mode]

        # 2. Risk assessment
        risk_mode, risk_score = self._risk.score(signal, features)

        # 3. If risk is NO_TRADE, override to IDLE
        if risk_mode == RiskMode.NO_TRADE:
            best_mode = StrategyMode.IDLE
            mode_conf = 0.0

        # 4. Capital allocation
        confidence = signal.get("confidence", 0)
        signal_quality = signal.get("signal_quality", 0)
        capital_frac, size_pct = self._allocator.allocate(
            best_mode, risk_mode, confidence, signal_quality,
        )

        # 5. Position intent from directional bias
        bias = signal.get("directional_bias", 0)
        if bias > 0.3:
            intent = PositionIntent.LONG_ONLY
        elif bias < -0.3:
            intent = PositionIntent.SHORT_ONLY
        elif abs(bias) <= 0.3 and best_mode != StrategyMode.IDLE:
            intent = PositionIntent.LONG_SHORT
        else:
            intent = PositionIntent.FLAT

        # 6. Constraints
        c = self._constraints.get(best_mode)

        # 7. Build context
        ctx = StrategyContext(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            strategy_mode=best_mode,
            mode_scores={k: round(v, 3) for k, v in mode_scores.items()},
            mode_confidence=round(mode_conf, 3),
            risk_mode=risk_mode,
            risk_score=risk_score,
            position_intent=intent,
            max_positions=c["max_positions"],
            position_size_pct=size_pct,
            entry_type=c["entry_type"],
            cooldown_seconds=c["cooldown_seconds"],
            capital_fraction=capital_frac,
            base_allocation=self._allocator.BASE.get(best_mode, 0),
            allow_reversal_entries=c["allow_reversal_entries"],
            max_frequency=c["max_frequency"],
            tp_style=c["tp_style"],
            sl_style=c["sl_style"],
            signal_quality=signal_quality,
            reasoning=self._build_reasoning(
                best_mode, risk_mode, mode_scores, risk_score, confidence,
            ),
        )

        return ctx

    def _build_reasoning(
        self, mode, risk_mode, scores, risk_score, confidence
    ) -> str:
        top3 = sorted(scores.items(), key=lambda x: -x[1])[:3]
        score_str = ", ".join(f"{k}={v:.2f}" for k, v in top3)
        return f"selected={mode} ({score_str}) risk={risk_mode}({risk_score:.2f}) conf={confidence:.2f}"
