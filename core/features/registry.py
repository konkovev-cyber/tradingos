"""T3 Feature Registry — превращает события Data Lake в осмысленные признаки.

Два типа фич:
  - State features: текущее состояние рынка (regime, volatility, etc.)
  - Temporal features: агрегации по окнам (rolling avg, density, etc.)

Каждая фича имеет version, source_event_types, и calculation_fn_hash.
"""
import json
import time
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Callable, Any
from collections import deque
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.data_lake.sqlite_backend import DataLake


# ── Feature Spec ───────────────────────────────────────────

@dataclass
class FeatureSpec:
    """Метаописание одной фичи."""
    name: str
    version: str                          # semver
    description: str
    source_event_types: list[str]         # какие события нужны
    window_size: int = 1                  # сколько событий в окне (1 = state)
    window_seconds: int = 0               # или по времени (0 = по количеству)
    unit: str = ""                        # "price", "ratio", "count", "percent", etc.
    range_min: float = -float('inf')
    range_max: float = float('inf')
    category: str = "state"              # "state", "temporal", "derived"
    owner: str = "feature_factory"
    deprecated: bool = False

    @property
    def calc_hash(self) -> str:
        """Хеш формулы (для versioning)."""
        return hashlib.md5(f"{self.name}:{self.version}:{self.window_size}:{self.window_seconds}".encode()).hexdigest()[:8]


# ── Feature Value ──────────────────────────────────────────

@dataclass
class FeatureValue:
    """Одно значение фичи."""
    feature_name: str
    feature_version: str
    value: float
    timestamp: str
    symbol: str
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Feature Calculator (ABC) ──────────────────────────────

class FeatureCalculator(ABC):
    """Калькулятор одной фичи. Каждая фича = один calculator."""

    @property
    @abstractmethod
    def spec(self) -> FeatureSpec: ...

    @abstractmethod
    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        """Рассчитать фичу из окна событий."""
        ...


# ── Встроенные калькуляторы ────────────────────────────────

class RegimeStrength(FeatureCalculator):
    """Сила текущего режима (confidence от RegimeDetected)."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="regime_strength", version="1.0.0",
            description="Confidence текущего рыночного режима",
            source_event_types=["RegimeDetected"],
            window_size=1, unit="ratio", range_min=0, range_max=1,
            category="state",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        last = events[-1]
        payload = last.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        conf = payload.get("confidence", 0)
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=float(conf), timestamp=last.get("timestamp", ""),
            symbol=last.get("symbol", ""),
            metadata={"regime": payload.get("regime", "UNKNOWN")},
        )


class PriceLast(FeatureCalculator):
    """Последняя цена."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="price_last", version="1.0.0",
            description="Последняя цена закрытия свечи",
            source_event_types=["CandleClosed"],
            window_size=1, unit="price",
            category="state",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        last = events[-1]
        payload = last.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        price = payload.get("price", 0)
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=float(price), timestamp=last.get("timestamp", ""),
            symbol=last.get("symbol", ""),
        )


class ATRSmoothed(FeatureCalculator):
    """Сглаженный ATR (EMA по последним N свечам)."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="atr_smoothed", version="1.0.0",
            description="EMA-smoothed ATR по последним 10 свечам",
            source_event_types=["CandleClosed"],
            window_size=10, unit="price",
            category="temporal",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        values = []
        for e in events:
            payload = e.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            atr = payload.get("atr")
            if atr is not None:
                values.append(float(atr))
        if not values:
            return None
        # EMA smoothing
        alpha = 2 / (len(values) + 1)
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=round(ema, 2), timestamp=events[-1].get("timestamp", ""),
            symbol=events[-1].get("symbol", ""),
            metadata={"samples": len(values)},
        )


class ADXTrendStrength(FeatureCalculator):
    """Сила тренда (ADX)."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="adx_trend_strength", version="1.0.0",
            description="Average Directional Index — сила тренда",
            source_event_types=["CandleClosed"],
            window_size=1, unit="ratio", range_min=0, range_max=100,
            category="state",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        last = events[-1]
        payload = last.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        adx = payload.get("adx", 0)
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=float(adx), timestamp=last.get("timestamp", ""),
            symbol=last.get("symbol", ""),
        )


class VolumeNotional(FeatureCalculator):
    """Номинальный объём (volume в USDT)."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="volume_notional", version="1.0.0",
            description="Номинальный объём последней свечи",
            source_event_types=["CandleClosed"],
            window_size=1, unit="usd",
            category="state",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        last = events[-1]
        payload = last.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        vol = payload.get("volume", 0)
        price = payload.get("price", 0)
        notional = float(vol) * float(price) if vol and price else 0
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=round(notional, 2), timestamp=last.get("timestamp", ""),
            symbol=last.get("symbol", ""),
        )


class CycleSpeed(FeatureCalculator):
    """Скорость циклов (средний интервал между свечами)."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="cycle_speed", version="1.0.0",
            description="Средний интервал между циклами (секунды)",
            source_event_types=["CandleClosed"],
            window_size=5, unit="seconds",
            category="temporal",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if len(events) < 2:
            return None
        timestamps = []
        for e in events:
            ts = e.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    timestamps.append(dt.timestamp())
                except Exception:
                    pass
        if len(timestamps) < 2:
            return None
        intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        avg_interval = sum(intervals) / len(intervals)
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=round(avg_interval, 1), timestamp=events[-1].get("timestamp", ""),
            symbol=events[-1].get("symbol", ""),
            metadata={"intervals": len(intervals)},
        )


