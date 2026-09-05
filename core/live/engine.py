"""Live Execution Layer — безопасное подключение к реальному рынку.

Стадии:
  1. PAPER      — симуляция (текущий режим)
  2. PAPER_LIVE — live данные, виртуальные ордера
  3. MICRO_LIVE — минимальный реальный капитал
  4. FULL_LIVE  — полный размер

Каждая стадия требует прохождения gate checks перед переходом.
"""
import json
import time
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from enum import Enum


# ── Deployment Stage ───────────────────────────────────────

class DeploymentStage(str, Enum):
    PAPER = "PAPER"
    PAPER_LIVE = "PAPER_LIVE"
    MICRO_LIVE = "MICRO_LIVE"
    FULL_LIVE = "FULL_LIVE"


# All deployment stages (not an enum member to avoid iteration issues)
ALL_DEPLOYMENT_STAGES = [DeploymentStage.PAPER, DeploymentStage.PAPER_LIVE,
                         DeploymentStage.MICRO_LIVE, DeploymentStage.FULL_LIVE]


# ── Stage Requirements ────────────────────────────────────

@dataclass
class StageRequirement:
    """Требование для перехода на следующую стадию."""
    name: str
    description: str
    check_fn: str = ""       # имя метода
    critical: bool = True    # если True — блокирует переход


class StageGates:
    """Gate checks для каждой стадии."""

    REQUIREMENTS = {
        DeploymentStage.PAPER_LIVE: [
            StageRequirement(
                name="system_stability",
                description="Система работает без crash ≥ 24 часа",
            ),
            StageRequirement(
                name="t6_reconciliation",
                description="T6 Reconciler показывает 0 phantom/ghost за 24ч",
            ),
            StageRequirement(
                name="governor_green",
                description="Governor в GREEN ≥ 24 часа",
            ),
            StageRequirement(
                name="learning_frozen_check",
                description="T7 learning не в frozen state",
            ),
        ],
        DeploymentStage.MICRO_LIVE: [
            StageRequirement(
                name="paper_live_stability",
                description="PAPER_LIVE работает ≥ 7 дней без drift",
            ),
            StageRequirement(
                name="slippage_model",
                description="Slippage model calibrated на реальных данных",
            ),
            StageRequirement(
                name="max_drawdown_ok",
                description="Max drawdown за PAPER_LIVE < 5%",
            ),
            StageRequirement(
                name="win_rate_threshold",
                description="Win rate ≥ 45% за PAPER_LIVE период",
            ),
        ],
        DeploymentStage.FULL_LIVE: [
            StageRequirement(
                name="micro_stability",
                description="MICRO_LIVE работает ≥ 14 дней стабильно",
            ),
            StageRequirement(
                name="risk_metrics_ok",
                description="Sharpe > 1.0, MaxDD < 10%, Calmar > 1.0",
            ),
            StageRequirement(
                name="execution_quality",
                description="Fill rate > 95%, avg slippage < 5 bps",
            ),
        ],
    }


# ── Order Intent ───────────────────────────────────────────

@dataclass
class OrderIntent:
    """Намерение исполнить ордер."""
    intent_id: str = ""
    symbol: str = ""
    side: str = ""          # BUY / SELL
    quantity: float = 0.0
    order_type: str = ""    # MARKET / LIMIT
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    time_in_force: str = "GTC"
    strategy_mode: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Order Result ───────────────────────────────────────────

@dataclass
class OrderResult:
    """Результат исполнения ордера."""
    intent_id: str = ""
    broker_order_id: str = ""
    status: str = ""        # FILLED / PARTIAL / REJECTED / PENDING
    fill_price: float = 0.0
    fill_quantity: float = 0.0
    slippage_bps: float = 0.0
    commission: float = 0.0
    latency_ms: float = 0.0
    reject_reason: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Staged Executor ────────────────────────────────────────

