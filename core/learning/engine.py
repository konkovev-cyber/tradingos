"""T7 Learning Loop — non-ML adaptive learning system.

Не использует ML/RL/backprop. Не является black box.
Event-driven adaptive weight system: наблюдает outcomes → корректирует weights.

Входы:
  - PnL outcomes (profit/loss per trade)
  - Drift events (T6 execution truth)
  - Regime context (T2/T3)
  - Signal quality (T4)
  - Policy success/failure (T5)

Выходы:
  - Feature weight adjustments (T3)
  - Signal recalibration (T4)
  - Policy adaptation (T5)
  - Regime-specific memory
"""
import json
import time
import sqlite3
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
from collections import deque


# ── Outcome Record ─────────────────────────────────────────

@dataclass
class OutcomeRecord:
    """Один результат сделки для анализа."""
    trade_id: str
    timestamp: str
    symbol: str
    regime: str
    strategy_mode: str
    direction: str                  # LONG / SHORT
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    duration_seconds: int
    slippage_bps: float = 0.0
    drift_score: float = 0.0
    signal_confidence: float = 0.0
    feature_snapshot: dict = field(default_factory=dict)
    signal_snapshot: dict = field(default_factory=dict)
    policy_snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


# ── Feature Weights ────────────────────────────────────────

@dataclass
class FeatureWeights:
    """Веса фичей для сигналов."""
    regime_strength: float = 0.20
    price_last: float = 0.15
    atr_smoothed: float = 0.15
    adx_trend_strength: float = 0.15
    volume_notional: float = 0.10
    cycle_speed: float = 0.10
    event_density_1h: float = 0.08
    trend_consistency: float = 0.07

    # Signal weights
    trend_weight: float = 0.30
    reversal_weight: float = 0.25
    momentum_weight: float = 0.25
    volatility_weight: float = 0.10
    trust_weight: float = 0.10

    # Policy weights
    trend_mode_threshold: float = 0.6
    scalp_mode_threshold: float = 0.5
    risk_threshold: float = 0.5

    # Metadata
    version: int = 1
    last_updated: str = ""
    total_adjustments: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def normalize(self):
        """Normalize feature weights to sum to 1.0."""
        total = (self.regime_strength + self.price_last + self.atr_smoothed +
                 self.adx_trend_strength + self.volume_notional +
                 self.cycle_speed + self.event_density_1h + self.trend_consistency)
        if total > 0:
            self.regime_strength /= total
            self.price_last /= total
            self.atr_smoothed /= total
            self.adx_trend_strength /= total
            self.volume_notional /= total
            self.cycle_speed /= total
            self.event_density_1h /= total
            self.trend_consistency /= total


# ── Regime Memory ──────────────────────────────────────────

@dataclass
class RegimeMemory:
    """Память о том, как система работала в каждом режиме."""
    regime: str
    total_trades: int = 0
    win_count: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0
    avg_duration: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    avg_drift: float = 0.0
    avg_confidence: float = 0.0
    last_updated: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Outcome Tracker ────────────────────────────────────────

