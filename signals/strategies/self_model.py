"""
System Self-Model Layer v8.5 — Система моделирует себя.

Философия:
    Финальный скачок — система начинает моделировать СВОЮ эффективность.
    
    v8.5 вводит:
    - edge_accuracy: rolling correlation(predicted_prob, actual_outcome)
    - risk_efficiency: pnl / max_drawdown
    - execution_drag: avg_slippage
    - regime_sensitivity: performance_by_regime
    
    Роль:
    - предсказывает свою эффективность
    - понимает где она "слепая"
    - корректирует optimizer v8.2
"""
import logging
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
import json
import os
import math

log = logging.getLogger("SelfModel")


@dataclass
class SelfModelState:
    """Состояние self-model."""
    edge_accuracy: float           # rolling correlation
    risk_efficiency: float          # pnl / max_drawdown
    execution_drag: float           # avg slippage
    regime_sensitivity: Dict[str, float]  # performance by regime
    blind_spots: List[str]          # где система слепая
    self_prediction_error: float    # ошибка предсказания себя
    overall_health: float           # 0.0-1.0
    last_update: Optional[datetime] = None


@dataclass
class PredictionRecord:
    """Запись предсказания vs реальности."""
    timestamp: datetime
    predicted_probability: float
    actual_outcome: bool  # True = win, False = loss
    regime: str
    symbol: str
    prediction_error: float  # |predicted - actual|


