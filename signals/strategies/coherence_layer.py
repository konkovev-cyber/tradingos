"""
Coherence Layer v8.1 — Единый показатель согласованности системы.

Философия:
    Система имеет 3 независимых control plane:
    - EDGE (probability)
    - RISK (capital)
    - EXECUTION (timing)
    
    Проблема: они не знают друг о друге.
    
    v8.1 вводит coherence_score — единый показатель того,
    "насколько система сейчас в адекватном состоянии".
    
    НЕ влияет на:
    - probability
    - risk size
    - execution decision
    
    Влияет ТОЛЬКО на:
    - приоритизацию сигналов
    - reorder execution
    - delay entry при низкой согласованности
"""
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
import json
import os

log = logging.getLogger("CoherenceLayer")


@dataclass
class CoherenceContext:
    """Контекст для вычисления coherence."""
    edge_confidence: float       # 0.0-1.0 (насколько уверен edge)
    regime_stability: float     # 0.0-1.0 (стабильность режима)
    execution_quality: float    # 0.0-1.0 (качество исполнения)
    portfolio_heat: float       # 0.0-1.0 (текущий нагрев портфеля)
    drawdown_pressure: float    # 0.0-1.0 (давление просадки)
    data_integrity: float       # 0.0-1.0 (качество данных)
    trade_frequency: float      # сделок/час (нормализовано)
    regime_flip_rate: float     # 0.0-1.0 (частота смены режима)


@dataclass
class CoherenceResult:
    """Результат coherence check."""
    coherence_score: float      # 0.0-1.0
    state: str                  # HIGH, NORMAL, LOW, CRITICAL
    conflicts: List[str]        # список конфликтов
    recommendation: str         # рекомендация
    details: Dict


