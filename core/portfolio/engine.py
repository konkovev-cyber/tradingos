"""Portfolio Intelligence Layer — multi-market capital brain.

Расширяет TradingOS v1 с single-market на multi-market.
1 brain → N markets → shared risk pool.

Модули:
1. CorrelationEngine — корреляция между активами
2. CapitalAllocator — распределение капитала
3. ExposureController — контроль экспозиции
4. PortfolioGovernor — portfolio-level risk
"""
import json
import math
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from collections import deque


# ── Asset Snapshot ─────────────────────────────────────────

@dataclass
class AssetSnapshot:
    """Снимок состояния одного актива."""
    symbol: str
    price: float
    regime: str
    regime_strength: float
    trend: float
    momentum: float
    volatility: float
    signal_confidence: float
    directional_bias: float
    strategy_mode: str
    risk_mode: str
    # Current state
    open_position: bool = False
    position_pnl: float = 0.0
    weight: float = 0.0          # current capital allocation

    def to_dict(self) -> dict:
        return asdict(self)


# ── Correlation Engine ─────────────────────────────────────

class CorrelationEngine:
    """Вычисляет и отслеживает корреляции между активами."""

    def __init__(self, window: int = 50):
        self._window = window
        self._returns: dict[str, deque] = {}  # symbol → deque of returns

    def update(self, symbol: str, price: float):
        """Добавить цену и обновить корреляции."""
        if symbol not in self._returns:
            self._returns[symbol] = deque(maxlen=self._window)

        # Append return
        returns = self._returns[symbol]
        if len(returns) > 0:
            prev = returns[-1]
            if prev != 0:
                ret = (price - prev) / prev
                returns.append(price)
            else:
                returns.append(price)
        else:
            returns.append(price)

    def get_correlation(self, sym_a: str, sym_b: str) -> float:
        """Корреляция Пирсона между двумя активами."""
        if sym_a not in self._returns or sym_b not in self._returns:
            return 0.0

        a = list(self._returns[sym_a])
        b = list(self._returns[sym_b])

        if len(a) < 10 or len(b) < 10:
            return 0.0

        # Align lengths
        n = min(len(a), len(b))
        a = a[-n:]
        b = b[-n:]

        # Compute returns
        ret_a = [(a[i] - a[i-1]) / a[i-1] if a[i-1] != 0 else 0 for i in range(1, len(a))]
        ret_b = [(b[i] - b[i-1]) / b[i-1] if b[i-1] != 0 else 0 for i in range(1, len(b))]

        n = min(len(ret_a), len(ret_b))
        if n < 5:
            return 0.0

        ret_a = ret_a[-n:]
        ret_b = ret_b[-n:]

        mean_a = sum(ret_a) / n
        mean_b = sum(ret_b) / n

        cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(ret_a, ret_b)) / n
        std_a = (sum((a - mean_a) ** 2 for a in ret_a) / n) ** 0.5
        std_b = (sum((b - mean_b) ** 2 for b in ret_b) / n) ** 0.5

        if std_a == 0 or std_b == 0:
            return 0.0

        return cov / (std_a * std_b)

    def get_correlation_matrix(self, symbols: list[str]) -> dict:
        """Матрица корреляций."""
        matrix = {}
        for i, sym_a in enumerate(symbols):
            matrix[sym_a] = {}
            for sym_b in symbols:
                if i == symbols.index(sym_b):
                    matrix[sym_a][sym_b] = 1.0
                else:
                    matrix[sym_a][sym_b] = round(self.get_correlation(sym_a, sym_b), 3)
        return matrix

    def get_avg_correlation(self, symbols: list[str]) -> float:
        """Средняя корреляция между всеми парами."""
        pairs = []
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                c = self.get_correlation(symbols[i], symbols[j])
                pairs.append(abs(c))
        return sum(pairs) / len(pairs) if pairs else 0.0

    def get_diversification_score(self, symbols: list[str]) -> float:
        """Оценка диверсификации: 1.0 = идеальная, 0.0 = все коррелированы."""
        avg_corr = self.get_avg_correlation(symbols)
        return 1.0 - avg_corr


# ── Capital Allocator ──────────────────────────────────────