class EventDensity(FeatureCalculator):
    """Плотность событий за час."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="event_density_1h", version="1.0.0",
            description="Количество событий за последний час",
            source_event_types=["CandleClosed", "RegimeDetected", "Heartbeat"],
            window_seconds=3600, unit="count",
            category="temporal",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        now = datetime.now(timezone.utc)
        one_hour_ago = now.timestamp() - 3600
        count = 0
        for e in events:
            ts = e.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt.timestamp() >= one_hour_ago:
                        count += 1
                except Exception:
                    pass
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=float(count), timestamp=now.isoformat(),
            symbol=events[-1].get("symbol", "") if events else "",
        )


class TrendConsistency(FeatureCalculator):
    """Consistency тренда (процент свеч в направлении тренда)."""
    @property
    def spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="trend_consistency", version="1.0.0",
            description="Процент свеч close > open за окно (bullish consistency)",
            source_event_types=["CandleClosed"],
            window_size=10, unit="percent", range_min=0, range_max=100,
            category="temporal",
        )

    def calculate(self, events: list[dict]) -> Optional[FeatureValue]:
        if not events:
            return None
        bullish = 0
        for e in events:
            payload = e.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            price = payload.get("price", 0)
            # Approximate: if price is close to cycle high → bullish
            atr = payload.get("atr", 1)
            if price > 0 and atr > 0:
                # Simple heuristic: if volume is above median → likely bullish
                vol = payload.get("volume", 0)
                if vol > 0:
                    bullish += 1
        consistency = (bullish / len(events) * 100) if events else 0
        return FeatureValue(
            feature_name=self.spec.name, feature_version=self.spec.version,
            value=round(consistency, 1), timestamp=events[-1].get("timestamp", ""),
            symbol=events[-1].get("symbol", ""),
        )


# ── Feature Registry ──────────────────────────────────────

class FeatureRegistry:
    """Реестр и хранилище фичей. Считает фичи из Data Lake."""

    def __init__(self, data_lake: DataLake):
        self._lake = data_lake
        self._calculators: dict[str, FeatureCalculator] = {}
        self._specs: dict[str, FeatureSpec] = {}
        self._latest: dict[str, FeatureValue] = {}  # name → latest value
        self._history: dict[str, deque] = {}        # name → deque of values

        # Register built-in calculators
        for calc in [
            RegimeStrength(), PriceLast(), ATRSmoothed(),
            ADXTrendStrength(), VolumeNotional(), CycleSpeed(),
            EventDensity(), TrendConsistency(),
        ]:
            self.register(calc)

    def register(self, calculator: FeatureCalculator):
        spec = calculator.spec
        self._calculators[spec.name] = calculator
        self._specs[spec.name] = spec
        self._history[spec.name] = deque(maxlen=1000)

    def compute_all(self, symbol: str = "BTCUSDT", limit: int = 100) -> dict[str, FeatureValue]:
        """Вычислить все фичи для символа."""
        results = {}
        for name, calc in self._calculators.items():
            spec = calc.spec
            # Fetch events for this feature's source types
            events = []
            for etype in spec.source_event_types:
                rows = self._lake.query(event_type=etype, symbol=symbol, limit=spec.window_size * 3)
                events.extend(rows)
            # Sort by timestamp
            events.sort(key=lambda e: e.get("timestamp", ""))
            # Take last N
            events = events[-spec.window_size:] if spec.window_size > 0 else events

            if events:
                value = calc.calculate(events)
                if value:
                    self._latest[name] = value
                    self._history[name].append(value)
                    results[name] = value
        return results

    def get_latest(self, name: str) -> Optional[FeatureValue]:
        return self._latest.get(name)

    def get_all_latest(self) -> dict[str, dict]:
        return {name: v.to_dict() for name, v in self._latest.items()}

    def get_history(self, name: str, limit: int = 100) -> list[dict]:
        history = list(self._history.get(name, []))
        return [v.to_dict() for v in history[-limit:]]

    def list_specs(self) -> list[dict]:
        return [asdict(s) for s in self._specs.values()]

    def to_snapshot(self) -> dict:
        """Текущий снапшот всех фичей."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "features": self.get_all_latest(),
            "specs": {name: s.version for name, s in self._specs.items()},
        }