class CoherenceLayer:
    """
    Coherence Layer v8.1.
    
    Единый показатель согласованности всех control plane.
    
    coherence_score = f(
        edge_confidence,
        regime_stability,
        execution_quality,
        portfolio_heat,
        drawdown_pressure,
        data_integrity
    )
    """
    
    # Пороги coherence
    COHERENCE_THRESHOLDS = {
        "HIGH": 0.80,       # ≥ 0.80 — всё согласовано
        "NORMAL": 0.60,     # ≥ 0.60 — нормально
        "LOW": 0.40,        # ≥ 0.40 — конфликты
        "CRITICAL": 0.0,    # < 0.40 — критично
    }
    
    # Веса компонентов
    WEIGHTS = {
        "edge_confidence": 0.25,
        "regime_stability": 0.20,
        "execution_quality": 0.20,
        "portfolio_heat": 0.15,
        "drawdown_pressure": 0.10,
        "data_integrity": 0.10,
    }
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/coherence"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # История coherence
        self.history: List[float] = []
        self.max_history = 100
        
        # Последний результат
        self.last_result: Optional[CoherenceResult] = None
    
    def compute_coherence(self, context: CoherenceContext) -> CoherenceResult:
        """
        Вычислить coherence score.
        
        Args:
            context: Контекст со всеми метриками
            
        Returns:
            CoherenceResult с оценкой и рекомендациями
        """
        conflicts = []
        
        # ========== COMPUTE SCORE ==========
        score = 1.0
        
        # Edge confidence
        score *= self._weighted("edge_confidence", context.edge_confidence)
        
        # Regime stability
        score *= self._weighted("regime_stability", context.regime_stability)
        
        # Execution quality
        score *= self._weighted("execution_quality", context.execution_quality)
        
        # Portfolio heat (inverse — чем выше heat, тем ниже score)
        heat_factor = 1.0 - context.portfolio_heat
        score *= self._weighted("portfolio_heat", heat_factor)
        
        # Drawdown pressure (inverse)
        dd_factor = 1.0 - context.drawdown_pressure
        score *= self._weighted("drawdown_pressure", dd_factor)
        
        # Data integrity
        score *= self._weighted("data_integrity", context.data_integrity)
        
        # Clamp
        coherence_score = max(0.0, min(1.0, score))
        
        # ========== DETECT CONFLICTS ==========
        # Conflict 1: High edge + Low execution quality
        if context.edge_confidence > 0.7 and context.execution_quality < 0.5:
            conflicts.append(
                "edge_high+execution_low: сигнал есть, но исполнить нечем"
            )
        
        # Conflict 2: Low drawdown + High portfolio heat
        if context.drawdown_pressure > 0.5 and context.portfolio_heat > 0.6:
            conflicts.append(
                "drawdown+heat: просадка при перегретом портфеле"
            )
        
        # Conflict 3: High regime flips + High edge confidence
        if context.regime_flip_rate > 0.3 and context.edge_confidence > 0.7:
            conflicts.append(
                "regime_flips+edge: режим скачет, но edge уверен — недоверие"
            )
        
        # Conflict 4: High trade frequency + Low data integrity
        if context.trade_frequency > 0.7 and context.data_integrity < 0.6:
            conflicts.append(
                "overtrading+bad_data: много сделок на плохих данных"
            )
        
        # ========== DETERMINE STATE ==========
        state = self._determine_state(coherence_score)
        
        # ========== GENERATE RECOMMENDATION ==========
        recommendation = self._generate_recommendation(
            coherence_score, state, conflicts
        )
        
        # ========== STORE ==========
        self.history.append(coherence_score)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        
        result = CoherenceResult(
            coherence_score=coherence_score,
            state=state,
            conflicts=conflicts,
            recommendation=recommendation,
            details={
                "edge_confidence": context.edge_confidence,
                "regime_stability": context.regime_stability,
                "execution_quality": context.execution_quality,
                "portfolio_heat": context.portfolio_heat,
                "drawdown_pressure": context.drawdown_pressure,
                "data_integrity": context.data_integrity,
                "trade_frequency": context.trade_frequency,
                "regime_flip_rate": context.regime_flip_rate,
            }
        )
        
        self.last_result = result
        
        log.debug(
            f"Coherence: {coherence_score:.2%} ({state}) | "
            f"Conflicts: {len(conflicts)}"
        )
        
        return result
    
    def get_execution_adjustment(self) -> Dict:
        """
        Получить корректировку для execution на основе coherence.
        
        Returns:
            Dict с корректировками:
            - delay_ms: задержка в мс
            - priority: приоритет сигнала
            - mode: NORMAL / CAUTIOUS / DEFENSIVE
        """
        if not self.last_result:
            return {"delay_ms": 0, "priority": 1.0, "mode": "NORMAL"}
        
        score = self.last_result.coherence_score
        
        if score >= 0.80:
            return {"delay_ms": 0, "priority": 1.0, "mode": "NORMAL"}
        elif score >= 0.60:
            return {"delay_ms": 100, "priority": 0.8, "mode": "CAUTIOUS"}
        elif score >= 0.40:
            return {"delay_ms": 300, "priority": 0.5, "mode": "DEFENSIVE"}
        else:
            return {"delay_ms": 1000, "priority": 0.2, "mode": "HOLD"}
    
    def get_coherence_trend(self) -> str:
        """Получить тренд coherence."""
        if len(self.history) < 5:
            return "STABLE"
        
        recent = self.history[-5:]
        if recent[-1] > recent[0] * 1.1:
            return "IMPROVING"
        elif recent[-1] < recent[0] * 0.9:
            return "DEGRADING"
        return "STABLE"
    
    def reset(self):
        """Сбросить состояние."""
        self.history = []
        self.last_result = None
    
    # ==================== PRIVATE METHODS ====================
    
    def _weighted(self, name: str, value: float) -> float:
        """Применить вес компонента."""
        weight = self.WEIGHTS.get(name, 0.1)
        return 1.0 - weight + weight * value
    
    def _determine_state(self, score: float) -> str:
        """Определить состояние coherence."""
        for state, threshold in sorted(
            self.COHERENCE_THRESHOLDS.items(),
            key=lambda x: -x[1]
        ):
            if score >= threshold:
                return state
        return "CRITICAL"
    
    def _generate_recommendation(
        self,
        score: float,
        state: str,
        conflicts: List[str]
    ) -> str:
        """Сгенерировать рекомендацию."""
        if state == "HIGH":
            return "Система согласована. Стандартный режим."
        
        if state == "NORMAL":
            return "Небольшие расхождения. Рекомендуется осторожность."
        
        if state == "LOW":
            base = "Система десинхронизирована. "
            if conflicts:
                base += f"Конфликты: {'; '.join(conflicts[:2])}. "
            base += "Рекомендуется снизить агрессию."
            return base
        
        return "Критическая десинхронизация. Рекомендуется остановка."
    
    def get_stats(self) -> Dict:
        """Получить статистику coherence."""
        if not self.history:
            return {"avg": 0.0, "min": 0.0, "max": 0.0, "trend": "STABLE"}
        
        return {
            "avg": sum(self.history) / len(self.history),
            "min": min(self.history),
            "max": max(self.history),
            "current": self.history[-1] if self.history else 0.0,
            "trend": self.get_coherence_trend(),
            "samples": len(self.history),
        }