class OutcomeTracker:
    """Отслеживает результаты сделок."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or Path(__file__).parent.parent.parent / "tradingos_learning.db"
        self._init_db()
        self._recent: deque = deque(maxlen=100)

    def _init_db(self):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                trade_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                regime TEXT NOT NULL,
                strategy_mode TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                duration_seconds INTEGER NOT NULL,
                slippage_bps REAL DEFAULT 0,
                drift_score REAL DEFAULT 0,
                signal_confidence REAL DEFAULT 0,
                feature_json TEXT,
                signal_json TEXT,
                policy_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_out_regime ON outcomes(regime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_out_mode ON outcomes(strategy_mode)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_out_ts ON outcomes(timestamp)")
        conn.commit()
        conn.close()

    def record(self, outcome: OutcomeRecord):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("""
            INSERT OR REPLACE INTO outcomes
            (trade_id, timestamp, symbol, regime, strategy_mode, direction,
             entry_price, exit_price, pnl, pnl_pct, duration_seconds,
             slippage_bps, drift_score, signal_confidence,
             feature_json, signal_json, policy_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            outcome.trade_id, outcome.timestamp, outcome.symbol,
            outcome.regime, outcome.strategy_mode, outcome.direction,
            outcome.entry_price, outcome.exit_price, outcome.pnl,
            outcome.pnl_pct, outcome.duration_seconds,
            outcome.slippage_bps, outcome.drift_score, outcome.signal_confidence,
            json.dumps(outcome.feature_snapshot),
            json.dumps(outcome.signal_snapshot),
            json.dumps(outcome.policy_snapshot),
        ))
        conn.commit()
        conn.close()
        self._recent.append(outcome)

    def get_by_regime(self, regime: str, limit: int = 100) -> list[OutcomeRecord]:
        conn = sqlite3.connect(str(self._path), timeout=10)
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE regime = ? ORDER BY timestamp DESC LIMIT ?",
            (regime, limit),
        ).fetchall()
        conn.close()
        return [self._row_to_record(r) for r in rows]

    def get_by_mode(self, mode: str, limit: int = 100) -> list[OutcomeRecord]:
        conn = sqlite3.connect(str(self._path), timeout=10)
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE strategy_mode = ? ORDER BY timestamp DESC LIMIT ?",
            (mode, limit),
        ).fetchall()
        conn.close()
        return [self._row_to_record(r) for r in rows]

    def get_recent(self, limit: int = 50) -> list[OutcomeRecord]:
        conn = sqlite3.connect(str(self._path), timeout=10)
        rows = conn.execute(
            "SELECT * FROM outcomes ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row) -> OutcomeRecord:
        cols = ["trade_id", "timestamp", "symbol", "regime", "strategy_mode",
                "direction", "entry_price", "exit_price", "pnl", "pnl_pct",
                "duration_seconds", "slippage_bps", "drift_score", "signal_confidence",
                "feature_json", "signal_json", "policy_json"]
        d = dict(zip(cols, row))
        return OutcomeRecord(
            trade_id=d["trade_id"], timestamp=d["timestamp"], symbol=d["symbol"],
            regime=d["regime"], strategy_mode=d["strategy_mode"], direction=d["direction"],
            entry_price=d["entry_price"], exit_price=d["exit_price"],
            pnl=d["pnl"], pnl_pct=d["pnl_pct"], duration_seconds=d["duration_seconds"],
            slippage_bps=d["slippage_bps"], drift_score=d["drift_score"],
            signal_confidence=d["signal_confidence"],
            feature_snapshot=json.loads(d["feature_json"] or "{}"),
            signal_snapshot=json.loads(d["signal_json"] or "{}"),
            policy_snapshot=json.loads(d["policy_json"] or "{}"),
        )


# ── Drift Analyzer ─────────────────────────────────────────

class DriftAnalyzer:
    """Анализирует drift из T6 и определяет паттерны."""

    def __init__(self):
        self._drift_history: deque = deque(maxlen=1000)

    def record_drift(self, drift_score: float, drift_type: str, context: dict):
        self._drift_history.append({
            "score": drift_score,
            "type": drift_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **context,
        })

    def get_drift_trend(self, window: int = 20) -> dict:
        """Тренд drift за последние N событий."""
        if len(self._drift_history) < 2:
            return {"trend": "stable", "avg": 0, "slope": 0}

        recent = list(self._drift_history)[-window:]
        scores = [d["score"] for d in recent]
        avg = statistics.mean(scores)
        slope = (scores[-1] - scores[0]) / len(scores) if len(scores) > 1 else 0

        if slope > 0.01:
            trend = "worsening"
        elif slope < -0.01:
            trend = "improving"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "avg": round(avg, 3),
            "slope": round(slope, 4),
            "samples": len(recent),
        }

    def get_drift_by_type(self) -> dict:
        by_type = {}
        for d in self._drift_history:
            t = d["type"]
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(d["score"])
        return {t: round(statistics.mean(scores), 3) for t, scores in by_type.items()}


# ── Weight Adjuster ────────────────────────────────────────

