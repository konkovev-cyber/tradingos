"""
Adaptive Learning Layer v7.6 — Самонастраивающаяся система.

Архитектура:
    LIVE SYSTEM → Trade Execution → Post-Trade Analytics
                                        ↓
                            FEATURE ATTRIBUTION ENGINE
                                        ↓
                            REGIME PERFORMANCE TRACKER
                                        ↓
                            COMPONENT PERFORMANCE TRACKER
                                        ↓
                            WEIGHT UPDATER (SAFE)
                                        ↓
                            NEXT CYCLE MODEL

Философия:
- НЕ online deep learning
- НЕ постоянный retrain модели
- Медленная, безопасная адаптация весов
- Пост-трейд анализ, а не предиктивное обучение
"""
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import json
import os

log = logging.getLogger("AdaptiveLearning")


@dataclass
class TradeResult:
    """Результат сделки для анализа."""
    symbol: str
    regime: str
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_percent: float
    probability: float
    execution_score: float
    position_size: float
    features: Dict
    timestamp: str
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "regime": self.regime,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "probability": self.probability,
            "execution_score": self.execution_score,
            "position_size": self.position_size,
            "features": self.features,
            "timestamp": self.timestamp
        }


@dataclass
class ComponentStats:
    """Статистика по компоненту."""
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_probability: float = 0.5
    
    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.5
    
    @property
    def sample_count(self) -> int:
        return self.wins + self.losses