class StagedExecutor:
    """Исполнение ордеров с учётом стадии deployment."""

    def __init__(self, stage: DeploymentStage = DeploymentStage.PAPER):
        self._stage = stage
        self._position_limit = {
            DeploymentStage.PAPER: 10,
            DeploymentStage.PAPER_LIVE: 5,
            DeploymentStage.MICRO_LIVE: 3,
            DeploymentStage.FULL_LIVE: 3,
        }
        self._max_order_value = {
            DeploymentStage.PAPER: 100000,
            DeploymentStage.PAPER_LIVE: 100000,
            DeploymentStage.MICRO_LIVE: 100,     # $100 max
            DeploymentStage.FULL_LIVE: 10000,
        }
        self._open_positions = 0
        self._execution_log: list[dict] = []

    def can_execute(self, intent: OrderIntent) -> tuple[bool, str]:
        """Проверить можно ли исполнить ордер."""
        # Position limit
        if self._open_positions >= self._position_limit[self._stage]:
            return False, f"max_positions ({self._position_limit[self._stage]})"

        # Order value limit
        order_value = intent.quantity * intent.limit_price if intent.limit_price else intent.quantity * 60000
        if order_value > self._max_order_value[self._stage]:
            return False, f"order_value {order_value:.0f} > max {self._max_order_value[self._stage]}"

        return True, "ok"

    def execute(self, intent: OrderIntent) -> OrderResult:
        """Исполнить ордер (в зависимости от стадии)."""
        can, reason = self.can_execute(intent)
        if not can:
            return OrderResult(
                intent_id=intent.intent_id,
                status="REJECTED",
                reject_reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        start = time.time()

        if self._stage == DeploymentStage.PAPER:
            result = self._execute_paper(intent)
        elif self._stage == DeploymentStage.PAPER_LIVE:
            result = self._execute_paper_live(intent)
        elif self._stage == DeploymentStage.MICRO_LIVE:
            result = self._execute_micro_live(intent)
        else:
            result = self._execute_full_live(intent)

        result.latency_ms = (time.time() - start) * 1000
        result.timestamp = datetime.now(timezone.utc).isoformat()

        self._execution_log.append(result.to_dict())
        return result

    def _execute_paper(self, intent: OrderIntent) -> OrderResult:
        """Полная симуляция."""
        # Simulate fill with random slippage
        import random
        slippage = random.uniform(0, 3)  # 0-3 bps
        fill_price = intent.limit_price or 62750
        if intent.side == "BUY":
            fill_price *= (1 + slippage / 10000)
        else:
            fill_price *= (1 - slippage / 10000)

        self._open_positions += 1
        return OrderResult(
            intent_id=intent.intent_id,
            broker_order_id=f"PAPER-{int(time.time())}",
            status="FILLED",
            fill_price=round(fill_price, 2),
            fill_quantity=intent.quantity,
            slippage_bps=round(slippage, 2),
        )

    def _execute_paper_live(self, intent: OrderIntent) -> OrderResult:
        """Live данные, виртуальные ордера."""
        return self._execute_paper(intent)  # same as paper for now

    def _execute_micro_live(self, intent: OrderIntent) -> OrderResult:
        """Реальный ордер с минимальным размером."""
        # TODO: actual Bybit API call
        return self._execute_paper(intent)  # placeholder

    def _execute_full_live(self, intent: OrderIntent) -> OrderResult:
        """Полный реальный ордер."""
        # TODO: actual Bybit API call
        return self._execute_paper(intent)  # placeholder

    def close_position(self):
        self._open_positions = max(0, self._open_positions - 1)

    def get_status(self) -> dict:
        return {
            "stage": self._stage,
            "open_positions": self._open_positions,
            "position_limit": self._position_limit[self._stage],
            "max_order_value": self._max_order_value[self._stage],
            "total_executions": len(self._execution_log),
        }


# ── Failure Mode Map ───────────────────────────────────────

class FailureModeMap:
    """Карта отказов и митигаций для live execution."""

    MODES = [
        {
            "mode": "EXCHANGE_UNAVAILABLE",
            "description": "Биржа не отвечает",
            "detection": "timeout > 10s or connection error",
            "mitigation": "circuit_breaker OPEN → pause trading → alert",
            "severity": "CRITICAL",
        },
        {
            "mode": "ORDER_REJECTED",
            "description": "Ордер отклонён биржей",
            "detection": "order status REJECTED",
            "mitigation": "log → adjust intent → retry with different params",
            "severity": "HIGH",
        },
        {
            "mode": "PARTIAL_FILL",
            "description": "Частичное исполнение",
            "detection": "fill_qty < intent_qty",
            "mitigation": "log → accept partial → adjust position state",
            "severity": "MEDIUM",
        },
        {
            "mode": "SLIPPAGE_EXCEEDED",
            "description": "Проскальзывание выше порога",
            "detection": "slippage_bps > max_slippage_bps",
            "mitigation": "log → reduce order size → alert",
            "severity": "MEDIUM",
        },
        {
            "mode": "LATENCY_SPIKE",
            "description": "Резкий рост задержки",
            "detection": "latency_ms > 5000",
            "mitigation": "circuit_breaker HALF_OPEN → reduce frequency",
            "severity": "HIGH",
        },
        {
            "mode": "POSITION_DRIFT",
            "description": "Расхождение internal vs broker state",
            "detection": "T6 PhantomDetector finds anomaly",
            "mitigation": "reconcile → force sync → alert if unresolved",
            "severity": "CRITICAL",
        },
        {
            "mode": "SL_TP_MISSING",
            "description": "SL/TP не установлены на позиции",
            "detection": "T6 SLTPGuardian finds SL_MISSING",
            "mitigation": "force_repair → set SL/TP → or close position",
            "severity": "CRITICAL",
        },
        {
            "mode": "DRAWDOWN_BREACH",
            "description": "Превышен максимальный drawdown",
            "detection": "Governor level = RED",
            "mitigation": "flatten_all → stop_trading → alert → human review",
            "severity": "CRITICAL",
        },
        {
            "mode": "CORRELATION_COLLAPSE",
            "description": "Все активы в одном режиме",
            "detection": "Portfolio Governor YELLOW/RED",
            "mitigation": "reduce_exposure → diversify → wait for regime shift",
            "severity": "HIGH",
        },
        {
            "mode": "LEARNING_DRIFT",
            "description": "T7 weights деградируют",
            "detection": "Stability rollback triggered",
            "mitigation": "rollback → freeze learning → audit → human review",
            "severity": "HIGH",
        },
    ]

    @classmethod
    def get_critical_modes(cls) -> list[dict]:
        return [m for m in cls.MODES if m["severity"] == "CRITICAL"]

    @classmethod
    def get_all(cls) -> list[dict]:
        return cls.MODES

    @classmethod
    def summary(cls) -> str:
        lines = []
        for m in cls.MODES:
            lines.append(f"  [{m['severity']:>8}] {m['mode']}: {m['description']}")
        return "\n".join(lines)


# ── Deployment Tracker ─────────────────────────────────────

class DeploymentTracker:
    """Отслеживает стадию deployment и метрики."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or Path(__file__).parent.parent.parent / "tradingos_deployment.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deployment_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT
            )
        """)
        conn.commit()
        conn.close()

    def log_event(self, stage: str, action: str, details: str = ""):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute(
            "INSERT INTO deployment_log (timestamp, stage, action, details) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), stage, action, details),
        )
        conn.commit()
        conn.close()

    def get_history(self, limit: int = 20) -> list[dict]:
        conn = sqlite3.connect(str(self._path), timeout=10)
        rows = conn.execute(
            "SELECT * FROM deployment_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        cols = ["id", "timestamp", "stage", "action", "details"]
        return [dict(zip(cols, row)) for row in rows]


# ── Live Execution Engine (ядро) ──────────────────────────

class LiveExecutionEngine:
    """Координирует live execution с safety gates."""

    def __init__(self):
        self._stage = DeploymentStage.PAPER
        self._executor = StagedExecutor(self._stage)
        self._tracker = DeploymentTracker()
        self._stage_gates = StageGates()

    def can_transition(self, target_stage: DeploymentStage) -> dict:
        """Проверить можно ли перейти на целевую стадию."""
        if target_stage == DeploymentStage.PAPER:
            return {"allowed": True, "reason": "always allowed"}

        requirements = self._stage_gates.REQUIREMENTS.get(target_stage, [])
        passed = []
        failed = []

        for req in requirements:
            # Execute actual check if check_fn is defined
            if req.check_fn and hasattr(self, req.check_fn):
                try:
                    check_method = getattr(self, req.check_fn)
                    result = check_method()
                    if result:
                        passed.append(req.name)
                    else:
                        if req.critical:
                            failed.append(req.name)
                except Exception as e:
                    if req.critical:
                        failed.append(f"{req.name}: {e}")
            else:
                # No check function defined — fail if critical
                if req.critical:
                    failed.append(f"{req.name}: check not implemented")

        return {
            "allowed": len(failed) == 0,
            "from_stage": self._stage,
            "to_stage": target_stage,
            "passed": passed,
            "failed": failed,
            "requirements": [{"name": r.name, "description": r.description} for r in requirements],
        }

    def transition(self, target_stage: DeploymentStage) -> dict:
        """Перейти на новую стадию."""
        check = self.can_transition(target_stage)
        if not check["allowed"]:
            return {"success": False, "reason": check["failed"]}

        old_stage = self._stage
        self._stage = target_stage
        self._executor = StagedExecutor(target_stage)
        self._tracker.log_event(
            target_stage.value,
            "stage_transition",
            f"from={old_stage.value} to={target_stage.value}",
        )

        return {
            "success": True,
            "old_stage": old_stage.value,
            "new_stage": target_stage.value,
        }

    def execute_order(self, intent: OrderIntent) -> OrderResult:
        """Исполнить ордер через staged executor."""
        result = self._executor.execute(intent)
        self._tracker.log_event(
            self._stage.value,
            "order_executed",
            json.dumps(result.to_dict()),
        )
        return result

    def get_status(self) -> dict:
        return {
            "stage": self._stage.value,
            "executor": self._executor.get_status(),
            "failure_modes": FailureModeMap.summary(),
            "history": self._tracker.get_history(5),
        }
