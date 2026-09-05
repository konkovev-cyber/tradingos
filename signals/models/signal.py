"""
Signal — торговый сигнал от стратегии.

Стратегия создаёт Signal → Decision Engine оценивает → Risk проверяет → Execution исполняет.

Каждая стратегия работает ТОЛЬКО с этой моделью.
Она не знает про биржу, ордера или позиции.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
import time


class SignalDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalStatus(Enum):
    PENDING = "PENDING"           # Стратегия создала сигнал
    APPROVED = "APPROVED"         # Decision Engine одобрил
    REJECTED = "REJECTED"         # Отклонён
    EXECUTING = "EXECUTING"       # Отправляется на биржу
    FILLED = "FILLED"             # Ордер исполнен
    CANCELLED = "CANCELLED"       # Отменён
    EXPIRED = "EXPIRED"           # Истёк


@dataclass
class Signal:
    """
    Торговый сигнал от стратегии.

    Это ЕДИНСТВЕННЫЙ способ для стратегии сообщить о желании войти в позицию.
    Стратегия НЕ размещает ордера — она создаёт Signal.

    Pipeline:
        Strategy.generate_signal() → Signal
        DecisionEngine.evaluate(signal) → approved/rejected
        RiskManager.check(signal) → approved/rejected
        ExecutionEngine.execute(signal) → Order
    """

    # === Идентификация ===
    strategy: str                   # Имя стратегии (e.g. "Scalping", "Trend")
    symbol: str                     # Торговая пара (e.g. "BTCUSDT")
    direction: SignalDirection      # BUY | SELL

    # === Качество сигнала ===
    confidence: float = 0.0        # 0.0 - 1.0 (уверенность стратегии)
    score: int = 0                  # 0 - 100 (综合评分)
    reasons: List[str] = field(default_factory=list)  # Причины сигнала

    # === Риск-параметры (стратегия ПРЕДЛАГАЕТ, RiskManager РЕШАЕТ) ===
    entry_price: float = 0.0       # Желаемая цена входа (0 = market)
    stop_loss: float = 0.0         # Предлагаемый SL
    take_profits: List[float] = field(default_factory=list)  # Предлагаемые TP (может быть несколько)
    risk_reward: float = 0.0       # Ожидаемый RR

    # === Контекст ===
    timeframe: str = "1m"          # Таймфрейм сигнала
    regime: str = ""               # Рыночный режим при сигнале
    features: Dict[str, Any] = field(default_factory=dict)  # Ключевые признаки

    # === Lifecycle ===
    status: SignalStatus = SignalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # === Результат (заполняется после исполнения) ===
    order_id: Optional[str] = None
    fill_price: float = 0.0
    fill_qty: float = 0.0
    reject_reason: Optional[str] = None

    # === Метаданные ===
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        """How old is this signal."""
        return time.time() - self.created_at

    @property
    def is_buy(self) -> bool:
        return self.direction == SignalDirection.BUY

    @property
    def is_sell(self) -> bool:
        return self.direction == SignalDirection.SELL

    @property
    def is_pending(self) -> bool:
        return self.status == SignalStatus.PENDING

    @property
    def is_active(self) -> bool:
        """Signal is still actionable."""
        return self.status in (
            SignalStatus.PENDING,
            SignalStatus.APPROVED,
            SignalStatus.EXECUTING,
        )

    def approve(self) -> None:
        """Mark signal as approved by Decision Engine."""
        self.status = SignalStatus.APPROVED
        self.updated_at = time.time()

    def reject(self, reason: str) -> None:
        """Mark signal as rejected."""
        self.status = SignalStatus.REJECTED
        self.reject_reason = reason
        self.updated_at = time.time()

    def mark_executing(self) -> None:
        """Mark signal as being executed."""
        self.status = SignalStatus.EXECUTING
        self.updated_at = time.time()

    def mark_filled(self, order_id: str, fill_price: float, fill_qty: float) -> None:
        """Mark signal as filled."""
        self.status = SignalStatus.FILLED
        self.order_id = order_id
        self.fill_price = fill_price
        self.fill_qty = fill_qty
        self.updated_at = time.time()

    def expire(self) -> None:
        """Mark signal as expired."""
        self.status = SignalStatus.EXPIRED
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "confidence": self.confidence,
            "score": self.score,
            "reasons": self.reasons,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "risk_reward": self.risk_reward,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "status": self.status.value,
            "created_at": self.created_at,
            "order_id": self.order_id,
            "fill_price": self.fill_price,
            "fill_qty": self.fill_qty,
            "reject_reason": self.reject_reason,
        }

    def __repr__(self) -> str:
        return (
            f"Signal({self.strategy} {self.symbol} {self.direction.value} "
            f"conf={self.confidence:.0%} score={self.score} "
            f"status={self.status.value})"
        )
