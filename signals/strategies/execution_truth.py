"""
Execution Truth Layer v8.4 — Реальность исполнения vs ожидание.

Философия:
    У системы есть execution_engine, но нет понимания:
    "насколько реальное исполнение соответствует ожидаемому".
    
    v8.4 вводит:
    - slippage_delta (разница между ожидаемой и реальной ценой)
    - latency_penalty (штраф за задержку)
    - fill_quality_error (качество заполнения)
    
    Влияет на:
    - корректировку future probability (через optimizer)
    - корректировку risk sizing
    - feeds optimizer v8.2
"""
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
import json
import os

log = logging.getLogger("ExecutionTruth")


@dataclass
class ExecutionRecord:
    """Запись об исполнении сделки."""
    timestamp: datetime
    symbol: str
    direction: str
    expected_price: float
    real_fill_price: float
    slippage: float          # в %
    latency_ms: float        # задержка в мс
    fill_quality: float      # 0.0-1.0
    order_type: str          # MARKET, LIMIT, POST_ONLY
    regime: str
    probability: float


@dataclass
class ExecutionTruthState:
    """Состояние truth layer."""
    avg_slippage: float
    avg_latency: float
    avg_fill_quality: float
    execution_truth_score: float  # 0.0-1.0
    slippage_trend: str           # IMPROVING, STABLE, DEGRADING
    latency_trend: str
    fill_trend: str
    total_records: int


