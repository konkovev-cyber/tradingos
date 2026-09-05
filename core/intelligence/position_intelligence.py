"""
PIE v1.1 Position Intelligence — Event Observation Layer.

Наблюдающий мозг позиции. Не управляет, а анализирует и записывает события.

Режимы:
  - observe: только анализ и запись событий (по умолчанию)
  - live_assist: PIE рекомендует действия, человек сравнивает
  - shadow: пишет рекомендации, но не исполняет
  - active: исполняет рекомендации (будущее)

События позиций:
  - ENTRY: позиция открыта
  - PROFIT_STARTED: позиция впервые в профите
  - NEW_MFE: новый максимум прибыли
  - PROFIT_DECAY: прибыль откатилась от пика
  - TREND_CHANGE: тренд сменился против позиции
  - RISK_WARNING: порог риска достигнут
  - THESIS_BROKEN: тезис сделки нарушен
  - EXIT: позиция закрыта

LIVE_ASSIST режим:
  PIE записывает рекомендации с контекстом.
  Через 15/30/60 минут проверяется результат.
  Это обучение на своих данных.
"""

import json
import logging
import time
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List
from pathlib import Path

logger = logging.getLogger("PositionIntelligence")


class PositionEvent(str, Enum):
    """События жизненного цикла позиции."""
    ENTRY = "ENTRY"
    PROFIT_STARTED = "PROFIT_STARTED"
    NEW_MFE = "NEW_MFE"
    PROFIT_DECAY = "PROFIT_DECAY"
    TREND_CHANGE = "TREND_CHANGE"
    RISK_WARNING = "RISK_WARNING"
    THESIS_BROKEN = "THESIS_BROKEN"
    EXIT = "EXIT"


class PositionState(str, Enum):
    """Состояния позиции для анализа."""
    ENTRY_FORMING = "ENTRY_FORMING"
    PROFIT_FORMING = "PROFIT_FORMING"
    PROFIT_LOCKED = "PROFIT_LOCKED"
    PROFIT_DECAYING = "PROFIT_DECAYING"
    RISK_ZONE = "RISK_ZONE"
    THESIS_COMPROMISED = "THESIS_COMPROMISED"
    STOPPED_OUT = "STOPPED_OUT"
    CLOSED_PROFIT = "CLOSED_PROFIT"
    CLOSED_LOSS = "CLOSED_LOSS"


class Recommendation(str, Enum):
    """Рекомендации на основе анализа."""
    HOLD = "HOLD"
    TIGHTEN_SL = "TIGHTEN_SL"
    TAKE_PARTIAL = "TAKE_PARTIAL"
    MOVE_SL_BE = "MOVE_SL_BE"
    EXIT_NOW = "EXIT_NOW"
    WAIT = "WAIT"


@dataclass
class PositionSnapshot:
    """Мгновенный снимок состояния позиции."""
    position_id: str
    symbol: str
    side: str  # BUY / SELL
    entry_price: float
    current_price: float
    pnl_pct: float
    max_profit_seen: float  # MFE в %
    max_loss_seen: float    # MAE в %
    profit_retracement: float  # % отданной прибыли от пика
    time_in_position: float  # секунды
    bars_held: int
    state: PositionState
    health_score: float  # 0-100
    recommendation: Recommendation
    reason: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class PositionMemory:
    """Память позиции — MFE/MAE история."""
    position_id: str
    symbol: str
    side: str
    entry_price: float
    entry_time: float
    max_profit_seen: float = 0.0
    max_loss_seen: float = 0.0
    max_profit_price: float = 0.0
    min_loss_price: float = 0.0
    profit_retracement_pct: float = 0.0
    time_to_first_profit: Optional[float] = None
    time_to_mfe: Optional[float] = None
    current_pnl_pct: float = 0.0
    bars_held: int = 0
    health_score: float = 100.0
    events: List[Dict[str, Any]] = field(default_factory=list)
    is_open: bool = True


