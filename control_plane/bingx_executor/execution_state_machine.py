"""
control_plane/bingx_executor/execution_state_machine.py
Execution State Machine — отслеживает жизненный цикл исполнения.

Состояния:
  PENDING          → решение зафиксировано, ещё не исполнено
  BUILDING         → готовим API запрос
  SENDING          → отправляем на биржу
  SENT             → биржа подтвердила
  VERIFIED         → проверили на бирже — TP/SL реально существует
  FAILED           → ошибка, нужен RETRY
  RETRYING         → повторная попытка
  MANUAL_REQUIRED  → после N попыток — человек должен сделать вручную
  REJECTED         → человек отклонил

Правила:
  - Каждый переход логируется
  - VERIFIED = успех, больше не трогаем
  - FAILED → RETRY (до 3 попыток) → MANUAL_REQUIRED
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class ExecutionState(str, Enum):
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    SENDING = "SENDING"
    SENT = "SENT"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    REJECTED = "REJECTED"


@dataclass
class ExecutionRecord:
    record_id: str
    symbol: str
    action: str  # e.g. "SET_TP", "CLOSE", "MODIFY_SL"
    target_price: float
    state: ExecutionState
    attempts: int = 0
    max_attempts: int = 3
    last_error: str = ""
    last_response: dict = field(default_factory=dict)
    verified_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "symbol": self.symbol,
            "action": self.action,
            "target_price": self.target_price,
            "state": self.state.value,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "last_response": self.last_response,
            "verified_at": self.verified_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ExecutionStateMachine:
    """
    Manages lifecycle of one execution attempt.
    PENDING → BUILDING → SENDING → SENT → VERIFIED
    On failure: FAILED → RETRYING (up to max_attempts) → MANUAL_REQUIRED
    """

    def __init__(self, record: ExecutionRecord):
        self.record = record
        self._history: List[tuple] = [(record.state, record.created_at)]

    def transition(self, new_state: ExecutionState, error: str = "", response: dict = None):
        """Make a state transition."""
        old = self.record.state
        self.record.state = new_state
        self.record.updated_at = datetime.now(timezone.utc).isoformat()
        if error:
            self.record.last_error = error
        if response:
            self.record.last_response = response
        if new_state == ExecutionState.VERIFIED:
            self.record.verified_at = self.record.updated_at
        if new_state in (ExecutionState.SENDING, ExecutionState.RETRYING):
            self.record.attempts += 1
        self._history.append((new_state, self.record.updated_at))

    def should_retry(self) -> bool:
        return self.record.attempts < self.record.max_attempts

    def is_terminal(self) -> bool:
        return self.record.state in (
            ExecutionState.VERIFIED,
            ExecutionState.MANUAL_REQUIRED,
            ExecutionState.REJECTED,
        )

    def history(self) -> List[dict]:
        return [{"state": s.value, "at": t} for s, t in self._history]