class CapitalAllocator:
    """Распределяет капитал между активами на основе risk-adjusted scoring."""

    def __init__(self):
        self._min_allocation = 0.05     # min 5% per asset
        self._max_allocation = 0.50     # max 50% per asset
        self._total_equity = 10000.0

    def allocate(
        self,
        snapshots: dict[str, AssetSnapshot],
        correlation_engine: CorrelationEngine,
        risk_mode: str = "NORMAL",
    ) -> dict[str, float]:
        """Распределить капитал. Возвращает {symbol: weight}."""
        if not snapshots:
            return {}

        symbols = list(snapshots.keys())

        # 1. Score each asset
        scores = {}
        for sym, snap in snapshots.items():
            score = self._score_asset(snap)
            # Penalty for high correlation with already-selected assets
            corr_penalty = self._correlation_penalty(sym, symbols, correlation_engine)
            scores[sym] = score * (1 - corr_penalty * 0.3)

        # 2. Normalize scores to weights
        total_score = sum(max(s, 0.01) for s in scores.values())
        weights = {sym: max(s, 0.01) / total_score for sym, s in scores.items()}

        # 3. Apply risk mode scaling
        risk_mult = {"AGGRESSIVE": 1.2, "NORMAL": 1.0, "DEFENSIVE": 0.6, "NO_TRADE": 0.0}
        mult = risk_mult.get(risk_mode, 1.0)

        # 4. Apply limits
        for sym in weights:
            weights[sym] *= mult
            weights[sym] = max(self._min_allocation, min(self._max_allocation, weights[sym]))

        # 5. Re-normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {sym: w / total for sym, w in weights.items()}

        return {sym: round(w, 3) for sym, w in weights.items()}

    def _score_asset(self, snap: AssetSnapshot) -> float:
        """Score asset: higher = more attractive."""
        score = 0.0

        # Signal quality
        score += snap.signal_confidence * 0.25

        # Regime strength (strong regime = more tradeable)
        score += snap.regime_strength * 0.20

        # Trend alignment (trend + momentum same direction)
        if snap.trend > 0.5 and snap.directional_bias > 0:
            score += 0.15
        elif snap.trend < 0.5 and snap.directional_bias < 0:
            score += 0.15

        # Low volatility = more stable
        score += (1 - min(snap.volatility, 1.0)) * 0.15

        # Active regime (not IDLE)
        if snap.strategy_mode != "IDLE":
            score += 0.15

        # Risk mode (AGGRESSIVE = higher potential)
        if snap.risk_mode == "AGGRESSIVE":
            score += 0.10

        return score

    def _correlation_penalty(
        self,
        symbol: str,
        all_symbols: list[str],
        corr_engine: CorrelationEngine,
    ) -> float:
        """Штраф за высокую корреляцию с другими активами."""
        correlations = []
        for other in all_symbols:
            if other != symbol:
                c = abs(corr_engine.get_correlation(symbol, other))
                correlations.append(c)
        return max(correlations) if correlations else 0.0


# ── Exposure Controller ────────────────────────────────────

class ExposureController:
    """Контроль экспозиции: total, per-asset, per-regime."""

    MAX_TOTAL_EXPOSURE = 0.80        # max 80% capital deployed
    MAX_PER_ASSET = 0.40             # max 40% in one asset
    MAX_PER_REGIME = 0.60            # max 60% in same regime
    MAX_CORRELATED = 0.50            # max 50% in correlated assets

    def check(
        self,
        weights: dict[str, float],
        snapshots: dict[str, AssetSnapshot],
        correlation_engine: CorrelationEngine,
    ) -> dict:
        """Проверить экспозицию. Возвращает violations."""
        violations = []

        # Total exposure
        total = sum(weights.values())
        if total > self.MAX_TOTAL_EXPOSURE:
            violations.append({
                "type": "TOTAL_EXPOSURE",
                "current": round(total, 3),
                "limit": self.MAX_TOTAL_EXPOSURE,
                "severity": "HIGH",
            })

        # Per-asset
        for sym, w in weights.items():
            if w > self.MAX_PER_ASSET:
                violations.append({
                    "type": "PER_ASSET",
                    "symbol": sym,
                    "current": round(w, 3),
                    "limit": self.MAX_PER_ASSET,
                    "severity": "MEDIUM",
                })

        # Per-regime
        regime_exposure = {}
        for sym, w in weights.items():
            regime = snapshots.get(sym, AssetSnapshot(sym, 0, "UNKNOWN", 0, 0, 0, 0, 0, 0, "IDLE", "NORMAL")).regime
            regime_exposure[regime] = regime_exposure.get(regime, 0) + w

        for regime, exp in regime_exposure.items():
            if exp > self.MAX_PER_REGIME:
                violations.append({
                    "type": "PER_REGIME",
                    "regime": regime,
                    "current": round(exp, 3),
                    "limit": self.MAX_PER_REGIME,
                    "severity": "MEDIUM",
                })

        # Correlated exposure
        symbols = list(weights.keys())
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                corr = abs(correlation_engine.get_correlation(symbols[i], symbols[j]))
                combined = weights[symbols[i]] + weights[symbols[j]]
                if corr > 0.7 and combined > self.MAX_CORRELATED:
                    violations.append({
                        "type": "CORRELATED_EXPOSURE",
                        "symbols": [symbols[i], symbols[j]],
                        "correlation": round(corr, 3),
                        "combined_weight": round(combined, 3),
                        "limit": self.MAX_CORRELATED,
                        "severity": "HIGH",
                    })

        return {
            "total_exposure": round(total, 3),
            "violations": violations,
            "healthy": len(violations) == 0,
        }


