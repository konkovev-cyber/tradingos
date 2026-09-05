"""Risk Constitution Layer — глобальный governor безопасности.

Не торгует. Не принимает решений.
Только проверяет: можно ли системе торговать вообще.

Уровни:
  0. GREEN  — всё в порядке, торговля разрешена
  1. YELLOW — замедление, уменьшение размеров
  2. ORANGE — freeze новых ордеров, только закрытие
  3. RED    — полная остановка, flatten всех позиций
"""
import json
import time
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path


# ── Risk Levels ────────────────────────────────────────────

class RiskLevel:
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"
    ALL = [GREEN, YELLOW, ORANGE, RED]


# ── Governor Decision ──────────────────────────────────────

@dataclass
class GovernorDecision:
    """Результат глобального risk governor."""
    timestamp: str
    level: str = RiskLevel.GREEN
    allowed_actions: list = field(default_factory=lambda: ["OPEN", "CLOSE", "MODIFY"])
    position_size_multiplier: float = 1.0     # 1.0 = full, 0.5 = half, 0.0 = none
    max_new_positions: int = 3
    flatten_all: bool = False
    freeze_new_orders: bool = False

    # Diagnostics
    triggered_rules: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        rules = ", ".join(self.triggered_rules) if self.triggered_rules else "none"
        return (
            f"level={self.level} actions={self.allowed_actions} "
            f"size_mult={self.position_size_multiplier:.2f} "
            f"flatten={self.flatten_all} rules=[{rules}]"
        )


# ── Risk Rules ─────────────────────────────────────────────

@dataclass
class RiskRule:
    """Одно правило risk governor."""
    name: str
    description: str
    check_fn: str = ""       # имя метода в GovernorEngine
    level_on_trigger: str = RiskLevel.YELLOW
    weight: float = 1.0      # чем выше, тем серьёзнее
    enabled: bool = True


class GovernorRules:
    """Каталог всех risk rules."""

    RULES = [
        RiskRule(
            name="max_drawdown",
            description="Превышен максимальный drawdown",
            level_on_trigger=RiskLevel.RED,
            weight=10.0,
        ),
        RiskRule(
            name="daily_loss_limit",
            description="Достигнут дневной лимит убытков",
            level_on_trigger=RiskLevel.ORANGE,
            weight=8.0,
        ),
        RiskRule(
            name="consecutive_losses",
            description="Слишком много убытков подряд",
            level_on_trigger=RiskLevel.YELLOW,
            weight=5.0,
        ),
        RiskRule(
            name="execution_instability",
            description="Высокий drift в T6 (.execution truth)",
            level_on_trigger=RiskLevel.ORANGE,
            weight=7.0,
        ),
        RiskRule(
            name="volatility_spike",
            description="Резкий всплеск волатильности",
            level_on_trigger=RiskLevel.YELLOW,
            weight=4.0,
        ),
        RiskRule(
            name="regime_chaos",
            description="Режим слишком нестабилен",
            level_on_trigger=RiskLevel.YELLOW,
            weight=3.0,
        ),
        RiskRule(
            name="correlation_collapse",
            description="Корреляции между инструментами взрыв",
            level_on_trigger=RiskLevel.ORANGE,
            weight=6.0,
        ),
        RiskRule(
            name="liquidity_collapse",
            description="Ликвидность упала ниже порога",
            level_on_trigger=RiskLevel.RED,
            weight=9.0,
        ),
        RiskRule(
            name="position_concentration",
            description="Слишком много капитала в одном инструменте",
            level_on_trigger=RiskLevel.YELLOW,
            weight=4.0,
        ),
        RiskRule(
            name="system_health_degraded",
            description="Системные компоненты в DEGRADED/FAILED",
            level_on_trigger=RiskLevel.ORANGE,
            weight=6.0,
        ),
    ]

    @classmethod
    def get_all(cls) -> list[RiskRule]:
        return [r for r in cls.RULES if r.enabled]


# ── Risk Metrics ───────────────────────────────────────────