class AdaptiveLearningEngine:
    """
    Adaptive Learning Layer v7.6.
    
    Анализирует результаты сделок и адаптирует веса:
    1. Feature Attribution — какие фичи работают
    2. Regime Performance — какие режимы прибыльные
    3. Component Performance — trend/setup/trigger scores
    4. Safe Weight Updater — медленная адаптация
    
    КРИТИЧЕСКИЕ ПРИНЦИПЫ:
    - learning = adjustment layer, NOT model replacement
    - Максимальное изменение веса: ±0.4 от базового
    - Минимум 10 сделок для адаптации
    - Вес ограничен [0.6, 1.4]
    """
    
    # ==================== BASE WEIGHTS ====================
    
    BASE_WEIGHTS = {
        "trend": 1.0,
        "setup": 1.0,
        "trigger": 1.0,
        "regime": 1.0,
        "execution": 1.0,
    }
    
    # ==================== FEATURES TO TRACK ====================
    
    TRACKED_FEATURES = [
        "adx",
        "rsi",
        "momentum",
        "volume_ratio",
        "volatility",
        "trend_alignment",
    ]
    
    # ==================== ADAPTATION PARAMS ====================
    
    MIN_SAMPLES = 10  # Минимум сделок для адаптации
    MAX_WEIGHT_DELTA = 0.4  # Максимальное изменение веса
    WEIGHT_MIN = 0.6  # Минимальный вес
    WEIGHT_MAX = 1.4  # Максимальный вес
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/learning"):
        """Инициализация Adaptive Learning Engine."""
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # Статистика
        self.regime_stats: Dict[str, ComponentStats] = {}
        self.feature_stats: Dict[str, Dict[str, ComponentStats]] = {}
        self.component_stats: Dict[str, ComponentStats] = {
            "trend": ComponentStats(),
            "setup": ComponentStats(),
            "trigger": ComponentStats(),
        }
        
        # Текущие веса
        self.current_weights = self.BASE_WEIGHTS.copy()
        
        # История сделок
        self.trade_history: List[TradeResult] = []
        
        # Загрузка истории
        self._load_history()
    
    # ==================== MAIN API ====================
    
    def record_trade(self, trade: TradeResult):
        """
        Записать результат сделки для анализа.
        
        Args:
            trade: TradeResult с данными о сделке
        """
        self.trade_history.append(trade)
        
        # Обновляем статистику по режимам
        self._update_regime_stats(trade)
        
        # Обновляем статистику по фичам
        self._update_feature_stats(trade)
        
        # Обновляем статистику по компонентам
        self._update_component_stats(trade)
        
        # Сохраняем
        self._save_trade(trade)
        
        log.debug(
            f"{trade.symbol}: Recorded trade — "
            f"PnL={trade.pnl_percent:+.2%} | "
            f"Regime={trade.regime} | "
            f"Prob={trade.probability:.2f}"
        )
    
    def adapt_weights(self) -> Dict[str, float]:
        """
        Адаптировать веса на основе статистики.
        
        Returns:
            Обновлённые веса
        """
        # ========== CHECK MINIMUM SAMPLES ==========
        total_trades = len(self.trade_history)
        
        if total_trades < self.MIN_SAMPLES:
            log.debug(f"Not enough samples ({total_trades} < {self.MIN_SAMPLES})")
            return self.current_weights
        
        # ========== ADAPT REGIME WEIGHTS ==========
        regime_weights = self._adapt_regime_weights()
        
        # ========== ADAPT FEATURE WEIGHTS ==========
        feature_weights = self._adapt_feature_weights()
        
        # ========== ADAPT COMPONENT WEIGHTS ==========
        component_weights = self._adapt_component_weights()
        
        # ========== MERGE WEIGHTS ==========
        new_weights = self.BASE_WEIGHTS.copy()
        
        # Regime weights влияют на все веса
        for key in ["trend", "setup", "trigger"]:
            new_weights[key] = self._clamp_weight(
                new_weights[key] * regime_weights.get(self._current_regime(), 1.0)
            )
        
        # Component weights
        for key in ["trend", "setup", "trigger"]:
            new_weights[key] = self._clamp_weight(
                new_weights[key] * component_weights.get(key, 1.0)
            )
        
        # ========== APPLY WEIGHTS ==========
        old_weights = self.current_weights.copy()
        self.current_weights = new_weights
        
        # ========== LOG ==========
        log.info(
            f"Weights adapted: "
            f"trend {old_weights['trend']:.2f}→{new_weights['trend']:.2f} | "
            f"setup {old_weights['setup']:.2f}→{new_weights['setup']:.2f} | "
            f"trigger {old_weights['trigger']:.2f}→{new_weights['trigger']:.2f}"
        )
        
        return new_weights
    
    def get_regime_performance(self) -> Dict[str, Dict]:
        """Получить статистику по режимам."""
        return {
            regime: {
                "win_rate": stats.win_rate,
                "total_pnl": stats.total_pnl,
                "sample_count": stats.sample_count,
            }
            for regime, stats in self.regime_stats.items()
        }
    
    def get_feature_effectiveness(self) -> Dict[str, Dict]:
        """Получить статистику по фичам."""
        result = {}
        for regime, features in self.feature_stats.items():
            result[regime] = {
                feature: {
                    "win_rate": stats.win_rate,
                    "avg_probability": stats.avg_probability,
                }
                for feature, stats in features.items()
            }
        return result
    
    # ==================== PRIVATE METHODS ====================
    
    def _update_regime_stats(self, trade: TradeResult):
        """Обновить статистику по режимам."""
        regime = trade.regime
        
        if regime not in self.regime_stats:
            self.regime_stats[regime] = ComponentStats()
        
        stats = self.regime_stats[regime]
        
        if trade.pnl > 0:
            stats.wins += 1
        else:
            stats.losses += 1
        
        stats.total_pnl += trade.pnl
    
    def _update_feature_stats(self, trade: TradeResult):
        """Обновить статистику по фичам."""
        regime = trade.regime
        
        if regime not in self.feature_stats:
            self.feature_stats[regime] = {}
        
        for feature, value in trade.features.items():
            if feature not in self.TRACKED_FEATURES:
                continue
            
            if feature not in self.feature_stats[regime]:
                self.feature_stats[regime][feature] = ComponentStats()
            
            stats = self.feature_stats[regime][feature]
            
            if trade.pnl > 0:
                stats.wins += 1
            else:
                stats.losses += 1
            
            stats.total_pnl += trade.pnl
    
    def _update_component_stats(self, trade: TradeResult):
        """Обновить статистику по компонентам."""
        # Trend component
        if "trend_score" in trade.features:
            if trade.pnl > 0:
                self.component_stats["trend"].wins += 1
            else:
                self.component_stats["trend"].losses += 1
        
        # Setup component
        if "setup_score" in trade.features:
            if trade.pnl > 0:
                self.component_stats["setup"].wins += 1
            else:
                self.component_stats["setup"].losses += 1
        
        # Trigger component
        if "trigger_score" in trade.features:
            if trade.pnl > 0:
                self.component_stats["trigger"].wins += 1
            else:
                self.component_stats["trigger"].losses += 1
    
    def _adapt_regime_weights(self) -> Dict[str, float]:
        """Адаптировать веса по режимам."""
        weights = {}
        
        for regime, stats in self.regime_stats.items():
            if stats.sample_count < self.MIN_SAMPLES:
                weights[regime] = 1.0
                continue
            
            # Win rate влияет на вес
            win_rate = stats.win_rate
            avg_pnl = stats.total_pnl / stats.sample_count
            
            # Выигрышный режим → усиливаем
            if win_rate > 0.55 and avg_pnl > 0:
                weights[regime] = 1.0 + min(0.2, (win_rate - 0.5) * 0.4)
            # Проигрышный режим → ослабляем
            elif win_rate < 0.45 or avg_pnl < 0:
                weights[regime] = 0.8
            else:
                weights[regime] = 1.0
        
        return weights
    
    def _adapt_feature_weights(self) -> Dict[str, float]:
        """Адаптировать веса по фичам."""
        weights = {}
        
        for feature in self.TRACKED_FEATURES:
            total_wins = 0
            total_samples = 0
            
            for regime, features in self.feature_stats.items():
                if feature in features:
                    stats = features[feature]
                    total_wins += stats.wins
                    total_samples += stats.sample_count
            
            if total_samples < self.MIN_SAMPLES:
                weights[feature] = 1.0
                continue
            
            win_rate = total_wins / total_samples
            
            # Фича с хорошим win rate → усиливаем
            if win_rate > 0.55:
                weights[feature] = 1.0 + min(0.2, (win_rate - 0.5) * 0.4)
            elif win_rate < 0.45:
                weights[feature] = 0.8
            else:
                weights[feature] = 1.0
        
        return weights
    
    def _adapt_component_weights(self) -> Dict[str, float]:
        """Адаптировать веса по компонентам."""
        weights = {}
        
        for component, stats in self.component_stats.items():
            if stats.sample_count < self.MIN_SAMPLES:
                weights[component] = 1.0
                continue
            
            win_rate = stats.win_rate
            
            # Компонент с хорошим win rate → усиливаем
            if win_rate > 0.55:
                weights[component] = 1.0 + min(0.2, (win_rate - 0.5) * 0.4)
            elif win_rate < 0.45:
                weights[component] = 0.8
            else:
                weights[component] = 1.0
        
        return weights
    
    def _clamp_weight(self, weight: float) -> float:
        """Ограничить вес."""
        return max(self.WEIGHT_MIN, min(self.WEIGHT_MAX, weight))
    
    def _current_regime(self) -> str:
        """Получить текущий режим (из последней сделки)."""
        if not self.trade_history:
            return "normal"
        return self.trade_history[-1].regime
    
    def _save_trade(self, trade: TradeResult):
        """Сохранить сделку в файл."""
        filename = os.path.join(self.data_dir, f"trades_{datetime.now().strftime('%Y%m')}.jsonl")
        
        with open(filename, "a") as f:
            f.write(json.dumps(trade.to_dict()) + "\n")
    
    def _load_history(self):
        """Загрузить историю из файлов."""
        try:
            # Загружаем последний файл
            files = sorted([
                f for f in os.listdir(self.data_dir)
                if f.startswith("trades_") and f.endswith(".jsonl")
            ], reverse=True)
            
            if not files:
                return
            
            latest_file = os.path.join(self.data_dir, files[0])
            
            with open(latest_file, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        trade = TradeResult(**data)
                        self.trade_history.append(trade)
                        
                        # Восстанавливаем статистику
                        self._update_regime_stats(trade)
                        self._update_feature_stats(trade)
                        
                    except Exception as e:
                        log.warning(f"Failed to load trade: {e}")
            
            log.info(f"Loaded {len(self.trade_history)} trades from history")
            
        except Exception as e:
            log.warning(f"Failed to load history: {e}")