class SystemSelfModel:
    """
    System Self-Model Layer v8.5.
    
    Система моделирует свою эффективность:
    1. Edge Accuracy — насколько точны предсказания
    2. Risk Efficiency — pnl на единицу риска
    3. Execution Drag — потери на исполнении
    4. Regime Sensitivity — где система работает/не работает
    5. Blind Spot Detection — где система слепая
    """
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/self_model"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # История предсказаний
        self.predictions: deque = deque(maxlen=500)
        
        # История PnL
        self.pnl_history: deque = deque(maxlen=200)
        self.peak_equity: float = 0.0
        
        # История slippage
        self.slippage_history: deque = deque(maxlen=200)
        
        # Per-regime performance
        self.regime_performance: Dict[str, Dict] = {}
        
        # Состояние
        self.state = SelfModelState()
        
        # Загружаем
        self._load_state()
    
    def record_prediction(
        self,
        predicted_probability: float,
        actual_outcome: bool,
        regime: str,
        symbol: str
    ):
        """
        Записать предсказание и реальный исход.
        
        Args:
            predicted_probability: Предсказанная вероятность (0.0-1.0)
            actual_outcome: Реальный исход (True = win)
            regime: Режим рынка
            symbol: Торговый символ
        """
        # Prediction error = |predicted - actual|
        actual_value = 1.0 if actual_outcome else 0.0
        prediction_error = abs(predicted_probability - actual_value)
        
        record = PredictionRecord(
            timestamp=datetime.now(),
            predicted_probability=predicted_probability,
            actual_outcome=actual_outcome,
            regime=regime,
            symbol=symbol,
            prediction_error=prediction_error
        )
        
        self.predictions.append(record)
        
        # Update regime performance
        if regime not in self.regime_performance:
            self.regime_performance[regime] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "prediction_errors": [],
            }
        
        perf = self.regime_performance[regime]
        perf["trades"] += 1
        if actual_outcome:
            perf["wins"] += 1
        else:
            perf["losses"] += 1
        perf["prediction_errors"].append(prediction_error)
        
        # Keep only last 100 errors per regime
        if len(perf["prediction_errors"]) > 100:
            perf["prediction_errors"] = perf["prediction_errors"][-100:]
    
    def record_trade_result(self, pnl: float):
        """Записать результат сделки."""
        self.pnl_history.append(pnl)
        
        if self.peak_equity == 0:
            self.peak_equity = 100.0  # начальный equity
        
        current_equity = self.peak_equity + sum(self.pnl_history)
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
    
    def record_slippage(self, slippage: float):
        """Записать проскальзывание."""
        self.slippage_history.append(slippage)
    
    def update(self) -> SelfModelState:
        """Обновить self-model."""
        
        # ========== 1. EDGE ACCURACY ==========
        edge_accuracy = self._compute_edge_accuracy()
        
        # ========== 2. RISK EFFICIENCY ==========
        risk_efficiency = self._compute_risk_efficiency()
        
        # ========== 3. EXECUTION DRAG ==========
        execution_drag = self._compute_execution_drag()
        
        # ========== 4. REGIME SENSITIVITY ==========
        regime_sensitivity = self._compute_regime_sensitivity()
        
        # ========== 5. BLIND SPOTS ==========
        blind_spots = self._detect_blind_spots()
        
        # ========== 6. SELF PREDICTION ERROR ==========
        self_prediction_error = self._compute_self_prediction_error()
        
        # ========== 7. OVERALL HEALTH ==========
        overall_health = self._compute_overall_health(
            edge_accuracy,
            risk_efficiency,
            execution_drag,
            self_prediction_error
        )
        
        self.state = SelfModelState(
            edge_accuracy=edge_accuracy,
            risk_efficiency=risk_efficiency,
            execution_drag=execution_drag,
            regime_sensitivity=regime_sensitivity,
            blind_spots=blind_spots,
            self_prediction_error=self_prediction_error,
            overall_health=overall_health,
            last_update=datetime.now()
        )
        
        self._save_state()
        
        log.debug(
            f"SelfModel: health={overall_health:.0%} | "
            f"edge_acc={edge_accuracy:.0%} | "
            f"risk_eff={risk_efficiency:.2f} | "
            f"blind_spots={len(blind_spots)}"
        )
        
        return self.state
    
    def get_optimizer_feedback(self) -> Dict:
        """
        Получить обратную связь для optimizer v8.2.
        
        Returns:
            Dict с рекомендациями для оптимизатора
        """
        feedback = {}
        
        # Если edge accuracy низкая → нужно повысить порог
        if self.state.edge_accuracy < 0.5 and len(self.predictions) > 20:
            feedback["raise_threshold"] = True
            feedback["threshold_boost"] = 0.02
        
        # Если execution drag высокий → ужесточить фильтры
        if self.state.execution_drag > 0.002:
            feedback["tighten_execution"] = True
        
        # Если risk efficiency низкая → снизить risk cap
        if self.state.risk_efficiency < 0.5:
            feedback["reduce_risk_cap"] = True
        
        # Если есть blind spots → снизить вес проблемных режимов
        if self.state.blind_spots:
            feedback["blind_regimes"] = self.state.blind_spots
        
        return feedback
    
    def predict_effectiveness(
        self,
        regime: str,
        probability: float
    ) -> Dict:
        """
        Предсказать эффективность сделки.
        
        Args:
            regime: Текущий режим
            probability: Вероятность сигнала
            
        Returns:
            Dict с предсказанием:
            - expected_accuracy: ожидаемая точность
            - confidence: уверенность в предсказании
            - recommendation: рекомендация
        """
        # Базовая точность
        base_accuracy = self.state.edge_accuracy
        
        # Корректировка по режиму
        regime_perf = self.regime_performance.get(regime, {})
        regime_trades = regime_perf.get("trades", 0)
        regime_winrate = (
            regime_perf.get("wins", 0) / regime_trades
            if regime_trades > 0 else 0.5
        )
        
        # Если в режиме мало сделок — низкая уверенность
        confidence = min(1.0, regime_trades / 20)
        
        # Ожидаемая точность
        expected_accuracy = (
            0.6 * base_accuracy +
            0.4 * regime_winrate
        )
        
        # Рекомендация
        if expected_accuracy < 0.4:
            recommendation = "SKIP"
        elif expected_accuracy < 0.55:
            recommendation = "CAUTIOUS"
        else:
            recommendation = "PROCEED"
        
        return {
            "expected_accuracy": expected_accuracy,
            "confidence": confidence,
            "recommendation": recommendation,
            "regime_trades": regime_trades,
            "regime_winrate": regime_winrate,
        }
    
    def get_state(self) -> SelfModelState:
        """Получить текущее состояние."""
        return self.state
    
    def reset(self):
        """Сбросить self-model."""
        self.predictions.clear()
        self.pnl_history.clear()
        self.slippage_history.clear()
        self.regime_performance.clear()
        self.peak_equity = 0.0
        self.state = SelfModelState()
        self._save_state()
    
    # ==================== PRIVATE METHODS ====================
    
    def _compute_edge_accuracy(self) -> float:
        """Вычислить точность edge (rolling correlation)."""
        if len(self.predictions) < 10:
            return 0.5  # недостаточно данных
        
        records = list(self.predictions)[-50:]  # последние 50
        
        # Средняя ошибка предсказания
        avg_error = sum(r.prediction_error for r in records) / len(records)
        
        # Accuracy = 1 - avg_error
        accuracy = 1.0 - avg_error
        
        return max(0.0, min(1.0, accuracy))
    
    def _compute_risk_efficiency(self) -> float:
        """Вычислить risk efficiency (PnL / max_drawdown)."""
        if len(self.pnl_history) < 5:
            return 1.0
        
        total_pnl = sum(self.pnl_history)
        
        # Max drawdown
        running_max = 0.0
        max_dd = 0.0
        cumulative = 0.0
        
        for pnl in self.pnl_history:
            cumulative += pnl
            if cumulative > running_max:
                running_max = cumulative
            dd = running_max - cumulative
            if dd > max_dd:
                max_dd = dd
        
        if max_dd == 0:
            return 1.0
        
        # Risk efficiency = total_pnl / max_dd
        efficiency = total_pnl / max_dd if max_dd > 0 else 1.0
        
        # Normalize: 0.5 = breakeven, 1.0 = 2:1, 0.0 = negative
        normalized = 0.5 + (efficiency - 0.5) / 2
        return max(0.0, min(1.0, normalized))
    
    def _compute_execution_drag(self) -> float:
        """Вычислить execution drag (avg slippage)."""
        if not self.slippage_history:
            return 0.0
        
        return sum(self.slippage_history) / len(self.slippage_history)
    
    def _compute_regime_sensitivity(self) -> Dict[str, float]:
        """Вычислить чувствительность к режимам."""
        sensitivity = {}
        
        for regime, perf in self.regime_performance.items():
            if perf["trades"] < 3:
                sensitivity[regime] = 0.5  # недостаточно данных
                continue
            
            winrate = perf["wins"] / perf["trades"]
            sensitivity[regime] = winrate
        
        return sensitivity
    
    def _detect_blind_spots(self) -> List[str]:
        """Обнаружить слепые зоны."""
        blind_spots = []
        
        for regime, perf in self.regime_performance.items():
            if perf["trades"] < 5:
                continue
            
            winrate = perf["wins"] / perf["trades"]
            
            # Winrate < 40% = blind spot
            if winrate < 0.40:
                blind_spots.append(regime)
            
            # High prediction error = blind spot
            if perf["prediction_errors"]:
                avg_error = sum(perf["prediction_errors"]) / len(perf["prediction_errors"])
                if avg_error > 0.4:
                    blind_spots.append(f"{regime}_unpredictable")
        
        return blind_spots
    
    def _compute_self_prediction_error(self) -> float:
        """Вычислить ошибку предсказания себя."""
        if len(self.predictions) < 20:
            return 0.0
        
        records = list(self.predictions)[-20:]
        errors = [r.prediction_error for r in records]
        
        return sum(errors) / len(errors)
    
    def _compute_overall_health(
        self,
        edge_accuracy: float,
        risk_efficiency: float,
        execution_drag: float,
        self_prediction_error: float
    ) -> float:
        """Вычислить общее здоровье системы."""
        health = 1.0
        
        # Edge accuracy (вес 0.35)
        health *= (0.65 + 0.35 * edge_accuracy)
        
        # Risk efficiency (вес 0.25)
        health *= (0.75 + 0.25 * risk_efficiency)
        
        # Execution drag (вес 0.20, inverse)
        drag_penalty = min(1.0, execution_drag * 100)
        health *= (0.80 + 0.20 * (1.0 - drag_penalty))
        
        # Self prediction error (вес 0.20, inverse)
        error_penalty = min(1.0, self_prediction_error * 2)
        health *= (0.80 + 0.20 * (1.0 - error_penalty))
        
        return max(0.0, min(1.0, health))
    
    def _save_state(self):
        """Сохранить состояние."""
        path = os.path.join(self.data_dir, "self_model_state.json")
        try:
            with open(path, "w") as f:
                json.dump({
                    "edge_accuracy": self.state.edge_accuracy,
                    "risk_efficiency": self.state.risk_efficiency,
                    "execution_drag": self.state.execution_drag,
                    "regime_sensitivity": self.state.regime_sensitivity,
                    "blind_spots": self.state.blind_spots,
                    "self_prediction_error": self.state.self_prediction_error,
                    "overall_health": self.state.overall_health,
                    "last_update": self.state.last_update.isoformat() if self.state.last_update else None,
                }, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save self-model state: {e}")
    
    def _load_state(self):
        """Загрузить состояние."""
        path = os.path.join(self.data_dir, "self_model_state.json")
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                    self.state.edge_accuracy = data.get("edge_accuracy", 0.5)
                    self.state.risk_efficiency = data.get("risk_efficiency", 1.0)
                    self.state.execution_drag = data.get("execution_drag", 0.0)
                    self.state.regime_sensitivity = data.get("regime_sensitivity", {})
                    self.state.blind_spots = data.get("blind_spots", [])
                    self.state.self_prediction_error = data.get("self_prediction_error", 0.0)
                    self.state.overall_health = data.get("overall_health", 1.0)
                    last = data.get("last_update")
                    if last:
                        self.state.last_update = datetime.fromisoformat(last)
        except Exception as e:
            log.error(f"Failed to load self-model state: {e}")