@dataclass
class RiskMetrics:
    """Текущие risk metrics для governor."""
    # PnL
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0

    # Drawdown
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0

    # Losses
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    loss_streak_days: int = 0

    # Execution
    avg_drift_score: float = 0.0
    execution_errors_24h: int = 0

    # Market
    volatility_pct: float = 0.0
    regime_instability: float = 0.0
    correlation_score: float = 0.0
    liquidity_score: float = 1.0

    # Positions
    open_positions: int = 0
    position_concentration_pct: float = 0.0
    total_capital_deployed_pct: float = 0.0

    # System
    system_health_score: float = 1.0
    module_failures: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Governor Engine ────────────────────────────────────────

class GovernorEngine:
    """Глобальный risk governor. Определяет можно ли торговать."""

    # Thresholds
    MAX_DRAWDOWN_PCT = 10.0          # max allowed drawdown
    DAILY_LOSS_LIMIT_PCT = 3.0       # max daily loss
    MAX_CONSECUTIVE_LOSSES = 5       # max losing trades in row
    MAX_EXECUTION_DRIFT = 0.5        # max T6 drift score
    VOLATILITY_SPIKE_THRESHOLD = 2.0 # ATR spike multiplier
    MIN_LIQUIDITY_SCORE = 0.3        # min liquidity to trade
    MAX_CONCENTRATION_PCT = 40.0     # max % in one instrument
    MIN_SYSTEM_HEALTH = 0.5          # min system health

    def evaluate(self, metrics: RiskMetrics) -> GovernorDecision:
        """Оценить risk level на основе текущих метрик."""
        now = datetime.now(timezone.utc).isoformat()
        triggered = []

        # Level accumulation
        level_scores = {RiskLevel.GREEN: 0, RiskLevel.YELLOW: 0, RiskLevel.ORANGE: 0, RiskLevel.RED: 0}

        # Rule checks
        checks = [
            ("max_drawdown", self._check_max_drawdown, metrics),
            ("daily_loss_limit", self._check_daily_loss, metrics),
            ("consecutive_losses", self._check_consecutive_losses, metrics),
            ("execution_instability", self._check_execution_instability, metrics),
            ("volatility_spike", self._check_volatility_spike, metrics),
            ("regime_chaos", self._check_regime_chaos, metrics),
            ("position_concentration", self._check_concentration, metrics),
            ("liquidity_collapse", self._check_liquidity, metrics),
            ("system_health_degraded", self._check_system_health, metrics),
        ]

        for name, check_fn, m in checks:
            result = check_fn(m)
            if result:
                triggered.append(name)
                # Find rule weight
                for rule in GovernorRules.get_all():
                    if rule.name == name:
                        level_scores[rule.level_on_trigger] += rule.weight
                        break

        # Determine final level (highest triggered)
        if level_scores[RiskLevel.RED] > 0:
            final_level = RiskLevel.RED
        elif level_scores[RiskLevel.ORANGE] > 0:
            final_level = RiskLevel.ORANGE
        elif level_scores[RiskLevel.YELLOW] > 0:
            final_level = RiskLevel.YELLOW
        else:
            final_level = RiskLevel.GREEN

        # Build decision
        decision = GovernorDecision(
            timestamp=now,
            level=final_level,
            triggered_rules=triggered,
            metrics=metrics.to_dict(),
        )

        # Apply level-specific settings
        if final_level == RiskLevel.GREEN:
            decision.allowed_actions = ["OPEN", "CLOSE", "MODIFY"]
            decision.position_size_multiplier = 1.0
            decision.max_new_positions = 3
            decision.reasoning = "All systems normal"
        elif final_level == RiskLevel.YELLOW:
            decision.allowed_actions = ["OPEN", "CLOSE", "MODIFY"]
            decision.position_size_multiplier = 0.5
            decision.max_new_positions = 2
            decision.reasoning = f"Reduced capacity: {', '.join(triggered)}"
        elif final_level == RiskLevel.ORANGE:
            decision.allowed_actions = ["CLOSE", "MODIFY"]
            decision.position_size_multiplier = 0.0
            decision.max_new_positions = 0
            decision.freeze_new_orders = True
            decision.reasoning = f"Frozen new orders: {', '.join(triggered)}"
        elif final_level == RiskLevel.RED:
            decision.allowed_actions = ["CLOSE"]
            decision.position_size_multiplier = 0.0
            decision.max_new_positions = 0
            decision.flatten_all = True
            decision.freeze_new_orders = True
            decision.reasoning = f"FLATTEN ALL: {', '.join(triggered)}"

        return decision

    # ── Rule implementations ────────────────────────────────

    def _check_max_drawdown(self, m: RiskMetrics) -> bool:
        return m.current_drawdown_pct > self.MAX_DRAWDOWN_PCT

    def _check_daily_loss(self, m: RiskMetrics) -> bool:
        return m.daily_pnl < 0 and abs(m.daily_pnl / m.current_equity * 100) > self.DAILY_LOSS_LIMIT_PCT if m.current_equity > 0 else False

    def _check_consecutive_losses(self, m: RiskMetrics) -> bool:
        return m.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES

    def _check_execution_instability(self, m: RiskMetrics) -> bool:
        return m.avg_drift_score > self.MAX_EXECUTION_DRIFT or m.execution_errors_24h > 5

    def _check_volatility_spike(self, m: RiskMetrics) -> bool:
        # Simple: if vol > 2x normal threshold
        return m.volatility_pct > self.VOLATILITY_SPIKE_THRESHOLD

    def _check_regime_chaos(self, m: RiskMetrics) -> bool:
        return m.regime_instability > 0.7

    def _check_concentration(self, m: RiskMetrics) -> bool:
        return m.position_concentration_pct > self.MAX_CONCENTRATION_PCT

    def _check_liquidity(self, m: RiskMetrics) -> bool:
        return m.liquidity_score < self.MIN_LIQUIDITY_SCORE

    def _check_system_health(self, m: RiskMetrics) -> bool:
        return m.system_health_score < self.MIN_SYSTEM_HEALTH or m.module_failures > 0


