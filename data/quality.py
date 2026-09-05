"""
Data Quality v1 — централизованная оценка качества рыночных данных.

Объединяет несколько метрик:
  1. Feature Age (свежесть индикаторов в секундах)
  2. Candle Count (сколько свечей в store)
  3. Source Quality (ws > rest_poller > recovery > none)
  4. Recent Errors (количество ошибок за последние N событий)
  5. Gap Ratio (доля пропущенных свечей)
  6. Drift Detection (RSI/ATR стоят на месте → индикаторы мёртвые)

Выдаёт:
  - feature_confidence: 0..1 (float)
  - confidence_level: HIGH / MEDIUM / LOW / UNUSABLE
  - reasons: список причин понижения confidence
  - is_tradeable: True если confidence >= threshold
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("UniBot.DataQuality")


class ConfidenceLevel(str, Enum):
    HIGH = "high"              # 80-100%
    MEDIUM = "medium"          # 50-79%
    LOW = "low"                # 25-49%
    UNUSABLE = "unusable"      # 0-24%


@dataclass
class QualityReport:
    """Полный отчёт о качестве данных для одного (symbol, timeframe)."""
    symbol: str
    timeframe: str
    feature_confidence: float          # 0..1
    confidence_level: ConfidenceLevel
    reasons: List[str] = field(default_factory=list)
    is_tradeable: bool = False
    components: Dict[str, float] = field(default_factory=dict)  # breakdown по компонентам

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "feature_confidence": round(self.feature_confidence * 100, 1),
            "confidence_level": self.confidence_level.value,
            "is_tradeable": self.is_tradeable,
            "reasons": self.reasons[:5],  # top-5 reasons
            "components": {k: round(v * 100, 1) for k, v in self.components.items()},
        }


class DataQualityScorer:
    """
    Скоринг качества данных для MarketDataSync.

    Использование:
        scorer = DataQualityScorer()
        report = scorer.score(symbol="BTC-USDT", timeframe="5m",
                              feature_age_sec=12.0,
                              candle_count=200,
                              primary_source=DataSource.REST_POLLER,
                              recent_errors=0,
                              gap_ratio=0.02,
                              drift_detected=False)
        if report.is_tradeable:
            ... использовать данные
    """

    # Веса компонентов (в сумме 1.0)
    WEIGHT_AGE = 0.30
    WEIGHT_COUNT = 0.20
    WEIGHT_SOURCE = 0.20
    WEIGHT_ERRORS = 0.15
    WEIGHT_GAPS = 0.10
    WEIGHT_DRIFT = 0.05

    # Пороги
    MIN_TRADEABLE_CONFIDENCE = 0.40  # ниже — нельзя торговать

    # Source quality: каждый источник даёт базовый score
    SOURCE_QUALITY = {
        "ws": 1.0,            # WebSocket — самый надёжный
        "rest_poller": 0.85,  # REST polling — нормально
        "historical": 0.60,   # Только bootstrap — устареет
        "recovery": 0.70,     # Gap recovery — норм но фрагментарно
        "none": 0.0,
    }

    def __init__(self):
        # История для drift detection: symbol → deque of (timestamp, atr, rsi, ema_diff)
        self._history: Dict[str, deque] = {}
        # История ошибок: symbol → deque of timestamps
        self._errors: Dict[str, deque] = {}

    def score(
        self,
        symbol: str,
        timeframe: str,
        feature_age_sec: Optional[float] = None,
        candle_count: int = 0,
        primary_source: str = "none",
        recent_errors: int = 0,
        gap_ratio: float = 0.0,
        drift_detected: bool = False,
    ) -> QualityReport:
        """
        Посчитать confidence для (symbol, timeframe).

        Args:
            feature_age_sec: секунд с последнего recalc() (None = неизвестно)
            candle_count: сколько свечей в FeatureStore
            primary_source: "ws" / "rest_poller" / "historical" / "recovery" / "none"
            recent_errors: кол-во ошибок за последние 100 событий
            gap_ratio: доля пропущенных свечей 0..1
            drift_detected: True если RSI/ATR стоят (drift detector нашёл)
        """
        components: Dict[str, float] = {}
        reasons: List[str] = []

        # 1. AGE — чем старше данные, тем хуже
        if feature_age_sec is None:
            age_score = 0.0
            reasons.append("no feature_age (unknown)")
        else:
            # <30s = 1.0, 30-60s = 0.9, 60-90s = 0.7, 90-180s = 0.4, >180s = 0.0
            if feature_age_sec <= 30:
                age_score = 1.0
            elif feature_age_sec <= 60:
                age_score = 0.9
            elif feature_age_sec <= 90:
                age_score = 0.7
            elif feature_age_sec <= 180:
                age_score = 0.4
                reasons.append(f"feature_age {feature_age_sec:.0f}s > 90s")
            else:
                age_score = 0.0
                reasons.append(f"feature_age {feature_age_sec:.0f}s stale")
        components["age"] = age_score

        # 2. CANDLE COUNT — чем больше, тем надёжнее
        # 35 = минимум, 200+ = отлично
        if candle_count < 35:
            count_score = 0.0
            reasons.append(f"only {candle_count} candles (<35)")
        elif candle_count < 100:
            count_score = 0.5
            reasons.append(f"{candle_count} candles (warmup)")
        elif candle_count < 200:
            count_score = 0.85
        else:
            count_score = 1.0
        components["count"] = count_score

        # 3. SOURCE QUALITY
        src_score = self.SOURCE_QUALITY.get(primary_source, 0.0)
        if src_score < 0.7:
            reasons.append(f"degraded source: {primary_source}")
        components["source"] = src_score

        # 4. ERRORS — больше ошибок, хуже
        if recent_errors == 0:
            err_score = 1.0
        elif recent_errors <= 2:
            err_score = 0.8
        elif recent_errors <= 5:
            err_score = 0.5
            reasons.append(f"{recent_errors} recent errors")
        else:
            err_score = 0.2
            reasons.append(f"{recent_errors} errors (data source unstable)")
        components["errors"] = err_score

        # 5. GAP RATIO
        if gap_ratio == 0:
            gap_score = 1.0
        elif gap_ratio < 0.02:
            gap_score = 0.95
        elif gap_ratio < 0.05:
            gap_score = 0.8
        elif gap_ratio < 0.1:
            gap_score = 0.6
            reasons.append(f"gap_ratio {gap_ratio*100:.1f}%")
        else:
            gap_score = 0.3
            reasons.append(f"gap_ratio {gap_ratio*100:.1f}% (heavy)")
        components["gaps"] = gap_score

        # 6. DRIFT DETECTION
        drift_score = 0.0 if drift_detected else 1.0
        if drift_detected:
            reasons.append("data drift: indicators frozen")
        components["drift"] = drift_score

        # Weighted total
        total = (
            components["age"] * self.WEIGHT_AGE +
            components["count"] * self.WEIGHT_COUNT +
            components["source"] * self.WEIGHT_SOURCE +
            components["errors"] * self.WEIGHT_ERRORS +
            components["gaps"] * self.WEIGHT_GAPS +
            components["drift"] * self.WEIGHT_DRIFT
        )
        confidence = max(0.0, min(1.0, total))

        # Level classification
        if confidence >= 0.80:
            level = ConfidenceLevel.HIGH
        elif confidence >= 0.50:
            level = ConfidenceLevel.MEDIUM
        elif confidence >= 0.25:
            level = ConfidenceLevel.LOW
        else:
            level = ConfidenceLevel.UNUSABLE

        is_tradeable = confidence >= self.MIN_TRADEABLE_CONFIDENCE

        return QualityReport(
            symbol=symbol,
            timeframe=timeframe,
            feature_confidence=confidence,
            confidence_level=level,
            reasons=reasons,
            is_tradeable=is_tradeable,
            components=components,
        )

    # ════════════════════════════════════════════════════════════
    #  Tracking helpers (drift detection + error tracking)
    # ════════════════════════════════════════════════════════════

    def track_indicators(self, symbol: str, atr: float, rsi: float, ema_fast: float, ema_slow: float) -> None:
        """Записать текущие значения индикаторов для drift detection."""
        key = symbol
        if key not in self._history:
            self._history[key] = deque(maxlen=20)
        ema_diff = abs(ema_fast - ema_slow)
        self._history[key].append({
            "ts": time.time(),
            "atr": atr,
            "rsi": rsi,
            "ema_diff": ema_diff,
        })

    def detect_drift(self, symbol: str, lookback: int = 10) -> bool:
        """
        True если ATR/RSI/EMA_diff не менялись за последние `lookback` точек.
        Это означает: либо рынок стоит (маловероятно), либо индикаторы перестали обновляться.
        """
        history = self._history.get(symbol)
        if not history or len(history) < lookback:
            return False
        recent = list(history)[-lookback:]
        atrs = [h["atr"] for h in recent]
        rsis = [h["rsi"] for h in recent]
        ema_diffs = [h["ema_diff"] for h in recent]

        # Если все значения идентичны с точностью до float precision — drift
        atr_range = max(atrs) - min(atrs)
        rsi_range = max(rsis) - min(rsis)
        ema_range = max(ema_diffs) - min(ema_diffs)

        # Порог: ATR меняется < 0.0001%, RSI < 0.01, EMA_diff < 0.0001
        if atr_range < 1e-6 and rsi_range < 0.01 and ema_range < 1e-6:
            return True
        return False

    def track_error(self, symbol: str) -> None:
        """Зарегистрировать ошибку источника данных."""
        if symbol not in self._errors:
            self._errors[symbol] = deque(maxlen=100)
        self._errors[symbol].append(time.time())

    def recent_error_count(self, symbol: str, window_sec: float = 600.0) -> int:
        """Количество ошибок за последние window_sec секунд."""
        errors = self._errors.get(symbol)
        if not errors:
            return 0
        cutoff = time.time() - window_sec
        return sum(1 for ts in errors if ts >= cutoff)

    def gap_ratio(self, symbol: str, expected: int, actual: int) -> float:
        """Доля пропущенных свечей."""
        if expected <= 0:
            return 0.0
        return max(0.0, 1.0 - (actual / expected))