# ── Portfolio Governor ─────────────────────────────────────

class PortfolioGovernor:
    """Portfolio-level risk governor (расширяет single-asset Governor)."""

    MAX_DRAWDOWN_PCT = 15.0
    MAX_DAILY_LOSS_PCT = 5.0
    MAX_LEVERAGE = 2.0
    MAX_CORRELATION_BETWEEN_POSITIONS = 0.8

    def evaluate(
        self,
        weights: dict[str, float],
        snapshots: dict[str, AssetSnapshot],
        correlation_engine: CorrelationEngine,
        total_equity: float = 10000.0,
        current_drawdown_pct: float = 0.0,
        daily_pnl: float = 0.0,
    ) -> dict:
        """Оценить portfolio-level risk."""
        alerts = []

        # Drawdown
        if current_drawdown_pct > self.MAX_DRAWDOWN_PCT:
            alerts.append({"level": "RED", "rule": "MAX_DRAWDOWN", "value": current_drawdown_pct})

        # Daily loss
        if total_equity > 0:
            daily_loss_pct = abs(daily_pnl / total_equity * 100) if daily_pnl < 0 else 0
            if daily_loss_pct > self.MAX_DAILY_LOSS_PCT:
                alerts.append({"level": "RED", "rule": "DAILY_LOSS", "value": daily_loss_pct})

        # High correlation between positions
        symbols = list(weights.keys())
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                corr = abs(correlation_engine.get_correlation(symbols[i], symbols[j]))
                if corr > self.MAX_CORRELATION_BETWEEN_POSITIONS:
                    alerts.append({
                        "level": "ORANGE",
                        "rule": "HIGH_CORRELATION",
                        "symbols": [symbols[i], symbols[j]],
                        "correlation": round(corr, 3),
                    })

        # Too many positions in same regime
        regimes = {}
        for sym, w in weights.items():
            snap = snapshots.get(sym)
            if snap:
                regimes.setdefault(snap.regime, []).append(sym)
        for regime, syms in regimes.items():
            if len(syms) > 2:
                alerts.append({
                    "level": "YELLOW",
                    "rule": "REGIME_CONCENTRATION",
                    "regime": regime,
                    "count": len(syms),
                })

        # Determine overall level
        levels = [a["level"] for a in alerts]
        if "RED" in levels:
            level = "RED"
        elif "ORANGE" in levels:
            level = "ORANGE"
        elif "YELLOW" in levels:
            level = "YELLOW"
        else:
            level = "GREEN"

        return {
            "level": level,
            "alerts": alerts,
            "total_exposure": round(sum(weights.values()), 3),
            "asset_count": len(weights),
            "regime_distribution": {r: len(s) for r, s in regimes.items()},
        }


# ── Portfolio Engine (ядро) ────────────────────────────────

class PortfolioEngine:
    """Координирует multi-market capital allocation."""

    def __init__(self):
        self._corr = CorrelationEngine()
        self._allocator = CapitalAllocator()
        self._exposure = ExposureController()
        self._governor = PortfolioGovernor()

    def process(
        self,
        snapshots: dict[str, AssetSnapshot],
        total_equity: float = 10000.0,
        current_drawdown_pct: float = 0.0,
        daily_pnl: float = 0.0,
    ) -> dict:
        """Полный цикл portfolio allocation."""
        # 1. Update correlations
        for sym, snap in snapshots.items():
            self._corr.update(sym, snap.price)

        # 2. Allocate capital
        weights = self._allocator.allocate(
            snapshots, self._corr, risk_mode="NORMAL",
        )

        # 3. Check exposure
        exposure_check = self._exposure.check(weights, snapshots, self._corr)

        # 4. Portfolio governor
        gov = self._governor.evaluate(
            weights, snapshots, self._corr,
            total_equity, current_drawdown_pct, daily_pnl,
        )

        # 5. Build result
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "weights": weights,
            "exposure": exposure_check,
            "governor": gov,
            "correlation_avg": self._corr.get_avg_correlation(list(snapshots.keys())),
            "diversification_score": self._corr.get_diversification_score(list(snapshots.keys())),
        }

        return result

    def get_correlation_matrix(self, symbols: list[str]) -> dict:
        return self._corr.get_correlation_matrix(symbols)

    def get_diversification_score(self, symbols: list[str]) -> float:
        return self._corr.get_diversification_score(symbols)