# ── Governor Ledger ────────────────────────────────────────

class GovernorLedger:
    """Журнал решений governor для анализа."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or Path(__file__).parent.parent.parent / "tradingos_governor.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS governor_decisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                level       TEXT NOT NULL,
                flatten_all INTEGER NOT NULL,
                freeze_new  INTEGER NOT NULL,
                size_mult   REAL NOT NULL,
                max_pos     INTEGER NOT NULL,
                rules_json  TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                reasoning   TEXT
            )
        """)
        conn.commit()
        conn.close()

    def write(self, decision: GovernorDecision):
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.execute("""
            INSERT INTO governor_decisions
            (timestamp, level, flatten_all, freeze_new, size_mult, max_pos, rules_json, metrics_json, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            decision.timestamp, decision.level,
            int(decision.flatten_all), int(decision.freeze_new_orders),
            decision.position_size_multiplier, decision.max_new_positions,
            json.dumps(decision.triggered_rules),
            json.dumps(decision.metrics),
            decision.reasoning,
        ))
        conn.commit()
        conn.close()

    def get_recent(self, limit: int = 10) -> list[dict]:
        conn = sqlite3.connect(str(self._path), timeout=10)
        rows = conn.execute(
            "SELECT * FROM governor_decisions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        cols = ["id", "timestamp", "level", "flatten_all", "freeze_new",
                "size_mult", "max_pos", "rules_json", "metrics_json", "reasoning"]
        return [dict(zip(cols, row)) for row in rows]

    def stats(self) -> dict:
        conn = sqlite3.connect(str(self._path), timeout=10)
        total = conn.execute("SELECT COUNT(*) FROM governor_decisions").fetchone()[0]
        by_level = {}
        for row in conn.execute("SELECT level, COUNT(*) FROM governor_decisions GROUP BY level"):
            by_level[row[0]] = row[1]
        conn.close()
        return {"total_decisions": total, "by_level": by_level}
