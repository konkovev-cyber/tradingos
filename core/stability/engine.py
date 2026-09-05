"""Stability Layer — production-grade hardening for T7 Learning.

Контролирует:
1. Learning freeze (шумные режимы → заморозка обучения)
2. Weight rollback (если weights ухудшают метрики)
3. Noise filter (не реагировать на выбросы)
4. Audit trail (каждое изменение весов фиксируется)
5. Deterministic replay mode
"""
import json
import time
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
from collections import deque


# ── Learning Freeze Rules ──────────────────────────────────

class FreezeReason:
    INSUFFICIENT_DATA = "insufficient_data"
    NOISE_REGIME = "noise_regime"
    CONSECUTIVE_NO_IMPROVEMENT = "no_improvement"
    HIGH_DRIFT = "high_drift"
    GOVERNOR_ORANGE_OR_RED = "governor_restricted"


@dataclass
class FreezeState:
    """Состояние заморозки обучения."""
    is_frozen: bool = False
    reason: str = ""
    frozen_at: str = ""
    unfreeze_at: str = ""
    freeze_count: int = 0
    total_frozen_seconds: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Weight Snapshot (for rollback) ─────────────────────────

@dataclass
class WeightSnapshot:
    """Снимок весов для отката."""
    snapshot_id: str
    timestamp: str
    weights: dict
    metrics: dict           #当时的 метрики (win_rate, pnl, etc.)
    version: int
    performance_score: float  # обобщённая оценка качества

    def to_dict(self) -> dict:
        return asdict(self)


# ── Audit Entry ────────────────────────────────────────────

@dataclass
class AuditEntry:
    """Запись в audit trail."""
    timestamp: str
    action: str             # "weight_adjustment", "freeze", "unfreeze", "rollback"
    old_value: dict = field(default_factory=dict)
    new_value: dict = field(default_factory=dict)
    reason: str = ""
    triggered_by: str = ""  # "learning_cycle", "freeze_rule", "rollback_rule"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Learning Freeze Engine ─────────────────────────────────

class LearningFreezeEngine:
    """Определяет когда нужно заморозить обучение."""

    MIN_SAMPLES_FOR_LEARNING = 10
    MIN_WIN_RATE_CHANGE = 0.02     # min change to justify learning
    MAX_DRIFT_FOR_LEARNING = 0.5
    NOISE_REGIMES = ["CHAOS", "CRASH", "RECOVERY"]

    def should_freeze(
        self,
        outcomes_count: int,
        recent_win_rate: float,
        previous_win_rate: float,
        drift_trend: str,
        governor_level: str,
        current_regime: str,
    ) -> tuple[bool, str]:
        """Определяет нужно ли заморозить. Возвращает (freeze, reason)."""

        # 1. Insufficient data
        if outcomes_count < self.MIN_SAMPLES_FOR_LEARNING:
            return True, FreezeReason.INSUFFICIENT_DATA

        # 2. Noise regime
        if current_regime in self.NOISE_REGIMES:
            return True, FreezeReason.NOISE_REGIME

        # 3. No improvement
        if outcomes_count >= self.MIN_SAMPLES_FOR_LEARNING:
            change = abs(recent_win_rate - previous_win_rate)
            if change < self.MIN_WIN_RATE_CHANGE:
                return True, FreezeReason.CONSECUTIVE_NO_IMPROVEMENT

        # 4. High drift
        if drift_trend == "worsening":
            return True, FreezeReason.HIGH_DRIFT

        # 5. Governor restricted
        if governor_level in ("ORANGE", "RED"):
            return True, FreezeReason.GOVERNOR_ORANGE_OR_RED

        return False, ""


# ── Weight Rollback Engine ─────────────────────────────────

class WeightRollbackEngine:
    """Откат весов если они ухудшают метрики."""

    ROLLBACK_THRESHOLD = 0.05   # max acceptable win_rate drop
    MAX_SNAPSHOTS = 50

    def __init__(self):
        self._snapshots: deque = deque(maxlen=self.MAX_SNAPSHOTS)

    def save_snapshot(
        self,
        weights: dict,
        metrics: dict,
        version: int,
    ) -> WeightSnapshot:
        """Сохранить снимок весов."""
        perf = self._compute_performance_score(metrics)
        snap = WeightSnapshot(
            snapshot_id=f"snap_{int(time.time())}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            weights=weights,
            metrics=metrics,
            version=version,
            performance_score=perf,
        )
        self._snapshots.append(snap)
        return snap

    def should_rollback(
        self,
        current_metrics: dict,
        previous_metrics: dict,
    ) -> bool:
        """Нужен ли откат."""
        curr_score = self._compute_performance_score(current_metrics)
        prev_score = self._compute_performance_score(previous_metrics)

        if prev_score > 0:
            change = (curr_score - prev_score) / prev_score
            return change < -self.ROLLBACK_THRESHOLD
        return False

    def get_rollback_target(self) -> Optional[WeightSnapshot]:
        """Получить лучший снимок для отката."""
        if not self._snapshots:
            return None
        return max(self._snapshots, key=lambda s: s.performance_score)

    def _compute_performance_score(self, metrics: dict) -> float:
        """Обобщённая оценка: win_rate × (1 + avg_pnl) × (1 - avg_drift)."""
        wr = metrics.get("win_rate", 0)
        pnl = metrics.get("avg_pnl", 0)
        drift = metrics.get("avg_drift", 0)
        return wr * (1 + pnl) * (1 - drift)


# ── Noise Filter ───────────────────────────────────────────

