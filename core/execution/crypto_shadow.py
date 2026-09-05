"""
CryptoShadowExecutor — теневое исполнение для Bybit testnet.

Не отправляет реальные ордера.
Создаёт виртуальные позиции по сигналам стратегии.
Трекает MFE/MAE на каждой свече.
Пишет события в Data Lake.

Поддерживает A/B риск-профили через RiskConfig.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("CryptoShadowExecutor")

# ── Data models ─────────────────────────────────────────────

class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class PositionStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_EXPIRY = "CLOSED_EXPIRY"

@dataclass
class PositionState:
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
    mfe: float = 0.0
    mae: float = 0.0
    bars_held: int = 0
    signal_metadata: dict = field(default_factory=dict)

@dataclass
class CloseResult:
    position_id: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    reason: str          # "SL" | "TP" | "EXPIRY" | "MANUAL"
    mfe: float
    mae: float
    mae_before_close: float  # MAE at the moment of exit (not max ever)
    bars_held: int
    profile: str = "A"       # risk profile label

# ── Risk Config ─────────────────────────────────────────────

@dataclass
class RiskConfig:
    """Профиль риск-менеджмента для shadow execution."""
    label: str = "A"
    sl_atr_multiple: float = 1.5
    tp_atr_multiple: float = 2.5
    max_bars_hold: int = 48

    def describe(self) -> str:
        return f"{self.label}: SL={self.sl_atr_multiple}×ATR TP={self.tp_atr_multiple}×ATR hold={self.max_bars_hold}bars"


# Default profiles
PROFILE_A = RiskConfig(label="A", sl_atr_multiple=1.5, tp_atr_multiple=2.5)
PROFILE_B = RiskConfig(label="B", sl_atr_multiple=3.0, tp_atr_multiple=4.5)
PROFILE_C = RiskConfig(label="C", sl_atr_multiple=2.0, tp_atr_multiple=3.5)


# ── Shadow Executor ─────────────────────────────────────────

class CryptoShadowExecutor:
    """Теневое исполнение: виртуальные позиции, никаких реальных ордеров."""

    def __init__(
        self,
        risk_config: RiskConfig = PROFILE_A,
        data_lake=None,
        db_path: Optional[Path] = None,
    ):
        self._config = risk_config
        self._positions: dict[str, PositionState] = {}
        self._closed: list[CloseResult] = []
        self._stats = {
            "total_signals": 0,
            "positions_opened": 0,
            "positions_closed": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "mfe_sum": 0.0,
            "mae_sum": 0.0,
            "mae_before_close_sum": 0.0,
            "bars_held_sum": 0,
            "exit_reasons": {"SL": 0, "TP": 0, "EXPIRY": 0, "MANUAL": 0},
        }

        # Data Lake
        self._lake = data_lake
        self._db_path = db_path
        if self._lake is None and db_path is not None:
            import importlib.util
            _dl_spec = importlib.util.spec_from_file_location(
                "sqlite_backend", str(db_path.parent / "core" / "data_lake" / "sqlite_backend.py")
            )
            _dl_mod = importlib.util.module_from_spec(_dl_spec)
            _dl_spec.loader.exec_module(_dl_mod)
            self._lake = _dl_mod.DataLake(db_path)

    @property
    def config(self) -> RiskConfig:
        return self._config

    # ── Position lifecycle ─────────────────────────────────

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
        pos_id = f"SH_{self._config.label}_{symbol}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now = time.time()

        pos = PositionState(
            position_id=pos_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=PositionStatus.OPEN,
            opened_at=now,
            signal_metadata=metadata or {},
        )
        self._positions[pos_id] = pos
        self._stats["positions_opened"] += 1

        self._write_event("ShadowPositionOpened", {
            "position_id": pos_id,
            "profile": self._config.label,
            "symbol": symbol,
            "side": side.value,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "quantity": quantity,
            "signal_metadata": metadata,
        })

        logger.info(f"[SHADOW-{self._config.label}] OPEN {side.value} {symbol} @ {entry_price} "
                     f"SL={stop_loss} TP={take_profit}")
        return pos_id

    async def close_position(self, position_id: str) -> Optional[CloseResult]:
        pos = self._positions.get(position_id)
        if not pos or pos.status != PositionStatus.OPEN:
            return None

        exit_price = pos.signal_metadata.get("current_price", pos.entry_price)
        pnl, pnl_pct = self._compute_pnl(pos, exit_price)
        reason = pos.signal_metadata.get("close_reason", "MANUAL")

        # MAE at the moment of exit (not max ever)
        if pos.side == PositionSide.LONG:
            mae_at_exit = pos.entry_price - exit_price if exit_price < pos.entry_price else 0
        else:
            mae_at_exit = exit_price - pos.entry_price if exit_price > pos.entry_price else 0

        result = CloseResult(
            position_id=position_id,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=reason,
            mfe=pos.mfe,
            mae=pos.mae,
            mae_before_close=mae_at_exit,
            bars_held=pos.bars_held,
            profile=self._config.label,
        )

        pos.status = PositionStatus.CLOSED_MANUAL
        pos.exit_price = exit_price
        pos.pnl = pnl
        pos.pnl_pct = pnl_pct
        pos.closed_at = time.time()

        self._closed.append(result)
        self._stats["positions_closed"] += 1
        self._stats["total_pnl"] += pnl
        self._stats["mfe_sum"] += pos.mfe
        self._stats["mae_sum"] += pos.mae
        self._stats["mae_before_close_sum"] += mae_at_exit
        self._stats["bars_held_sum"] += pos.bars_held
        self._stats["exit_reasons"][reason] = self._stats["exit_reasons"].get(reason, 0) + 1

        if pnl > 0:
            self._stats["wins"] += 1
        else:
            self._stats["losses"] += 1

        self._write_event("ShadowPositionClosed", {
            "position_id": position_id,
            "profile": self._config.label,
            "symbol": pos.symbol,
            "side": pos.side.value,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "pnl": round(pnl, 6),
            "pnl_pct": round(pnl_pct, 4),
            "reason": reason,
            "mfe": round(pos.mfe, 2),
            "mae": round(pos.mae, 2),
            "mae_before_close": round(mae_at_exit, 2),
            "bars_held": pos.bars_held,
        })

        logger.info(f"[SHADOW-{self._config.label}] CLOSE {pos.side.value} {pos.symbol} "
                     f"entry={pos.entry_price} exit={exit_price} "
                     f"pnl={pnl:.4f} mfe={pos.mfe:.2f} mae={pos.mae:.2f} "
                     f"reason={reason} bars={pos.bars_held}")
        return result

    async def get_positions(self) -> list[PositionState]:
        return [p for p in self._positions.values() if p.status == PositionStatus.OPEN]

    async def sync_state(self) -> dict:
        open_positions = await self.get_positions()
        return {
            "profile": self._config.label,
            "open_count": len(open_positions),
            "closed_count": len(self._closed),
            "positions": [p.__dict__ for p in open_positions],
            "stats": dict(self._stats),
        }

    async def health(self) -> bool:
        return True

    # ── Bar-by-bar update ──────────────────────────────────

    async def update_positions(self, market_data: dict):
        """Обновить все открытые позиции на новой свече.
        Проверить TP/SL, обновить MFE/MAE.
        Возвращает список закрытых позиций за этот бар.
        """
        price = market_data.get("close", 0)
        high = market_data.get("high", 0)
        low = market_data.get("low", 0)

        closed = []
        for pos_id, pos in list(self._positions.items()):
            if pos.status != PositionStatus.OPEN:
                continue

            pos.bars_held += 1

            # Update MFE/MAE
            if pos.side == PositionSide.LONG:
                mfe_candidate = high - pos.entry_price
                mae_candidate = pos.entry_price - low
            else:
                mfe_candidate = pos.entry_price - low
                mae_candidate = high - pos.entry_price

            pos.mfe = max(pos.mfe, mfe_candidate)
            pos.mae = max(pos.mae, mae_candidate)

            # Check SL
            if pos.stop_loss is not None:
                hit_sl = (
                    (pos.side == PositionSide.LONG and low <= pos.stop_loss) or
                    (pos.side == PositionSide.SHORT and high >= pos.stop_loss)
                )
                if hit_sl:
                    pos.signal_metadata["current_price"] = pos.stop_loss
                    pos.signal_metadata["close_reason"] = "SL"
                    result = await self.close_position(pos_id)
                    if result:
                        closed.append(result)
                    continue

            # Check TP
            if pos.take_profit is not None:
                hit_tp = (
                    (pos.side == PositionSide.LONG and high >= pos.take_profit) or
                    (pos.side == PositionSide.SHORT and low <= pos.take_profit)
                )
                if hit_tp:
                    pos.signal_metadata["current_price"] = pos.take_profit
                    pos.signal_metadata["close_reason"] = "TP"
                    result = await self.close_position(pos_id)
                    if result:
                        closed.append(result)
                    continue

            # Auto-close after max bars
            if pos.bars_held >= self._config.max_bars_hold:
                pos.signal_metadata["current_price"] = price
                pos.signal_metadata["close_reason"] = "EXPIRY"
                result = await self.close_position(pos_id)
                if result:
                    closed.append(result)
                continue

            # Write position update event
            self._write_event("ShadowPositionUpdated", {
                "position_id": pos_id,
                "profile": self._config.label,
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry_price": pos.entry_price,
                "current_price": price,
                "mfe": round(pos.mfe, 2),
                "mae": round(pos.mae, 2),
                "bars_held": pos.bars_held,
                "unrealized_pnl": round(self._compute_pnl(pos, price)[0], 6),
            })

        return closed

    # ── Process a strategy signal ──────────────────────────

    async def process_signal(self, signal) -> Optional[str]:
        """Принять сигнал от стратегии, открыть виртуальную позицию."""
        self._stats["total_signals"] += 1

        symbol = signal.symbol
        direction = signal.direction
        price = signal.metadata.get("current_price", 0)
        atr = signal.metadata.get("atr", 0)
        adx = signal.metadata.get("adx", 0)
        metadata = dict(signal.metadata)

        if price == 0:
            logger.warning(f"[SHADOW-{self._config.label}] Signal {symbol} {direction}: no price, skipping")
            return None

        # Compute SL/TP from ATR
        if atr > 0:
            if direction == "BUY":
                sl = price - atr * self._config.sl_atr_multiple
                tp = price + atr * self._config.tp_atr_multiple
            else:
                sl = price + atr * self._config.sl_atr_multiple
                tp = price - atr * self._config.tp_atr_multiple
        else:
            sl = None
            tp = None

        side = PositionSide.LONG if direction == "BUY" else PositionSide.SHORT
        metadata["atr"] = atr
        metadata["adx"] = adx
        metadata["sl_atr_multiple"] = self._config.sl_atr_multiple
        metadata["tp_atr_multiple"] = self._config.tp_atr_multiple
        metadata["profile"] = self._config.label

        pos_id = await self.open_position(
            symbol=symbol,
            side=side,
            entry_price=price,
            stop_loss=sl,
            take_profit=tp,
            quantity=0.001,
            metadata=metadata,
        )
        return pos_id

    # ── Stats ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        s = self._stats
        total_closed = s["positions_closed"]
        return {
            "profile": self._config.label,
            "config": self._config.describe(),
            "total_signals": s["total_signals"],
            "positions_opened": s["positions_opened"],
            "positions_closed": s["positions_closed"],
            "open_positions": sum(1 for p in self._positions.values() if p.status == PositionStatus.OPEN),
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": round(s["wins"] / total_closed, 4) if total_closed else 0,
            "total_pnl": round(s["total_pnl"], 6),
            "avg_mfe": round(s["mfe_sum"] / total_closed, 2) if total_closed else 0,
            "avg_mae": round(s["mae_sum"] / total_closed, 2) if total_closed else 0,
            "avg_mae_before_close": round(s["mae_before_close_sum"] / total_closed, 2) if total_closed else 0,
            "avg_bars_held": round(s["bars_held_sum"] / total_closed, 1) if total_closed else 0,
            "avg_mfe_mae_ratio": round(
                (s["mfe_sum"] / total_closed) / max((s["mae_sum"] / total_closed), 0.01), 2
            ) if total_closed else 0,
            "exit_reasons": dict(s["exit_reasons"]),
        }

    def get_closed_positions(self) -> list[CloseResult]:
        return list(self._closed)

    def reset(self):
        self._positions.clear()
        self._closed.clear()
        self._stats = {
            "total_signals": 0, "positions_opened": 0, "positions_closed": 0,
            "wins": 0, "losses": 0, "total_pnl": 0.0,
            "mfe_sum": 0.0, "mae_sum": 0.0, "mae_before_close_sum": 0.0,
            "bars_held_sum": 0,
            "exit_reasons": {"SL": 0, "TP": 0, "EXPIRY": 0, "MANUAL": 0},
        }

    # ── Internal ───────────────────────────────────────────

    def _compute_pnl(self, pos: PositionState, exit_price: float) -> tuple[float, float]:
        if pos.side == PositionSide.LONG:
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity
        pnl_pct = pnl / (pos.entry_price * pos.quantity) if pos.entry_price * pos.quantity > 0 else 0
        return pnl, pnl_pct

    def _write_event(self, event_type: str, payload: dict):
        if self._lake is None:
            return
        event = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_module": "crypto_shadow_executor",
            "symbol": payload.get("symbol", ""),
            "severity": "info",
            "trace_id": str(uuid.uuid4()),
            "payload": payload,
        }
        self._lake.write(event)