class ExecutionTruthLayer:
    """
    Execution Truth Layer v8.4.
    
    Отслеживает реальное качество исполнения и корректирует систему.
    """
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/execution_truth"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        self.records: deque = deque(maxlen=500)
        self.max_history = 500
        
        # Загружаем историю
        self._load_history()
    
    def record_execution(
        self,
        symbol: str,
        direction: str,
        expected_price: float,
        real_fill_price: float,
        latency_ms: float,
        order_type: str,
        regime: str,
        probability: float
    ):
        """
        Записать результат исполнения.
        
        Args:
            symbol: Торговый символ
            direction: BUY/SELL
            expected_price: Ожидаемая цена
            real_fill_price: Реальная цена заполнения
            latency_ms: Задержка в мс
            order_type: Тип ордера
            regime: Режим рынка
            probability: Вероятность сигнала
        """
        # Вычисляем slippage
        if expected_price > 0:
            slippage = abs(real_fill_price - expected_price) / expected_price
        else:
            slippage = 0.0
        
        # Вычисляем fill quality
        fill_quality = self._compute_fill_quality(
            slippage, latency_ms, order_type
        )
        
        record = ExecutionRecord(
            timestamp=datetime.now(),
            symbol=symbol,
            direction=direction,
            expected_price=expected_price,
            real_fill_price=real_fill_price,
            slippage=slippage,
            latency_ms=latency_ms,
            fill_quality=fill_quality,
            order_type=order_type,
            regime=regime,
            probability=probability
        )
        
        self.records.append(record)
        
        # Сохраняем
        self._save_history()
        
        log.debug(
            f"ExecutionTruth: {symbol} {direction} | "
            f"slippage={slippage:.3%} | "
            f"latency={latency_ms:.0f}ms | "
            f"fill={fill_quality:.0%}"
        )
    
    def get_truth_state(self) -> ExecutionTruthState:
        """Получить текущее состояние truth layer."""
        if not self.records:
            return ExecutionTruthState(
                avg_slippage=0.0,
                avg_latency=0.0,
                avg_fill_quality=1.0,
                execution_truth_score=1.0,
                slippage_trend="STABLE",
                latency_trend="STABLE",
                fill_trend="STABLE",
                total_records=0
            )
        
        records = list(self.records)
        
        # Средние значения
        avg_slippage = sum(r.slippage for r in records) / len(records)
        avg_latency = sum(r.latency_ms for r in records) / len(records)
        avg_fill = sum(r.fill_quality for r in records) / len(records)
        
        # Тренды (сравниваем последние 10 с предыдущими 10)
        recent = records[-10:] if len(records) >= 10 else records
        older = records[-20:-10] if len(records) >= 20 else records[:-10] if len(records) > 10 else []
        
        slippage_trend = self._compute_trend(
            [r.slippage for r in recent],
            [r.slippage for r in older] if older else []
        )
        latency_trend = self._compute_trend(
            [r.latency_ms for r in recent],
            [r.latency_ms for r in older] if older else []
        )
        fill_trend = self._compute_trend(
            [r.fill_quality for r in recent],
            [r.fill_quality for r in older] if older else [],
            inverse=True  # higher is better
        )
        
        # Execution truth score
        execution_truth_score = self._compute_truth_score(
            avg_slippage, avg_latency, avg_fill
        )
        
        return ExecutionTruthState(
            avg_slippage=avg_slippage,
            avg_latency=avg_latency,
            avg_fill_quality=avg_fill,
            execution_truth_score=execution_truth_score,
            slippage_trend=slippage_trend,
            latency_trend=latency_trend,
            fill_trend=fill_trend,
            total_records=len(records)
        )
    
    def get_execution_error(self) -> float:
        """
        Получить ошибку исполнения для корректировки.
        
        Returns:
            execution_error: 0.0-1.0 (0 = идеально, 1 = ужасно)
        """
        state = self.get_truth_state()
        return 1.0 - state.execution_truth_score
    
    def get_slippage_adjustment(self) -> float:
        """
        Получить корректировку для future probability.
        
        Returns:
            multiplier: 0.0-1.0 (корректировка probability)
        """
        state = self.get_truth_state()
        
        if state.total_records < 5:
            return 1.0
        
        # Если slippage > 0.2% → снижаем confidence
        if state.avg_slippage > 0.002:
            return max(0.5, 1.0 - state.avg_slippage * 50)
        
        return 1.0
    
    def get_latency_penalty(self) -> float:
        """
        Получить штраф за задержку.
        
        Returns:
            penalty: 0.0-1.0 (1.0 = нет штрафа)
        """
        state = self.get_truth_state()
        
        if state.total_records < 5:
            return 1.0
        
        # Если latency > 500ms → штраф
        if state.avg_latency > 500:
            return max(0.5, 1.0 - (state.avg_latency - 500) / 2000)
        
        return 1.0
    
    def get_regime_slippage(self, regime: str) -> float:
        """Получить средний slippage для режима."""
        records = [r for r in self.records if r.regime == regime]
        if not records:
            return 0.0
        return sum(r.slippage for r in records) / len(records)
    
    def reset(self):
        """Сбросить историю."""
        self.records.clear()
        self._save_history()
    
    # ==================== PRIVATE METHODS ====================
    
    def _compute_fill_quality(
        self,
        slippage: float,
        latency_ms: float,
        order_type: str
    ) -> float:
        """Вычислить качество заполнения."""
        quality = 1.0
        
        # Slippage penalty
        if slippage > 0.005:  # > 0.5%
            quality *= 0.3
        elif slippage > 0.002:  # > 0.2%
            quality *= 0.6
        elif slippage > 0.001:  # > 0.1%
            quality *= 0.8
        
        # Latency penalty
        if latency_ms > 1000:
            quality *= 0.5
        elif latency_ms > 500:
            quality *= 0.7
        elif latency_ms > 200:
            quality *= 0.9
        
        # Order type bonus
        if order_type == "LIMIT":
            quality *= 1.1  # limit orders get better fills
        elif order_type == "POST_ONLY":
            quality *= 1.2
        
        return max(0.0, min(1.0, quality))
    
    def _compute_truth_score(
        self,
        avg_slippage: float,
        avg_latency: float,
        avg_fill: float
    ) -> float:
        """Вычислить execution truth score."""
        score = 1.0
        
        # Slippage component
        if avg_slippage > 0.005:
            score *= 0.5
        elif avg_slippage > 0.002:
            score *= 0.7
        elif avg_slippage > 0.001:
            score *= 0.9
        
        # Latency component
        if avg_latency > 1000:
            score *= 0.6
        elif avg_latency > 500:
            score *= 0.8
        elif avg_latency > 200:
            score *= 0.95
        
        # Fill quality component
        score *= avg_fill
        
        return max(0.0, min(1.0, score))
    
    def _compute_trend(
        self,
        recent: List[float],
        older: List[float],
        inverse: bool = False
    ) -> str:
        """Вычислить тренд."""
        if not older:
            return "STABLE"
        
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        
        if inverse:
            # Higher is better
            if recent_avg > older_avg * 1.1:
                return "IMPROVING"
            elif recent_avg < older_avg * 0.9:
                return "DEGRADING"
        else:
            # Lower is better
            if recent_avg < older_avg * 0.9:
                return "IMPROVING"
            elif recent_avg > older_avg * 1.1:
                return "DEGRADING"
        
        return "STABLE"
    
    def _save_history(self):
        """Сохранить историю."""
        path = os.path.join(self.data_dir, "execution_history.json")
        try:
            records = list(self.records)[-100:]  # сохраняем последние 100
            data = [{
                "timestamp": r.timestamp.isoformat(),
                "symbol": r.symbol,
                "direction": r.direction,
                "expected_price": r.expected_price,
                "real_fill_price": r.real_fill_price,
                "slippage": r.slippage,
                "latency_ms": r.latency_ms,
                "fill_quality": r.fill_quality,
                "order_type": r.order_type,
                "regime": r.regime,
                "probability": r.probability,
            } for r in records]
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save execution history: {e}")
    
    def _load_history(self):
        """Загрузить историю."""
        path = os.path.join(self.data_dir, "execution_history.json")
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                    for item in data:
                        self.records.append(ExecutionRecord(
                            timestamp=datetime.fromisoformat(item["timestamp"]),
                            symbol=item["symbol"],
                            direction=item["direction"],
                            expected_price=item["expected_price"],
                            real_fill_price=item["real_fill_price"],
                            slippage=item["slippage"],
                            latency_ms=item["latency_ms"],
                            fill_quality=item["fill_quality"],
                            order_type=item["order_type"],
                            regime=item["regime"],
                            probability=item["probability"],
                        ))
        except Exception as e:
            log.error(f"Failed to load execution history: {e}")