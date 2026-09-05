"""
Optimizer v8.2 — Self-modifying parameter system.

Философия:
    Система должна менять себя на основе реальной производительности.
    
    v8.2 оптимизирует:
    - regime weights (веса режимов)
    - risk caps (лимиты риска)
    - entry threshold (порог входа)
    - execution filters (фильтры исполнения)
    - position sizing curve (кривая размера позиции)
    
    Ключевой принцип: медленная эволюция, а не быстрая адаптация.
    Изменения ≤ 5% за шаг.
"""
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import os

log = logging.getLogger("OptimizerV8")


@dataclass
class PerformanceSnapshot:
    """Снимок производительности для оптимизатора."""
    timestamp: datetime
    winrate: float
    winrate_trending: float
    winrate_ranging: float
    winrate_volatile: float
    winrate_low_liq: float
    drawdown: float
    sharpe: float
    avg_pnl_percent: float
    total_trades: int
    execution_slippage: float
    regime_distribution: Dict[str, int]


@dataclass
class OptimizerState:
    """Текущее состояние оптимизатора."""
    regime_weights: Dict[str, float] = field(default_factory=lambda: {
        "trending": 1.0,
        "ranging": 1.0,
        "high_volatility": 1.0,
        "low_liquidity": 1.0,
    })
    risk_cap: float = 1.0
    entry_threshold: float = 0.55
    execution_strictness: float = 1.0
    position_sizing_curve: str = "kelly"  # kelly, half_kelly, quarter_kelly
    max_position_fraction: float = 0.30
    last_update: Optional[datetime] = None