class WeightAdjuster:
    """Корректирует веса на основе outcomes."""

    def __init__(self):
        self._adjustment_rate = 0.05    # max change per cycle
        self._min_weight = 0.02
        self._max_weight = 0.50

    def adjust_feature_weights(
        self,
        weights: FeatureWeights,
        outcomes: list[OutcomeRecord],
    ) -> FeatureWeights:
        """Корректировка feature weights на основе outcomes."""
        if len(outcomes) < 5:
            return weights

        # Compute feature importance correlation with PnL
        # Features that correlate with wins → increase weight
        # Features that correlate with losses → decrease weight

        wins = [o for o in outcomes if o.is_win]
        losses = [o for o in outcomes if not o.is_win]

        if not wins or not losses:
            return weights

        # Simple heuristic: compare average feature values in wins vs losses
        # This is not ML — it's statistical observation
        for feat_name in ["regime_strength", "adx_trend_strength", "volume_notional",
                          "trend_consistency", "atr_smoothed", "cycle_speed",
                          "event_density_1h", "price_last"]:
            win_vals = [o.feature_snapshot.get(feat_name, {}).get("value", 0) for o in wins]
            loss_vals = [o.feature_snapshot.get(feat_name, {}).get("value", 0) for o in losses]

            if win_vals and loss_vals:
                win_avg = statistics.mean(win_vals)
                loss_avg = statistics.mean(loss_vals)

                # If wins have higher feature value → feature is predictive
                if win_avg > loss_avg and win_avg > 0:
                    ratio = min(win_avg / loss_avg, 2.0) if loss_avg > 0 else 1.5
                    adjustment = self._adjustment_rate * (ratio - 1.0)
                elif loss_avg > win_avg and loss_avg > 0:
                    ratio = min(loss_avg / win_avg, 2.0) if win_avg > 0 else 1.5
                    adjustment = -self._adjustment_rate * (ratio - 1.0)
                else:
                    adjustment = 0

                # Apply
                current = getattr(weights, feat_name, 0)
                new_val = current + adjustment
                new_val = max(self._min_weight, min(self._max_weight, new_val))
                setattr(weights, feat_name, new_val)

        weights.normalize()
        weights.version += 1
        weights.total_adjustments += 1
        weights.last_updated = datetime.now(timezone.utc).isoformat()
        return weights

    def adjust_signal_weights(
        self,
        weights: FeatureWeights,
        outcomes: list[OutcomeRecord],
    ) -> FeatureWeights:
        """Корректировка signal weights."""
        if len(outcomes) < 5:
            return weights

        # Check which signal components predict wins
        for sig_name in ["trend", "reversal", "momentum", "volatility_pressure", "market_trust"]:
            weight_attr = f"{sig_name}_weight"
            if not hasattr(weights, weight_attr):
                continue

            win_vals = [o.signal_snapshot.get(sig_name, 0) for o in outcomes if o.is_win]
            loss_vals = [o.signal_snapshot.get(sig_name, 0) for o in outcomes if not o.is_win]

            if win_vals and loss_vals:
                win_avg = statistics.mean(win_vals)
                loss_avg = statistics.mean(loss_vals)

                if win_avg > loss_avg:
                    adjustment = self._adjustment_rate * 0.5
                elif loss_avg > win_avg:
                    adjustment = -self._adjustment_rate * 0.5
                else:
                    adjustment = 0

                current = getattr(weights, weight_attr)
                new_val = current + adjustment
                new_val = max(0.05, min(0.50, new_val))
                setattr(weights, weight_attr, new_val)

        return weights


# ── Regime Memory Manager ──────────────────────────────────

