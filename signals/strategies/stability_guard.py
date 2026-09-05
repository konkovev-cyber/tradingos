"""
Stability Guard v7.9 — Контроль поведения системы во времени.

Архитектура:
    EDGE DECISION → RISK STACK → POSITION SIZING → EXECUTION
                                            ↓
                                SYSTEM STABILITY GUARD (NEW 🔥)
                                            ↓
                        TRADE ALLOW / SUPPRESS / COOLDOWN

Философия:
- Решения зависят от состояния системы
- После серии убытков — замедление
- При нестабильности — cooldown
- Защита от overtrading
"""
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import deque
import json
import os

log = logging.getLogger("StabilityGuard")


@dataclass
class StabilityState:
    """Состояние стабильности системы."""
    volatility: float
    drawdown: float
    cooldown_state: str  # NORMAL, SLOW, COOLDOWN, PAUSE
    drift_state: str  # STABLE, WEAKENING, DEGRADED
    trade_frequency: int  # trades per hour
    stability_score: float
    can_trade: bool
    aggression_level: float  # 0.0-1.0
    reason: str


class EquitySmoother:
    """Сглаживание equity curve и расчёт волатильности."""
    
    def __init__(self, window: int = 20):
        self.equity: deque = deque(maxlen=window)
        self.peak: float = 0.0
    
    def update(self, value: float):
        """Обновить equity."""
        self.equity.append(value)
        
        if value > self.peak:
            self.peak = value
    
    def volatility(self) -> float:
        """Вычислить волатильность equity."""
        if len(self.equity) < 5:
            return 0.0
        
        values = list(self.equity)
        returns = [
            abs(values[i] - values[i-1])
            for i in range(1, len(values))
        ]
        
        return sum(returns) / len(returns) if returns else 0.0
    
    def drawdown(self) -> float:
        """Вычислить текущую просадку."""
        if self.peak == 0 or len(self.equity) == 0:
            return 0.0
        
        current = self.equity[-1] if self.equity else 0.0
        return (self.peak - current) / self.peak if self.peak > 0 else 0.0


class TradeThrottle:
    """Контроль частоты сделок."""
    
    def __init__(self, max_trades_per_hour: int = 10, min_interval_seconds: int = 30):
        self.max_trades_per_hour = max_trades_per_hour
        self.min_interval_seconds = min_interval_seconds
        self.trades: List[datetime] = []
    
    def can_trade(self) -> bool:
        """Проверить, можно ли торговать."""
        now = datetime.now()
        
        # Удаляем старые сделки (старше 1 часа)
        self.trades = [
            t for t in self.trades
            if (now - t).total_seconds() < 3600
        ]
        
        # Проверяем лимит
        if len(self.trades) >= self.max_trades_per_hour:
            return False
        
        # Проверяем минимальный интервал
        if self.trades:
            last_trade = self.trades[-1]
            if (now - last_trade).total_seconds() < self.min_interval_seconds:
                return False
        
        return True
    
    def register_trade(self):
        """Зарегистрировать сделку."""
        self.trades.append(datetime.now())
    
    def get_frequency(self) -> int:
        """Получить частоту сделок в час."""
        now = datetime.now()
        self.trades = [
            t for t in self.trades
            if (now - t).total_seconds() < 3600
        ]
        return len(self.trades)


class StrategyDriftDetector:
    """Детектор дрейфа стратегии."""
    
    def __init__(self, short_window: int = 10, long_window: int = 50):
        self.short_window = short_window
        self.long_window = long_window
        self.results: deque = deque(maxlen=long_window)
    
    def record_result(self, win: bool):
        """Записать результат сделки."""
        self.results.append(1 if win else 0)
    
    def detect_drift(self) -> str:
        """Обнаружить дрейф стратегии."""
        if len(self.results) < self.short_window:
            return "STABLE"
        
        # Win rate за короткий период
        recent = list(self.results)[-self.short_window:]
        recent_winrate = sum(recent) / len(recent)
        
        # Win rate за длинный период
        long_winrate = sum(self.results) / len(self.results)
        
        # Дрейф
        delta = recent_winrate - long_winrate
        
        if delta < -0.10:
            return "DEGRADED"
        elif delta < -0.05:
            return "WEAKENING"
        
        return "STABLE"