class PIEPositionIntelligence:
    """
    PIE v1.1 — Observing Position Intelligence.

    Не управляет позициями. Анализирует и записывает события.
    """

    # Пороги для событий
    PROFIT_START_THRESHOLD = 0.001  # 0.1% — позиция считается "в профите"
    PROFIT_DECAY_THRESHOLD = 0.30   # 30% от пика — значительный откат
    RISK_WARNING_THRESHOLD = 0.02   # 2% — порог предупреждения
    THESIS_BROKEN_THRESHOLD = 0.03  # 3% — тезис нарушен

    def __init__(
        self,
        mode: str = "observe",
        db_path: Optional[Path] = None,
    ):
        """
        Args:
            mode: "observe" | "shadow" | "active"
            db_path: путь к SQLite для хранения событий
        """
        self._mode = mode
        self._db_path = db_path
        self._positions: Dict[str, PositionMemory] = {}
        self._closed: List[PositionMemory] = []
        self._lock = threading.Lock()

        if db_path:
            self._init_db()

        logger.info(f"PIE v1.1 initialized: mode={mode}")

    def _init_db(self):
        """Инициализация SQLite для хранения событий позиций."""
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS position_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                max_profit_seen REAL NOT NULL,
                max_loss_seen REAL NOT NULL,
                profit_retracement REAL NOT NULL,
                time_in_position REAL NOT NULL,
                bars_held INTEGER NOT NULL,
                state TEXT NOT NULL,
                health_score REAL NOT NULL,
                recommendation TEXT NOT NULL,
                reason TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS position_summary (
                position_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                max_profit_seen REAL NOT NULL,
                max_loss_seen REAL NOT NULL,
                profit_retracement_final REAL,
                profit_capture_efficiency REAL,  -- реализованная прибыль / MFE
                time_in_position REAL,
                bars_held INTEGER,
                final_pnl_pct REAL,
                exit_reason TEXT,
                health_score_avg REAL,
                events_count INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                closed_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                timestamp_utc TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # LIVE_ASSIST mode: лог рекомендаций с what-if анализом
        conn.execute("""
            CREATE TABLE IF NOT EXISTS live_assist_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                price_at_recommendation REAL NOT NULL,
                pnl_at_recommendation REAL NOT NULL,
                mfe_at_recommendation REAL NOT NULL,
                health_at_recommendation REAL NOT NULL,
                reason TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL DEFAULT (datetime('now')),
                -- What-if результаты (заполняются через 15/30/60 минут)
                price_after_15m REAL,
                pnl_after_15m REAL,
                price_after_30m REAL,
                pnl_after_30m REAL,
                price_after_60m REAL,
                pnl_after_60m REAL,
                -- Оценка качества решения
                decision_quality TEXT,  -- 'correct' | 'early' | 'late' | 'wrong'
                quality_notes TEXT
            )
        """)

        conn.commit()
        conn.close()
        logger.info(f"PIE DB initialized: {self._db_path}")

    def on_position_open(
        self,
        position_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        metadata: Optional[Dict] = None,
    ):
        """Вызывается при открытии позиции."""
        now = time.time()

        memory = PositionMemory(
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=now,
            max_profit_price=entry_price,
            min_loss_price=entry_price,
        )

        with self._lock:
            self._positions[position_id] = memory

        event = {
            "type": PositionEvent.ENTRY.value,
            "timestamp": now,
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
        }
        memory.events.append(event)

        self._log_event(position_id, symbol, side, entry_price, entry_price, 0, 0, 0, 0, 0, 0,
                       PositionState.ENTRY_FORMING.value, 100, Recommendation.HOLD.value,
                       "Position opened", PositionEvent.ENTRY.value)

        logger.info(f"[PIE] ENTRY: {symbol} {side} @ {entry_price} (id={position_id})")

    def on_bar_update(
        self,
        position_id: str,
        current_price: float,
        high: float,
        low: float,
        bars_held: int,
        trend: str = "UNKNOWN",
        btc_trend: str = "UNKNOWN",
    ) -> Optional[PositionSnapshot]:
        """
        Вызывается на каждом обновлении свечи.

        Анализирует состояние позиции, генерирует события.
        Возвращает снимок состояния.
        """
        with self._lock:
            memory = self._positions.get(position_id)
            if not memory or not memory.is_open:
                return None

        entry = memory.entry_price
        side = memory.side

        # Вычисляем PnL
        if side == "BUY":
            pnl_pct = (current_price - entry) / entry
        else:
            pnl_pct = (entry - current_price) / entry

        memory.current_pnl_pct = pnl_pct
        memory.bars_held = bars_held

        # Обновляем MFE/MAE
        if side == "BUY":
            profit_from_entry = (high - entry) / entry
            loss_from_entry = (entry - low) / entry
        else:
            profit_from_entry = (entry - low) / entry
            loss_from_entry = (high - entry) / entry

        # Time to first profit
        if memory.time_to_first_profit is None and pnl_pct > self.PROFIT_START_THRESHOLD:
            memory.time_to_first_profit = time.time() - memory.entry_time

        # New MFE
        if profit_from_entry > memory.max_profit_seen:
            old_mfe = memory.max_profit_seen
            memory.max_profit_seen = profit_from_entry
            memory.max_profit_price = high if side == "BUY" else low
            memory.time_to_mfe = time.time() - memory.entry_time

            if old_mfe > 0:
                self._emit_event(memory, PositionEvent.NEW_MFE, current_price,
                               f"New MFE: {profit_from_entry:.2%} (was {old_mfe:.2%})")

        # MAE
        if loss_from_entry > memory.max_loss_seen:
            memory.max_loss_seen = loss_from_entry
            memory.min_loss_price = low if side == "BUY" else high

        # Profit Retracement
        if memory.max_profit_seen > 0 and pnl_pct < memory.max_profit_seen:
            retracement = (memory.max_profit_seen - pnl_pct) / memory.max_profit_seen
            memory.profit_retracement_pct = retracement

            if retracement > self.PROFIT_DECAY_THRESHOLD:
                self._emit_event(memory, PositionEvent.PROFIT_DECAY, current_price,
                               f"Profit decay: {retracement:.0%} from peak "
                               f"(peak={memory.max_profit_seen:.2%}, now={pnl_pct:.2%})")

        # Health Score
        health = self._compute_health(memory, trend, btc_trend)
        memory.health_score = health

        # Определяем состояние и рекомендацию
        state = self._determine_state(memory, pnl_pct, trend, btc_trend)
        recommendation, reason = self._determine_recommendation(memory, state, pnl_pct)

        # Создаём снимок
        time_in_position = time.time() - memory.entry_time

        snapshot = PositionSnapshot(
            position_id=position_id,
            symbol=memory.symbol,
            side=memory.side,
            entry_price=entry,
            current_price=current_price,
            pnl_pct=pnl_pct,
            max_profit_seen=memory.max_profit_seen,
            max_loss_seen=memory.max_loss_seen,
            profit_retracement=memory.profit_retracement_pct,
            time_in_position=time_in_position,
            bars_held=bars_held,
            state=state,
            health_score=health,
            recommendation=recommendation,
            reason=reason,
        )

        # Логируем в БД
        self._log_event(
            position_id, memory.symbol, memory.side, entry, current_price,
            pnl_pct, memory.max_profit_seen, memory.max_loss_seen,
            memory.profit_retracement_pct, time_in_position, bars_held,
            state.value, health, recommendation.value, reason,
            "BAR_UPDATE"
        )

        # LIVE_ASSIST: логируем рекомендации (не HOLD/WAIT)
        if self._mode == "live_assist" and recommendation.value not in ("HOLD", "WAIT"):
            self.log_assist_recommendation(
                position_id=position_id,
                symbol=memory.symbol,
                side=memory.side,
                recommendation=recommendation.value,
                price=current_price,
                pnl=pnl_pct,
                mfe=memory.max_profit_seen,
                health=health,
                reason=reason,
            )

        # LIVE_ASSIST: обновляем what-if результаты
        if self._mode == "live_assist":
            self.update_assist_results(position_id, current_price, pnl_pct)

        return snapshot

    def on_position_close(
        self,
        position_id: str,
        exit_price: float,
        exit_reason: str = "unknown",
    ) -> Optional[Dict]:
        """Вызывается при закрытии позиции. Возвращает финальный анализ."""
        with self._lock:
            memory = self._positions.get(position_id)
            if not memory:
                return None

        entry = memory.entry_price
        if memory.side == "BUY":
            final_pnl = (exit_price - entry) / entry
        else:
            final_pnl = (entry - exit_price) / entry

        memory.is_open = False

        event = {
            "type": PositionEvent.EXIT.value,
            "timestamp": time.time(),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "final_pnl": final_pnl,
        }
        memory.events.append(event)

        # Сохраняем в summary
        self._save_summary(memory, exit_price, final_pnl, exit_reason)

        # Вычисляем аналитику
        analytics = {
            "position_id": position_id,
            "symbol": memory.symbol,
            "side": memory.side,
            "entry_price": entry,
            "exit_price": exit_price,
            "final_pnl_pct": final_pnl,
            "max_profit_seen": memory.max_profit_seen,
            "max_loss_seen": memory.max_loss_seen,
            "profit_retracement_final": memory.profit_retracement_pct,
            "time_to_first_profit": memory.time_to_first_profit,
            "time_to_mfe": memory.time_to_mfe,
            "bars_held": memory.bars_held,
            "exit_reason": exit_reason,
            "events_count": len(memory.events),
            "health_score_avg": sum(e.get("health", 100) for e in memory.events) / max(len(memory.events), 1),
        }

        self._closed.append(memory)
        del self._positions[position_id]

        self._log_event(
            position_id, memory.symbol, memory.side, entry, exit_price,
            final_pnl, memory.max_profit_seen, memory.max_loss_seen,
            memory.profit_retracement_pct, time.time() - memory.entry_time,
            memory.bars_held, PositionState.CLOSED_PROFIT.value if final_pnl > 0 else PositionState.CLOSED_LOSS.value,
            memory.health_score, Recommendation.HOLD.value,
            f"Closed: {exit_reason}", PositionEvent.EXIT.value
        )

        logger.info(
            f"[PIE] EXIT: {memory.symbol} {memory.side} "
            f"entry={entry} exit={exit_price} pnl={final_pnl:+.2%} "
            f"MFE={memory.max_profit_seen:.2%} MAE={memory.max_loss_seen:.2%} "
            f"retrace={memory.profit_retracement_pct:.0%} reason={exit_reason}"
        )

        return analytics

    def get_position_report(self, position_id: str) -> Optional[Dict]:
        """Получить полный отчёт по позиции."""
        with self._lock:
            memory = self._positions.get(position_id)
            if not memory:
                return None

        return {
            "position_id": memory.position_id,
            "symbol": memory.symbol,
            "side": memory.side,
            "entry_price": memory.entry_price,
            "current_pnl_pct": memory.current_pnl_pct,
            "max_profit_seen": memory.max_profit_seen,
            "max_loss_seen": memory.max_loss_seen,
            "profit_retracement": memory.profit_retracement_pct,
            "time_in_position": time.time() - memory.entry_time,
            "bars_held": memory.bars_held,
            "health_score": memory.health_score,
            "events": memory.events[-10:],  # последние 10 событий
        }

    def get_all_positions_report(self) -> List[Dict]:
        """Отчёт по всем открытым позициям."""
        reports = []
        with self._lock:
            for pos_id in self._positions:
                report = self.get_position_report(pos_id)
                if report:
                    reports.append(report)
        return reports

    def get_closed_analytics(self, limit: int = 100) -> List[Dict]:
        """Аналитика по закрытым позициям."""
        with self._lock:
            return [
                {
                    "position_id": m.position_id,
                    "symbol": m.symbol,
                    "side": m.side,
                    "entry_price": m.entry_price,
                    "max_profit_seen": m.max_profit_seen,
                    "max_loss_seen": m.max_loss_seen,
                    "profit_retracement": m.profit_retracement_pct,
                    "bars_held": m.bars_held,
                    "events_count": len(m.events),
                }
                for m in self._closed[-limit:]
            ]

    # ── Internal ───────────────────────────────────────────

    def _emit_event(self, memory: PositionMemory, event_type: PositionEvent,
                    current_price: float, reason: str):
        """Зафиксировать событие."""
        event = {
            "type": event_type.value,
            "timestamp": time.time(),
            "current_price": current_price,
            "reason": reason,
        }
        memory.events.append(event)
        logger.info(f"[PIE] {event_type.value}: {memory.symbol} {reason}")

    def _compute_health(self, memory: PositionMemory, trend: str, btc_trend: str) -> float:
        """
        Вычислить Health Score позиции (0-100).

        Факторы:
        - Текущий PnL (30%)
        - MFE/MAE ratio (25%)
        - Profit Retracement (25%)
        - Trend alignment (20%)
        """
        score = 100.0

        # PnL factor (30%)
        pnl = memory.current_pnl_pct
        if pnl > 0.02:
            pnl_score = 100
        elif pnl > 0:
            pnl_score = 70 + (pnl / 0.02) * 30
        elif pnl > -0.01:
            pnl_score = 40 + ((pnl + 0.01) / 0.01) * 30
        else:
            pnl_score = max(0, 40 + pnl * 2000)

        score = score * 0.70 + pnl_score * 0.30

        # MFE/MAE ratio (25%)
        if memory.max_loss_seen > 0:
            ratio = memory.max_profit_seen / memory.max_loss_seen
            ratio_score = min(100, ratio * 30)
        else:
            ratio_score = 100 if memory.max_profit_seen > 0 else 50

        score = score * 0.75 + ratio_score * 0.25

        # Profit Retracement (25%)
        retrace = memory.profit_retracement_pct
        if retrace < 0.1:
            retrace_score = 100
        elif retrace < 0.3:
            retrace_score = 70 + (0.3 - retrace) / 0.2 * 30
        elif retrace < 0.5:
            retrace_score = 40 + (0.5 - retrace) / 0.2 * 30
        else:
            retrace_score = max(0, 40 - (retrace - 0.5) * 80)

        score = score * 0.75 + retrace_score * 0.25

        # Trend alignment (20%)
        trend_score = 50  # neutral
        if memory.side == "BUY":
            if trend == "BULLISH":
                trend_score = 90
            elif trend == "BEARISH":
                trend_score = 20
            if btc_trend == "BULLISH":
                trend_score = min(100, trend_score + 20)
            elif btc_trend == "BEARISH":
                trend_score = max(0, trend_score - 20)
        else:  # SELL
            if trend == "BEARISH":
                trend_score = 90
            elif trend == "BULLISH":
                trend_score = 20
            if btc_trend == "BEARISH":
                trend_score = min(100, trend_score + 20)
            elif btc_trend == "BULLISH":
                trend_score = max(0, trend_score - 20)

        score = score * 0.80 + trend_score * 0.20

        return round(max(0, min(100, score)), 1)

    def _determine_state(self, memory: PositionMemory, pnl_pct: float,
                        trend: str, btc_trend: str) -> PositionState:
        """Определить состояние позиции."""
        # Проверяем тезис
        if memory.side == "BUY":
            thesis_broken = trend == "BEARISH" and pnl_pct < -self.THESIS_BROKEN_THRESHOLD
        else:
            thesis_broken = trend == "BULLISH" and pnl_pct < -self.THESIS_BROKEN_THRESHOLD

        if thesis_broken:
            return PositionState.THESIS_COMPROMISED

        # RISK_ZONE
        if pnl_pct < -self.RISK_WARNING_THRESHOLD:
            return PositionState.RISK_ZONE

        # PROFIT_DECAYING
        if memory.profit_retracement_pct > self.PROFIT_DECAY_THRESHOLD and pnl_pct > 0:
            return PositionState.PROFIT_DECAYING

        # PROFIT_LOCKED (MFE > 2% и текущий PnL > 50% от MFE)
        if memory.max_profit_seen > 0.02 and pnl_pct > memory.max_profit_seen * 0.5:
            return PositionState.PROFIT_LOCKED

        # PROFIT_FORMING
        if pnl_pct > self.PROFIT_START_THRESHOLD:
            return PositionState.PROFIT_FORMING

        return PositionState.ENTRY_FORMING

    def _determine_recommendation(
        self,
        memory: PositionMemory,
        state: PositionState,
        pnl_pct: float,
    ) -> tuple[Recommendation, str]:
        """Определить рекомендацию."""
        if state == PositionState.THESIS_COMPROMISED:
            return Recommendation.EXIT_NOW, "Thesis broken — trend reversed against position"

        if state == PositionState.RISK_ZONE:
            return Recommendation.TIGHTEN_SL, f"Price {pnl_pct:+.2%} beyond risk threshold"

        if state == PositionState.PROFIT_DECAYING:
            if memory.profit_retracement_pct > 0.5:
                return Recommendation.TAKE_PARTIAL, f"Profit decay {memory.profit_retracement_pct:.0%} from peak"
            return Recommendation.MOVE_SL_BE, f"Protect profits — retrace {memory.profit_retracement_pct:.0%}"

        if state == PositionState.PROFIT_LOCKED:
            return Recommendation.MOVE_SL_BE, f"Lock profits — MFE {memory.max_profit_seen:.2%}"

        if state == PositionState.PROFIT_FORMING:
            return Recommendation.HOLD, f"Profit forming: {pnl_pct:+.2%}"

        return Recommendation.WAIT, "Entry forming — no action needed"

    def _log_event(self, position_id, symbol, side, entry_price, current_price,
                   pnl_pct, max_profit, max_loss, retrace, time_in_pos, bars,
                   state, health, recommendation, reason, event_type):
        """Записать событие в БД."""
        if not self._db_path:
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.execute("""
                INSERT INTO position_events
                (position_id, symbol, side, entry_price, current_price, pnl_pct,
                 max_profit_seen, max_loss_seen, profit_retracement, time_in_position,
                 bars_held, state, health_score, recommendation, reason, event_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (position_id, symbol, side, entry_price, current_price, pnl_pct,
                  max_profit, max_loss, retrace, time_in_pos, bars,
                  state, health, recommendation, reason, event_type))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to log event: {e}")

    def _save_summary(self, memory: PositionMemory, exit_price: float,
                     final_pnl: float, exit_reason: str):
        """Сохранить сводку по закрытой позиции."""
        if not self._db_path:
            return

        # Profit Capture Efficiency
        # = реализованная прибыль / MFE (если MFE > 0 и final_pnl >= 0)
        pce = None
        if memory.max_profit_seen > 0 and final_pnl >= 0:
            pce = final_pnl / memory.max_profit_seen if memory.max_profit_seen > 0 else 0
        elif memory.max_profit_seen > 0 and final_pnl < 0:
            # Был шанс на прибыль, но ушли в минус
            pce = 0.0  # эффективность 0 — упустили всю прибыль

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.execute("""
                INSERT OR REPLACE INTO position_summary
                (position_id, symbol, side, entry_price, exit_price,
                 max_profit_seen, max_loss_seen, profit_retracement_final,
                 profit_capture_efficiency,
                 time_in_position, bars_held, final_pnl_pct, exit_reason,
                 health_score_avg, events_count, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (memory.position_id, memory.symbol, memory.side,
                  memory.entry_price, exit_price,
                  memory.max_profit_seen, memory.max_loss_seen,
                  memory.profit_retracement_pct, pce,
                  time.time() - memory.entry_time, memory.bars_held,
                  final_pnl, exit_reason,
                  sum(e.get("health", 100) for e in memory.events) / max(len(memory.events), 1),
                  len(memory.events)))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save summary: {e}")

    # ── LIVE_ASSIST Methods ──────────────────────────────────

    def log_assist_recommendation(
        self,
        position_id: str,
        symbol: str,
        side: str,
        recommendation: str,
        price: float,
        pnl: float,
        mfe: float,
        health: float,
        reason: str,
    ):
        """
        Логировать рекомендацию LIVE_ASSIST.

        Вызывается когда PIE в режиме live_assist генерирует рекомендацию.
        Записывает контекст для последующего what-if анализа.
        """
        if self._mode != "live_assist":
            return

        if not self._db_path:
            return

        # Не логировать HOLD — только действия
        if recommendation in ("HOLD", "WAIT"):
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.execute("""
                INSERT INTO live_assist_log
                (position_id, symbol, side, recommendation,
                 price_at_recommendation, pnl_at_recommendation,
                 mfe_at_recommendation, health_at_recommendation, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (position_id, symbol, side, recommendation,
                  price, pnl, mfe, health, reason))
            conn.commit()
            conn.close()

            logger.info(
                f"[PIE ASSIST] {symbol} {side}: {recommendation} @ {price:.4f} "
                f"pnl={pnl:+.2%} mfe={mfe:+.2%} health={health:.0f} — {reason}"
            )
        except Exception as e:
            logger.error(f"Failed to log assist recommendation: {e}")

    def update_assist_results(self, position_id: str, current_price: float, current_pnl: float):
        """
        Обновить what-if результаты для рекомендаций.

        Вызывается каждую минуту для обновления цен после рекомендации.
        Также вычисляет Missed Opportunity — сколько прибыли потеряно/сохранено.
        """
        if self._mode != "live_assist":
            return

        if not self._db_path:
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)

            # Найти рекомендации, которые нужно обновить
            cursor = conn.execute("""
                SELECT id, timestamp_utc, price_at_recommendation,
                       pnl_at_recommendation, recommendation
                FROM live_assist_log
                WHERE position_id = ?
                AND price_after_60m IS NULL
                ORDER BY id DESC
                LIMIT 5
            """, (position_id,))

            now = time.time()
            from datetime import datetime
            for row in cursor.fetchall():
                log_id = row[0]
                rec_time_str = row[1]
                rec_price = row[2]
                rec_pnl = row[3]
                rec_type = row[4]

                # Парсим время рекомендации
                rec_time = datetime.fromisoformat(rec_time_str).timestamp()
                minutes_since = (now - rec_time) / 60

                # Missed Opportunity: разница между PnL сейчас и PnL в момент рекомендации
                # Если current_pnl > rec_pnl — мы "упустили" прибыль (надо было держать)
                # Если current_pnl < rec_pnl — мы правильно защитились
                missed = current_pnl - rec_pnl

                # Обновляем результаты
                if minutes_since >= 15:
                    conn.execute("""
                        UPDATE live_assist_log
                        SET price_after_15m = ?, pnl_after_15m = ?
                        WHERE id = ? AND price_after_15m IS NULL
                    """, (current_price, current_pnl, log_id))

                if minutes_since >= 30:
                    conn.execute("""
                        UPDATE live_assist_log
                        SET price_after_30m = ?, pnl_after_30m = ?
                        WHERE id = ? AND price_after_30m IS NULL
                    """, (current_price, current_pnl, log_id))

                if minutes_since >= 60:
                    # Финальная оценка качества
                    # Если PIE сказал "сократить/защитить", а цена выросла → quality=early
                    # Если PIE сказал "сократить/защитить", а цена упала → quality=correct
                    # Если PIE сказал "держать/ждать", а цена выросла → quality=correct
                    # Если PIE сказал "держать/ждать", а цена упала → quality=wrong
                    quality = None
                    notes = None

                    if rec_type in ("TAKE_PARTIAL", "MOVE_SL_BE"):
                        if missed > 0:
                            quality = "early"
                            notes = f"Price continued up: {missed*100:+.2f}% more available"
                        else:
                            quality = "correct"
                            notes = f"Protected: missed loss of {abs(missed)*100:.2f}%"
                    elif rec_type in ("HOLD", "EXIT_NOW"):
                        if missed > 0:
                            quality = "correct"
                            notes = f"Held correctly: {missed*100:+.2f}%"
                        else:
                            quality = "wrong"
                            notes = f"Should have acted: {abs(missed)*100:.2f}% loss"

                    conn.execute("""
                        UPDATE live_assist_log
                        SET price_after_60m = ?, pnl_after_60m = ?,
                            decision_quality = ?, quality_notes = ?
                        WHERE id = ? AND price_after_60m IS NULL
                    """, (current_price, current_pnl, quality, notes, log_id))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to update assist results: {e}")

    def get_assist_quality_report(self) -> Dict[str, Any]:
        """
        Отчёт о качестве рекомендаций LIVE_ASSIST.

        Показывает:
        - сколько рекомендаций было
        - какой результат через 15/30/60 минут
        - какие рекомендации были правильными
        """
        if not self._db_path:
            return {}

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)

            # Всего рекомендаций
            cursor = conn.execute("SELECT COUNT(*) FROM live_assist_log")
            total = cursor.fetchone()[0]

            if total == 0:
                conn.close()
                return {"total": 0, "message": "No recommendations yet"}

            # По типам рекомендаций
            cursor = conn.execute("""
                SELECT recommendation, COUNT(*),
                       AVG(pnl_at_recommendation),
                       AVG(pnl_after_15m),
                       AVG(pnl_after_30m),
                       AVG(pnl_after_60m)
                FROM live_assist_log
                GROUP BY recommendation
            """)
            by_type = {}
            for row in cursor.fetchall():
                by_type[row[0]] = {
                    "count": row[1],
                    "avg_pnl_at_rec": row[2],
                    "avg_pnl_15m": row[3],
                    "avg_pnl_30m": row[4],
                    "avg_pnl_60m": row[5],
                }

            # MOVE_SL_BE эффективность
            cursor = conn.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN pnl_after_15m > pnl_at_recommendation THEN 1 ELSE 0 END),
                       SUM(CASE WHEN pnl_after_30m > pnl_at_recommendation THEN 1 ELSE 0 END),
                       SUM(CASE WHEN pnl_after_60m > pnl_at_recommendation THEN 1 ELSE 0 END)
                FROM live_assist_log
                WHERE recommendation = 'MOVE_SL_BE'
            """)
            move_sl = cursor.fetchone()

            # TAKE_PARTIAL эффективность
            cursor = conn.execute("""
                SELECT COUNT(*),
                       AVG(pnl_at_recommendation - pnl_after_60m)
                FROM live_assist_log
                WHERE recommendation = 'TAKE_PARTIAL'
            """)
            take_partial = cursor.fetchone()

            conn.close()

            return {
                "total": total,
                "by_type": by_type,
                "move_sl_be": {
                    "count": move_sl[0],
                    "better_after_15m": move_sl[1],
                    "better_after_30m": move_sl[2],
                    "better_after_60m": move_sl[3],
                },
                "take_partial": {
                    "count": take_partial[0],
                    "avg_saved_vs_hold": take_partial[1],
                },
            }
        except Exception as e:
            logger.error(f"Failed to get assist quality report: {e}")
            return {"error": str(e)}