class RegimeMemoryManager:
    """Управляет памятью о режимах."""

    def __init__(self):
        self._memories: dict[str, RegimeMemory] = {}

    def update(self, outcome: OutcomeRecord):
        regime = outcome.regime
        if regime not in self._memories:
            self._memories[regime] = RegimeMemory(regime=regime)

        m = self._memories[regime]
        m.total_trades += 1
        if outcome.is_win:
            m.win_count += 1
        m.total_pnl += outcome.pnl
        m.avg_pnl = m.total_pnl / m.total_trades
        m.win_rate = m.win_count / m.total_trades
        m.avg_duration = (m.avg_duration * (m.total_trades - 1) + outcome.duration_seconds) / m.total_trades
        m.best_trade_pnl = max(m.best_trade_pnl, outcome.pnl)
        m.worst_trade_pnl = min(m.worst_trade_pnl, outcome.pnl)
        m.avg_drift = (m.avg_drift * (m.total_trades - 1) + outcome.drift_score) / m.total_trades
        m.avg_confidence = (m.avg_confidence * (m.total_trades - 1) + outcome.signal_confidence) / m.total_trades
        m.last_updated = datetime.now(timezone.utc).isoformat()

    def get_memory(self, regime: str) -> Optional[RegimeMemory]:
        return self._memories.get(regime)

    def get_all(self) -> dict[str, dict]:
        return {k: v.to_dict() for k, v in self._memories.items()}

    def get_best_regime(self) -> Optional[str]:
        if not self._memories:
            return None
        return max(self._memories, key=lambda r: self._memories[r].avg_pnl)

    def get_worst_regime(self) -> Optional[str]:
        if not self._memories:
            return None
        return min(self._memories, key=lambda r: self._memories[r].avg_pnl)


# ── Learning Engine (ядро T7) ──────────────────────────────

class LearningEngine:
    """Основной engine T7: outcomes → weight adjustments."""

    def __init__(self, db_path: Optional[Path] = None):
        self._tracker = OutcomeTracker(db_path)
        self._drift_analyzer = DriftAnalyzer()
        self._weight_adjuster = WeightAdjuster()
        self._regime_memory = RegimeMemoryManager()
        self._weights = FeatureWeights()
        self._learning_enabled = True
        self._min_outcomes_for_learning = 10

    def record_outcome(self, outcome: OutcomeRecord):
        """Записать результат сделки."""
        self._tracker.record(outcome)
        self._regime_memory.update(outcome)
        if outcome.drift_score > 0:
            self._drift_analyzer.record_drift(
                outcome.drift_score, "execution_drift",
                {"trade_id": outcome.trade_id, "regime": outcome.regime},
            )

    def learn(self) -> dict:
        """Один цикл обучения: анализ outcomes → корректировка weights."""
        if not self._learning_enabled:
            return {"status": "disabled"}

        recent = self._tracker.get_recent(limit=100)
        if len(recent) < self._min_outcomes_for_learning:
            return {
                "status": "insufficient_data",
                "outcomes": len(recent),
                "required": self._min_outcomes_for_learning,
            }

        # Adjust weights
        old_version = self._weights.version
        self._weights = self._weight_adjuster.adjust_feature_weights(self._weights, recent)
        self._weights = self._weight_adjuster.adjust_signal_weights(self._weights, recent)

        # Get drift trend
        drift_trend = self._drift_analyzer.get_drift_trend()

        # Get regime memory
        regime_summary = self._regime_memory.get_all()

        return {
            "status": "learned",
            "outcomes_analyzed": len(recent),
            "old_version": old_version,
            "new_version": self._weights.version,
            "adjustments": self._weights.total_adjustments,
            "drift_trend": drift_trend,
            "regime_memory": regime_summary,
            "best_regime": self._regime_memory.get_best_regime(),
            "worst_regime": self._regime_memory.get_worst_regime(),
            "weights": self._weights.to_dict(),
        }

    def get_weights(self) -> FeatureWeights:
        return self._weights

    def set_weights(self, weights: FeatureWeights):
        self._weights = weights

    def get_stats(self) -> dict:
        recent = self._tracker.get_recent(limit=100)
        wins = sum(1 for o in recent if o.is_win)
        total_pnl = sum(o.pnl for o in recent)
        return {
            "total_outcomes": len(recent),
            "win_rate": round(wins / len(recent), 3) if recent else 0,
            "total_pnl": round(total_pnl, 4),
            "avg_pnl": round(total_pnl / len(recent), 6) if recent else 0,
            "regime_memory": self._regime_memory.get_all(),
            "drift_trend": self._drift_analyzer.get_drift_trend(),
            "weights_version": self._weights.version,
            "total_adjustments": self._weights.total_adjustments,
        }
