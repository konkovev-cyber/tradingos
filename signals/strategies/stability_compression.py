"""
Stability Compression v8.3 — Гашение режимных скачков и overtrading.

Философия:
    Система может быть слишком реактивной.
    v8.3 = стабилизатор поведения системы.
    
    Убирает:
    - overtrading (слишком частые сделки)
    - regime flips (скачки режима)
    - signal noise (шум сигналов)
    
    Влияет ТОЛЬКО на:
    - entry_threshold (повышает при хаосе)
    - leverage (снижает при нестабильности)
    - asset freeze (замораживает не-основные активы)
"""
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
import json
import os

log = logging.getLogger("StabilityCompression")


@dataclass
class CompressionState:
    """Состояние компрессора стабильности."""
    entry_threshold_boost: float = 0.0     # дополнительный % к порогу
    leverage_reduction: float = 1.0        # множитель плеча
    frozen_assets: List[str] = field(default_factory=list)
    compression_mode: str = "NONE"         # NONE, LIGHT, MODERATE, HEAVY
    reason: str = "OK"


class StabilityCompression:
    """
    Stability Compression v8.3.
    
    Стабилизатор поведения системы:
    1. Trade Frequency Dampener — гасит overtrading
    2. Regime Flip Stabilizer — гасит режимные скачки
    3. Signal Noise Filter — фильтрует шум
    4. Asset Freeze — замораживает не-основные активы
    """
    
    # Пороги частоты сделок
    FREQUENCY_THRESHOLDS = {
        "NORMAL": 6,     # < 6 сделок/час
        "ELEVATED": 10,  # < 10 сделок/час
        "HIGH": 15,      # < 15 сделок/час
        "EXTREME": 100,  # >= 15 сделок/час
    }
    
    # Пороги смены режима
    REGIME_FLIP_THRESHOLDS = {
        "STABLE": 2,     # < 2 смен/час
        "FLIPPING": 4,  # < 4 смен/час
        "ERRATIC": 10,   # < 10 смен/час
        "CHAOTIC": 100,  # >= 10 смен/час
    }
    
    # Компрессия по режимам
    COMPRESSION_LEVELS = {
        "NONE": {"threshold_boost": 0.0, "leverage": 1.0, "freeze": False},
        "LIGHT": {"threshold_boost": 0.01, "leverage": 0.85, "freeze": False},
        "MODERATE": {"threshold_boost": 0.03, "leverage": 0.65, "freeze": True},
        "HEAVY": {"threshold_boost": 0.05, "leverage": 0.40, "freeze": True},
    }
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/compression"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # История
        self.trade_timestamps: deque = deque(maxlen=100)
        self.regime_changes: deque = deque(maxlen=50)
        self.signal_scores: deque = deque(maxlen=100)
        
        # Core assets (не замораживаются)
        self.core_assets: List[str] = [
            "BTCUSDT", "ETHUSDT", "SOLUSDT"
        ]
        
        # Состояние
        self.state = CompressionState()
        self.last_check: Optional[datetime] = None
    
    def compress(
        self,
        trade_frequency: int,
        regime_flips_last_hour: int,
        signal_noise: float,
        current_assets: List[str],
        regime: str
    ) -> CompressionState:
        """
        Применить компрессию стабильности.
        
        Args:
            trade_frequency: Сделок в час
            regime_flips_last_hour: Смен режима за час
            signal_noise: Уровень шума сигналов (0.0-1.0)
            current_assets: Текущие активы в портфеле
            regime: Текущий режим рынка
            
        Returns:
            CompressionState с корректировками
        """
        reasons = []
        
        # ========== CHECK 1: TRADE FREQUENCY ==========
        freq_state = self._classify_frequency(trade_frequency)
        if freq_state in ("HIGH", "EXTREME"):
            reasons.append(f"frequency={freq_state}({trade_frequency}/h)")
        
        # ========== CHECK 2: REGIME FLIPS ==========
        flip_state = self._classify_flips(regime_flips_last_hour)
        if flip_state in ("ERRATIC", "CHAOTIC"):
            reasons.append(f"flips={flip_state}({regime_flips_last_hour}/h)")
        
        # ========== CHECK 3: SIGNAL NOISE ==========
        noise_state = self._classify_noise(signal_noise)
        if noise_state in ("HIGH", "EXTREME"):
            reasons.append(f"noise={noise_state}({signal_noise:.2f})")
        
        # ========== DETERMINE COMPRESSION LEVEL ==========
        compression_level = self._determine_compression(
            freq_state, flip_state, noise_state
        )
        
        level_config = self.COMPRESSION_LEVELS.get(compression_level, self.COMPRESSION_LEVELS["NONE"])
        
        # ========== BUILD STATE ==========
        self.state = CompressionState(
            entry_threshold_boost=level_config["threshold_boost"],
            leverage_reduction=level_config["leverage"],
            frozen_assets=self._get_frozen_assets(
                current_assets, level_config["freeze"]
            ),
            compression_mode=compression_level,
            reason=" | ".join(reasons) if reasons else "OK"
        )
        
        self.last_check = datetime.now()
        
        if compression_level != "NONE":
            log.debug(
                f"StabilityCompression: {compression_level} | "
                f"threshold+{level_config['threshold_boost']:.0%} | "
                f"leverage={level_config['leverage']:.0%} | "
                f"frozen={len(self.state.frozen_assets)} assets"
            )
        
        return self.state
    
    def register_trade(self):
        """Зарегистрировать сделку."""
        self.trade_timestamps.append(datetime.now())
    
    def register_regime_change(self):
        """Зарегистрировать смену режима."""
        self.regime_changes.append(datetime.now())
    
    def register_signal(self, score: float):
        """Зарегистрировать сигнал."""
        self.signal_scores.append(score)
    
    def get_trade_frequency(self) -> int:
        """Получить частоту сделок в час."""
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        return sum(1 for t in self.trade_timestamps if t > cutoff)
    
    def get_regime_flips(self) -> int:
        """Получить количество смен режима за час."""
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        return sum(1 for t in self.regime_changes if t > cutoff)
    
    def get_signal_noise(self) -> float:
        """Получить уровень шума сигналов."""
        if len(self.signal_scores) < 10:
            return 0.0
        
        scores = list(self.signal_scores)[-20:]
        if not scores:
            return 0.0
        
        # Noise = std / mean
        mean = sum(scores) / len(scores)
        if mean == 0:
            return 0.0
        
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5
        
        return min(1.0, std / mean)
    
    def reset(self):
        """Сбросить состояние."""
        self.trade_timestamps.clear()
        self.regime_changes.clear()
        self.signal_scores.clear()
        self.state = CompressionState()
    
    # ==================== PRIVATE METHODS ====================
    
    def _classify_frequency(self, freq: int) -> str:
        """Классифицировать частоту сделок."""
        for state, threshold in sorted(
            self.FREQUENCY_THRESHOLDS.items(),
            key=lambda x: x[1]
        ):
            if freq < threshold:
                return state
        return "EXTREME"
    
    def _classify_flips(self, flips: int) -> str:
        """Классифицировать смены режима."""
        for state, threshold in sorted(
            self.REGIME_FLIP_THRESHOLDS.items(),
            key=lambda x: x[1]
        ):
            if flips < threshold:
                return state
        return "CHAOTIC"
    
    def _classify_noise(self, noise: float) -> str:
        """Классифицировать уровень шума."""
        if noise < 0.2:
            return "LOW"
        elif noise < 0.4:
            return "NORMAL"
        elif noise < 0.6:
            return "ELEVATED"
        elif noise < 0.8:
            return "HIGH"
        return "EXTREME"
    
    def _determine_compression(
        self,
        freq_state: str,
        flip_state: str,
        noise_state: str
    ) -> str:
        """Определить уровень компрессии."""
        # Считаем количество проблем
        problems = 0
        if freq_state in ("HIGH", "EXTREME"):
            problems += 1
        if flip_state in ("ERRATIC", "CHAOTIC"):
            problems += 1
        if noise_state in ("HIGH", "EXTREME"):
            problems += 1
        
        if problems >= 3:
            return "HEAVY"
        elif problems >= 2:
            return "MODERATE"
        elif problems >= 1:
            return "LIGHT"
        
        return "NONE"
    
    def _get_frozen_assets(
        self,
        current_assets: List[str],
        freeze: bool
    ) -> List[str]:
        """Получить список замороженных активов."""
        if not freeze:
            return []
        
        frozen = []
        for asset in current_assets:
            if asset not in self.core_assets:
                frozen.append(asset)
        
        return frozen
    
    def get_stats(self) -> Dict:
        """Получить статистику компрессии."""
        return {
            "compression_mode": self.state.compression_mode,
            "threshold_boost": self.state.entry_threshold_boost,
            "leverage_reduction": self.state.leverage_reduction,
            "frozen_assets": len(self.state.frozen_assets),
            "trade_frequency": self.get_trade_frequency(),
            "regime_flips": self.get_regime_flips(),
            "signal_noise": self.get_signal_noise(),
        }