class StabilityGuard:
    """
    System Stability Guard v7.9.
    
    Контролирует поведение системы во времени:
    1. Equity Smoothing — волатильность equity curve
    2. Trade Throttle — контроль overtrading
    3. Cooldown State — замедление после убытков
    4. Drift Detection — обнаружение деградации стратегии
    
    Результат:
    - can_trade = True/False
    - aggression_level = 0.0-1.0
    - stability_score = 0.0-1.0
    """
    
    # ==================== COOLDOWN THRESHOLDS ====================
    
    DRAWDOWN_THRESHOLDS = {
        "NORMAL": 0.03,    # < 3%
        "SLOW": 0.07,      # < 7%
        "COOLDOWN": 0.12,  # < 12%
        "PAUSE": 1.0,      # >= 12%
    }
    
    # Aggression levels by state
    AGGRESSION_LEVELS = {
        "NORMAL": 1.0,
        "SLOW": 0.7,
        "COOLDOWN": 0.4,
        "PAUSE": 0.0,
    }
    
    # Volatility thresholds
    VOLATILITY_THRESHOLDS = {
        "LOW": 0.02,      # < 2%
        "NORMAL": 0.05,   # < 5%
        "HIGH": 0.10,     # < 10%
        "EXTREME": 1.0,   # >= 10%
    }
    
    def __init__(
        self,
        max_trades_per_hour: int = 10,
        min_trade_interval: int = 30,
        data_dir: str = "/opt/scalper_v6/data/stability"
    ):
        """Инициализация Stability Guard."""
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # Компоненты
        self.equity_smoother = EquitySmoother()
        self.trade_throttle = TradeThrottle(
            max_trades_per_hour=max_trades_per_hour,
            min_interval_seconds=min_trade_interval
        )
        self.drift_detector = StrategyDriftDetector()
        
        # Состояние
        self.last_check: datetime = datetime.now()
        self.suppressed_count: int = 0
    
    # ==================== MAIN API ====================
    
    def check_stability(
        self,
        current_equity: float,
        recent_win: Optional[bool] = None
    ) -> StabilityState:
        """
        Проверить стабильность системы.
        
        Args:
            current_equity: Текущий equity
            recent_win: Результат последней сделки (опционально)
            
        Returns:
            StabilityState с решением о торговле
        """
        # ========== UPDATE STATE ==========
        self.equity_smoother.update(current_equity)
        
        if recent_win is not None:
            self.drift_detector.record_result(recent_win)
        
        # ========== CALCULATE METRICS ==========
        volatility = self.equity_smoother.volatility()
        drawdown = self.equity_smoother.drawdown()
        drift_state = self.drift_detector.detect_drift()
        trade_frequency = self.trade_throttle.get_frequency()
        
        # ========== DETERMINE COOLDOWN STATE ==========
        cooldown_state = self._determine_cooldown_state(drawdown)
        aggression_level = self.AGGRESSION_LEVELS.get(cooldown_state, 0.4)
        
        # ========== CALCULATE STABILITY SCORE ==========
        stability_score = self._calculate_stability_score(
            volatility=volatility,
            cooldown_state=cooldown_state,
            drift_state=drift_state,
            trade_frequency=trade_frequency
        )
        
        # ========== CHECK THROTTLE ==========
        throttle_ok = self.trade_throttle.can_trade()
        
        # ========== MAKE DECISION ==========
        can_trade, reason = self._make_decision(
            stability_score=stability_score,
            cooldown_state=cooldown_state,
            drift_state=drift_state,
            throttle_ok=throttle_ok
        )
        
        # ========== LOG ==========
        if not can_trade:
            self.suppressed_count += 1
            log.debug(
                f"Stability Guard: SUPPRESSED — {reason} | "
                f"Vol={volatility:.2%} | DD={drawdown:.2%} | "
                f"Cooldown={cooldown_state} | Drift={drift_state}"
            )
        
        return StabilityState(
            volatility=volatility,
            drawdown=drawdown,
            cooldown_state=cooldown_state,
            drift_state=drift_state,
            trade_frequency=trade_frequency,
            stability_score=stability_score,
            can_trade=can_trade,
            aggression_level=aggression_level,
            reason=reason
        )
    
    def register_trade(self):
        """Зарегистрировать сделку."""
        self.trade_throttle.register_trade()
    
    def reset(self):
        """Сбросить состояние."""
        self.equity_smoother = EquitySmoother()
        self.trade_throttle = TradeThrottle()
        self.drift_detector = StrategyDriftDetector()
        self.suppressed_count = 0
    
    # ==================== PRIVATE METHODS ====================
    
    def _determine_cooldown_state(self, drawdown: float) -> str:
        """Определить состояние cooldown."""
        for state, threshold in self.DRAWDOWN_THRESHOLDS.items():
            if drawdown < threshold:
                return state
        return "PAUSE"
    
    def _calculate_stability_score(
        self,
        volatility: float,
        cooldown_state: str,
        drift_state: str,
        trade_frequency: int
    ) -> float:
        """Вычислить stability score."""
        score = 1.0
        
        # Volatility penalty
        if volatility > self.VOLATILITY_THRESHOLDS["EXTREME"]:
            score *= 0.3
        elif volatility > self.VOLATILITY_THRESHOLDS["HIGH"]:
            score *= 0.6
        elif volatility > self.VOLATILITY_THRESHOLDS["NORMAL"]:
            score *= 0.8
        
        # Cooldown penalty
        if cooldown_state == "SLOW":
            score *= 0.8
        elif cooldown_state == "COOLDOWN":
            score *= 0.5
        elif cooldown_state == "PAUSE":
            score *= 0.0
        
        # Drift penalty
        if drift_state == "WEAKENING":
            score *= 0.8
        elif drift_state == "DEGRADED":
            score *= 0.5
        
        # Overtrading penalty
        if trade_frequency > 8:
            score *= 0.7
        
        return score
    
    def _make_decision(
        self,
        stability_score: float,
        cooldown_state: str,
        drift_state: str,
        throttle_ok: bool
    ) -> tuple:
        """
        Принять решение о торговле.
        
        Returns:
            (can_trade, reason)
        """
        reasons = []
        
        # ========== CHECK 1: STABILITY SCORE ==========
        if stability_score < 0.3:
            reasons.append(f"stability={stability_score:.2f}<0.3")
            return False, " | ".join(reasons)
        
        # ========== CHECK 2: COOLDOWN STATE ==========
        if cooldown_state == "PAUSE":
            reasons.append("cooldown=PAUSE")
            return False, " | ".join(reasons)
        
        # ========== CHECK 3: DRIFT ==========
        if drift_state == "DEGRADED":
            # НЕ блокируем полностью, но снижаем агрессию
            reasons.append("drift=DEGRADED")
        
        # ========== CHECK 4: THROTTLE ==========
        if not throttle_ok:
            reasons.append("throttle=limit")
            return False, " | ".join(reasons)
        
        # ========== ALLOWED ==========
        reason = "OK" if not reasons else " | ".join(reasons)
        return True, reason