class NoiseFilter:
    """Фильтрация шумовых значений перед обучением."""

    def __init__(self, window: int = 10):
        self._window = window
        self._values: deque = deque(maxlen=window * 3)

    def is_outlier(self, value: float, metric: str = "pnl") -> bool:
        """Проверяет является ли значение выбросом."""
        self._values.append(value)
        if len(self._values) < self._window:
            return False

        values = list(self._values)
        mean = sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5

        if std == 0:
            return False

        z_score = abs(value - mean) / std
        return z_score > 3.0  # 3-sigma rule

    def filter_outcomes(self, outcomes: list) -> list:
        """Убрать outliers из outcomes перед анализом."""
        if len(outcomes) < 5:
            return outcomes

        pnls = [o.pnl for o in outcomes]
        mean_pnl = sum(pnls) / len(pnls)
        std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)) ** 0.5

        if std_pnl == 0:
            return outcomes

        filtered = []
        for o in outcomes:
            z = abs(o.pnl - mean_pnl) / std_pnl
            if z <= 3.0:
                filtered.append(o)

        return filtered if len(filtered) >= 3 else outcomes


# ── Audit Trail ────────────────────────────────────────────

class AuditTrail:
    """Журнал всех изменений в системе."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or Path(__file__).parent.parent.parent / "tradingos_audit.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                action      TEXT NOT NULL,
                old_json    TEXT,
                new_json    TEXT,
                reason      TEXT,
                triggered_by TEXT
            )
        """)
        conn.commit()
        conn.close()

    def record(self, entry: AuditEntry):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("""
            INSERT INTO audit_trail (timestamp, action, old_json, new_json, reason, triggered_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            entry.timestamp, entry.action,
            json.dumps(entry.old_value) if entry.old_value else None,
            json.dumps(entry.new_value) if entry.new_value else None,
            entry.reason, entry.triggered_by,
        ))
        conn.commit()
        conn.close()

    def get_recent(self, limit: int = 20) -> list[dict]:
        conn = sqlite3.connect(str(self._path), timeout=10)
        rows = conn.execute(
            "SELECT * FROM audit_trail ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        cols = ["id", "timestamp", "action", "old_json", "new_json", "reason", "triggered_by"]
        return [dict(zip(cols, row)) for row in rows]


# ── Stability Engine (ядро) ───────────────────────────────

class StabilityEngine:
    """Координирует все stability mechanisms."""

    def __init__(self):
        self._freeze = LearningFreezeEngine()
        self._rollback = WeightRollbackEngine()
        self._noise = NoiseFilter()
        self._audit = AuditTrail()
        self._freeze_state = FreezeState()
        self._prev_metrics: Optional[dict] = None

    def check_stability(
        self,
        outcomes_count: int,
        recent_win_rate: float,
        previous_win_rate: float,
        drift_trend: str,
        governor_level: str,
        current_regime: str,
    ) -> dict:
        """Проверить все stability conditions."""
        # Freeze check
        should_freeze, reason = self._freeze.should_freeze(
            outcomes_count, recent_win_rate, previous_win_rate,
            drift_trend, governor_level, current_regime,
        )

        if should_freeze and not self._freeze_state.is_frozen:
            self._freeze_state.is_frozen = True
            self._freeze_state.reason = reason
            self._freeze_state.frozen_at = datetime.now(timezone.utc).isoformat()
            self._freeze_state.freeze_count += 1
            self._audit.record(AuditEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action="freeze",
                reason=reason,
                triggered_by="stability_check",
            ))
        elif not should_freeze and self._freeze_state.is_frozen:
            self._freeze_state.is_frozen = False
            self._freeze_state.reason = ""
            self._audit.record(AuditEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action="unfreeze",
                triggered_by="stability_check",
            ))

        # Rollback check
        needs_rollback = False
        if self._prev_metrics and self._prev_metrics.get("win_rate", 0) > 0:
            needs_rollback = self._rollback.should_rollback(
                {"win_rate": recent_win_rate},
                self._prev_metrics,
            )

        return {
            "frozen": self._freeze_state.is_frozen,
            "freeze_reason": self._freeze_state.reason,
            "freeze_count": self._freeze_state.freeze_count,
            "needs_rollback": needs_rollback,
            "audit_entries": len(self._audit.get_recent(10)),
        }

    def record_weight_change(self, old_weights: dict, new_weights: dict, reason: str):
        """Зафиксировать изменение весов в audit trail."""
        self._audit.record(AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="weight_adjustment",
            old_value=old_weights,
            new_value=new_weights,
            reason=reason,
            triggered_by="learning_cycle",
        ))

    def save_weight_snapshot(self, weights: dict, metrics: dict, version: int):
        """Сохранить снимок весов для возможного отката."""
        self._rollback.save_snapshot(weights, metrics, version)

    def get_rollback_target(self) -> Optional[dict]:
        """Получить веса для отката."""
        snap = self._rollback.get_rollback_target()
        return snap.weights if snap else None

    def filter_outcomes(self, outcomes: list) -> list:
        """Отфильтровать outliers."""
        return self._noise.filter_outcomes(outcomes)

    def get_audit_trail(self, limit: int = 20) -> list[dict]:
        return self._audit.get_recent(limit)

    def update_prev_metrics(self, metrics: dict):
        self._prev_metrics = metrics

    def get_state(self) -> dict:
        return {
            "freeze": self._freeze_state.to_dict(),
            "rollback_snapshots": len(self._rollback._snapshots),
            "audit_entries": len(self._audit.get_recent(100)),
        }