class OptimizerV8:
    """
    Optimizer v8.2 — Self-modifying parameter system.
    
    Изменяет параметры системы на основе реальной производительности.
    Медленная эволюция: изменения ≤ 5% за шаг.
    """
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/optimizer"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        self.state = OptimizerState()
        self.performance_log: List[PerformanceSnapshot] = []
        self.max_log = 200
        
        # Загружаем сохранённое состояние
        self._load_state()
    
    def update(self, snapshot: PerformanceSnapshot):
        """
        Обновить оптимизатор на основе снимка производительности.
        
        Args:
            snapshot: Снимок производительности
        """
        self.performance_log.append(snapshot)
        if len(self.performance_log) > self.max_log:
            self.performance_log = self.performance_log[-self.max_log:]
        
        # ========== UPDATE REGIME WEIGHTS ==========
        self._update_regime_weights(snapshot)
        
        # ========== UPDATE RISK CAP ==========
        self._update_risk_cap(snapshot)
        
        # ========== UPDATE ENTRY THRESHOLD ==========
        self._update_entry_threshold(snapshot)
        
        # ========== UPDATE EXECUTION STRICTNESS ==========
        self._update_execution_strictness(snapshot)
        
        # ========== UPDATE POSITION SIZING ==========
        self._update_position_sizing(snapshot)
        
        self.state.last_update = datetime.now()
        
        # Сохраняем состояние
        self._save_state()
        
        log.debug(
            f"Optimizer updated: threshold={self.state.entry_threshold:.2%} | "
            f"risk_cap={self.state.risk_cap:.0%} | "
            f"execution={self.state.execution_strictness:.0%}"
        )
    
    def get_adjustments(self) -> Dict:
        """
        Получить текущие корректировки для системы.
        
        Returns:
            Dict с корректировками параметров
        """
        return {
            "regime_weights": dict(self.state.regime_weights),
            "risk_cap": self.state.risk_cap,
            "entry_threshold": self.state.entry_threshold,
            "execution_strictness": self.state.execution_strictness,
            "position_sizing_curve": self.state.position_sizing_curve,
            "max_position_fraction": self.state.max_position_fraction,
            "last_update": self.state.last_update.isoformat() if self.state.last_update else None,
        }
    
    def reset(self):
        """Сбросить оптимизатор до начального состояния."""
        self.state = OptimizerState()
        self.performance_log = []
        self._save_state()
    
    # ==================== PRIVATE METHODS ====================
    
    def _update_regime_weights(self, snapshot: PerformanceSnapshot):
        """Обновить веса режимов."""
        regime_map = {
            "trending": snapshot.winrate_trending,
            "ranging": snapshot.winrate_ranging,
            "high_volatility": snapshot.winrate_volatile,
            "low_liquidity": snapshot.winrate_low_liq,
        }
        
        for regime, winrate in regime_map.items():
            if winrate < 0.45 and winrate > 0:
                # Снижаем вес режима на 5%
                self.state.regime_weights[regime] *= 0.95
            elif winrate > 0.60:
                # Увеличиваем вес режима на 2%
                self.state.regime_weights[regime] *= 1.02
            
            # Clamp
            self.state.regime_weights[regime] = max(
                0.3, min(1.5, self.state.regime_weights[regime])
            )
    
    def _update_risk_cap(self, snapshot: PerformanceSnapshot):
        """Обновить лимит риска."""
        if snapshot.drawdown > 0.10:
            # Просадка > 10% → снижаем risk cap на 10%
            self.state.risk_cap *= 0.90
        elif snapshot.drawdown > 0.05:
            # Просадка > 5% → снижаем на 5%
            self.state.risk_cap *= 0.95
        elif snapshot.drawdown < 0.02 and snapshot.winrate > 0.55:
            # Всё хорошо → можно немного увеличить
            self.state.risk_cap *= 1.02
        
        self.state.risk_cap = max(0.3, min(1.0, self.state.risk_cap))
    
    def _update_entry_threshold(self, snapshot: PerformanceSnapshot):
        """Обновить порог входа."""
        if snapshot.winrate < 0.40 and snapshot.total_trades > 10:
            # Низкий winrate → повышаем порог
            self.state.entry_threshold += 0.02
        elif snapshot.winrate > 0.60 and snapshot.total_trades > 10:
            # Высокий winrate → можно снизить порог
            self.state.entry_threshold -= 0.01
        
        self.state.entry_threshold = max(0.50, min(0.70, self.state.entry_threshold))
    
    def _update_execution_strictness(self, snapshot: PerformanceSnapshot):
        """Обновить строгость фильтров исполнения."""
        if snapshot.execution_slippage > 0.002:  # > 0.2%
            # Высокое проскальзывание → ужесточаем фильтры
            self.state.execution_strictness *= 1.05
        elif snapshot.execution_slippage < 0.0005:  # < 0.05%
            # Низкое проскальзывание → можно ослабить
            self.state.execution_strictness *= 0.98
        
        self.state.execution_strictness = max(
            0.5, min(2.0, self.state.execution_strictness)
        )
    
    def _update_position_sizing(self, snapshot: PerformanceSnapshot):
        """Обновить кривую размера позиции."""
        if snapshot.sharpe < 0.5 and snapshot.total_trades > 20:
            # Низкий sharpe → переходим на более консервативный sizing
            if self.state.position_sizing_curve == "kelly":
                self.state.position_sizing_curve = "half_kelly"
                self.state.max_position_fraction = 0.20
            elif self.state.position_sizing_curve == "half_kelly":
                self.state.position_sizing_curve = "quarter_kelly"
                self.state.max_position_fraction = 0.10
        elif snapshot.sharpe > 1.5 and snapshot.total_trades > 20:
            # Высокий sharpe → можно увеличить
            if self.state.position_sizing_curve == "quarter_kelly":
                self.state.position_sizing_curve = "half_kelly"
                self.state.max_position_fraction = 0.20
            elif self.state.position_sizing_curve == "half_kelly":
                self.state.position_sizing_curve = "kelly"
                self.state.max_position_fraction = 0.30
    
    def _save_state(self):
        """Сохранить состояние оптимизатора."""
        path = os.path.join(self.data_dir, "optimizer_state.json")
        try:
            with open(path, "w") as f:
                json.dump({
                    "regime_weights": self.state.regime_weights,
                    "risk_cap": self.state.risk_cap,
                    "entry_threshold": self.state.entry_threshold,
                    "execution_strictness": self.state.execution_strictness,
                    "position_sizing_curve": self.state.position_sizing_curve,
                    "max_position_fraction": self.state.max_position_fraction,
                    "last_update": self.state.last_update.isoformat() if self.state.last_update else None,
                }, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save optimizer state: {e}")
    
    def _load_state(self):
        """Загрузить состояние оптимизатора."""
        path = os.path.join(self.data_dir, "optimizer_state.json")
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                    self.state.regime_weights = data.get("regime_weights", self.state.regime_weights)
                    self.state.risk_cap = data.get("risk_cap", self.state.risk_cap)
                    self.state.entry_threshold = data.get("entry_threshold", self.state.entry_threshold)
                    self.state.execution_strictness = data.get("execution_strictness", self.state.execution_strictness)
                    self.state.position_sizing_curve = data.get("position_sizing_curve", self.state.position_sizing_curve)
                    self.state.max_position_fraction = data.get("max_position_fraction", self.state.max_position_fraction)
                    last = data.get("last_update")
                    if last:
                        self.state.last_update = datetime.fromisoformat(last)
        except Exception as e:
            log.error(f"Failed to load optimizer state: {e}")