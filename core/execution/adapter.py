"""
ExecutionAdapter — абстрактный контракт для всех адаптеров исполнения.

Contract:
  open_position(signal) → position_id
  close_position(position_id) → result
  get_positions() → list[PositionState]
  sync_state() → dict
  health() → bool

Реализации:
  - MT5DemoAdapter (существующий)
  - CryptoShadowExecutor (теневой, без реальных ордеров)
  - BybitLiveAdapter (реальный, будущий)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(str, Enum):
    PENDING = "PENDING"          # сигнал получен, вход не состоялся
    OPEN = "OPEN"                # позиция открыта
    CLOSED_TP = "CLOSED_TP"      # закрыта по тейку
    CLOSED_SL = "CLOSED_SL"      # закрыта по стопу
    CLOSED_MANUAL = "CLOSED_MANUAL"  # закрыта вручную
    CLOSED_EXPIRY = "CLOSED_EXPIRY"  # закрыта по времени


@dataclass
class PositionState:
    """Состояние одной позиции в любой момент времени."""
    position_id: str
    symbol: str
    side: PositionSide
    entry_price: float
    quantity: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: PositionStatus = PositionStatus.PENDING
    opened_at: Optional[float] = None
    closed_at: Optional[float] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    mfe: float = 0.0          # Maximum Favorable Excursion
    mae: float = 0.0          # Maximum Adverse Excursion
    bars_held: int = 0
    signal_metadata: dict = field(default_factory=dict)


@dataclass
class CloseResult:
    """Результат закрытия позиции."""
    position_id: str
    exit_price: float
    pnl: float
    pnl_pct: float
    reason: str  # "TP" | "SL" | "MANUAL" | "EXPIRY"
    mfe: float
    mae: float
    bars_held: int


class ExecutionAdapter(ABC):
    """Абстрактный адаптер исполнения."""

    @abstractmethod
    async def open_position(
        self,
        symbol: str,
        side: PositionSide,
        entry_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        quantity: float = 0.001,
        metadata: Optional[dict] = None,
    ) -> str:
        """Открыть позицию. Возвращает position_id."""
        ...

    @abstractmethod
    async def close_position(self, position_id: str) -> Optional[CloseResult]:
        """Закрыть позицию. Возвращает результат."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[PositionState]:
        """Получить все открытые позиции."""
        ...

    @abstractmethod
    async def sync_state(self) -> dict:
        """Синхронизировать состояние. Возвращает отчёт."""
        ...

    @abstractmethod
    async def health(self) -> bool:
        """Проверка здоровья адаптера."""
        ...
