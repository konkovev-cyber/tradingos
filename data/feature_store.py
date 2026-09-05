"""
FeatureStore — единый источник индикаторов и рыночных данных.

Architecture:
    Exchange → TimeframeManager → FeatureStore → Strategies

Все стратегии получают данные отсюда.
Никаких calculate_ema() внутри стратегии.

Multi-timeframe support:
    BTCUSDT
    ├── 1m  → candles + indicators
    ├── 5m  → candles + indicators
    ├── 15m → candles + indicators
    ├── 1H  → candles + indicators
    ├── 4H  → candles + indicators
    └── 1D  → candles + indicators

Usage:
    store = FeatureStore(max_candles=500)

    # Add candle
    store.add_candle("BTCUSDT", "5m", candle)

    # Get features
    features = store.get_features("BTCUSDT", "5m")

    # Get specific indicator
    rsi = store.get("BTCUSDT", "5m", "rsi")
"""
import logging
import time
from typing import Dict, List, Any, Optional
from collections import defaultdict

from tradingos.data.models.candle import Candle
from tradingos.data.models.features import Features
from tradingos.data.indicators import IndicatorCalculator

log = logging.getLogger("FeatureStore")


class SymbolData:
    """
    Data for a single symbol+timeframe.

    Stores candles and computed indicators.
    """

    def __init__(self, symbol: str, timeframe: str, max_candles: int = 500):
        self.symbol = symbol
        self.timeframe = timeframe
        self.max_candles = max_candles

        # Raw candles
        self.candles: List[Candle] = []

        # Computed indicators (updated on each new candle)
        self.indicators: Dict[str, Any] = {}

        # Last update timestamp (wall-clock)
        self.last_update: float = 0.0

        # Per-indicator timestamps (when each indicator was last computed)
        self.indicator_timestamps: Dict[str, float] = {}

        # Flag: indicators need recalculation
        self._dirty: bool = True

    def add_candle(self, candle: Candle) -> None:
        """Add a new candle and mark indicators as dirty."""
        # Update existing candle if same timestamp
        if self.candles and self.candles[-1].timestamp == candle.timestamp:
            self.candles[-1] = candle
        else:
            self.candles.append(candle)

        # Trim to max
        if len(self.candles) > self.max_candles:
            self.candles = self.candles[-self.max_candles:]

        self.last_update = time.time()
        self._dirty = True

    def recalculate(self) -> None:
        """Recalculate indicators if dirty."""
        if not self._dirty:
            return

        if len(self.candles) < 35:
            return

        self.indicators = IndicatorCalculator.calculate_all(self.candles)
        # Stamp per-indicator timestamps (для freshness tracking)
        now = time.time()
        for ind_name in self.indicators.keys():
            self.indicator_timestamps[ind_name] = now
        self._dirty = False

    @property
    def candle_count(self) -> int:
        return len(self.candles)

    @property
    def last_price(self) -> float:
        return self.candles[-1].close if self.candles else 0.0

    @property
    def last_candle(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None


class FeatureStore:
    """
    Единый источник индикаторов для всей платформы.

    Все стратегии читают данные отсюда.
    Индикаторы пересчитываются только при поступлении новых свечей.

    Key features:
        - Multi-timeframe: 1m, 5m, 15m, 1H, 4H, 1D
        - Lazy recalculation: only when dirty
        - Thread-safe: async-compatible
        - Memory-bounded: max_candles per symbol+timeframe
    """

    # Supported timeframes with their durations in seconds
    TIMEFRAMES = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1H": 3600,
        "4H": 14400,
        "1D": 86400,
    }

    def __init__(self, max_candles: int = 500):
        """
        Args:
            max_candles: Maximum candles to keep per symbol+timeframe
        """
        self.max_candles = max_candles

        # symbol → timeframe → SymbolData
        self._data: Dict[str, Dict[str, SymbolData]] = defaultdict(dict)

        # Statistics
        self._updates = 0
        self._recalculations = 0

    # ══════════════════════════════════════════════════════════
    #  CANDLE MANAGEMENT
    # ══════════════════════════════════════════════════════════

    def add_candle(self, symbol: str, timeframe: str, candle: Candle) -> None:
        """
        Add a candle to the store.

        This is the main data input method.
        Called by TimeframeManager when new candles are available.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            timeframe: Timeframe (e.g. "5m", "1H")
            candle: Candle object
        """
        if timeframe not in self._data[symbol]:
            self._data[symbol][timeframe] = SymbolData(
                symbol, timeframe, self.max_candles
            )

        self._data[symbol][timeframe].add_candle(candle)
        self._updates += 1

    def add_candles(self, symbol: str, timeframe: str, candles: List[Candle]) -> None:
        """Add multiple candles at once (for preload)."""
        for candle in candles:
            self.add_candle(symbol, timeframe, candle)

    def update_from_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        timestamp: float,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        """
        Update from raw OHLCV data (convenience method).

        Creates a Candle and adds it to the store.
        """
        candle = Candle(
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            symbol=symbol,
            timeframe=timeframe,
        )
        self.add_candle(symbol, timeframe, candle)

    # ══════════════════════════════════════════════════════════
    #  FEATURES ACCESS
    # ══════════════════════════════════════════════════════════

    def get_features(self, symbol: str, timeframe: str = "5m") -> Optional[Features]:
        """
        Get computed features for symbol+timeframe.

        This is the main method strategies call.

        Args:
            symbol: Trading pair
            timeframe: Timeframe (default "5m")

        Returns:
            Features object with all indicators, or None if not enough data
        """
        data = self._data.get(symbol, {}).get(timeframe)
        if not data or data.candle_count < 35:
            return None

        # Recalculate if dirty
        data.recalculate()
        if not data.indicators:
            return None

        ind = data.indicators

        # Data freshness metadata
        import time as _time
        now = _time.time()
        last_candle_ts = data.candles[-1].timestamp if data.candles else 0.0
        data_age = max(0.0, now - data.last_update) if data.last_update else float("inf")
        candle_age = max(0.0, now - last_candle_ts) if last_candle_ts else float("inf")
        data_quality = 1.0 if data_age < 90 else max(0.0, 1.0 - data_age / 300.0)

        # Per-indicator freshness (EMA updated X sec ago, ADX Y sec ago, etc.)
        indicator_age = {}
        for ind_name, ts in data.indicator_timestamps.items():
            indicator_age[ind_name] = max(0.0, now - ts) if ts else float("inf")

        return Features(
            symbol=symbol,
            timeframe=timeframe,
            price=ind.get("price", 0.0),
            rsi=ind.get("rsi", 50.0),
            ema_fast=ind.get("ema_9", 0.0),
            ema_slow=ind.get("ema_20", 0.0),
            ema_20=ind.get("ema_20", 0.0),
            ema_50=ind.get("ema_50", 0.0),
            ema_200=ind.get("ema_200", 0.0),
            macd=ind.get("macd", 0.0),
            macd_signal=ind.get("macd_signal", 0.0),
            macd_hist=ind.get("macd_histogram", 0.0),
            adx=ind.get("adx", 0.0),
            atr=ind.get("atr", 0.0),
            atr_pct=ind.get("atr_pct", 0.0),
            vwap=ind.get("vwap", 0.0),
            bollinger_upper=ind.get("bollinger_upper", 0.0),
            bollinger_lower=ind.get("bollinger_lower", 0.0),
            bollinger_mid=ind.get("bollinger_middle", 0.0),
            momentum=ind.get("momentum", 0.0),
            trend_up=ind.get("trend_direction", "neutral") == "up",
            volatility=ind.get("volatility", 0.0),
            candles=data.candles[-50:],
            prices=[c.close for c in data.candles[-50:]],
            candle_count=data.candle_count,
            extra={
                "volume_ratio": ind.get("volume_ratio", 1.0),
                "bollinger_width": ind.get("bollinger_width", 0.0),
                "trend_direction": ind.get("trend_direction", "neutral"),
                "candle_count": data.candle_count,
                "data_age": data_age,
                "candle_age": candle_age,
                "data_quality": data_quality,
                "is_stale": data_age > 90.0,
                "is_insufficient": False,  # мы уже проверили candle_count >= 35
                "last_candle_ts": last_candle_ts,
                "indicator_age": indicator_age,        # per-indicator freshness
                "ema_age": indicator_age.get("ema_20", float("inf")),
                "adx_age": indicator_age.get("adx", float("inf")),
                "atr_age": indicator_age.get("atr", float("inf")),
                "rsi_age": indicator_age.get("rsi", float("inf")),
            },
        )

    def get_indicator(self, symbol: str, timeframe: str, name: str) -> Any:
        """
        Get a specific indicator value.

        Args:
            symbol: Trading pair
            timeframe: Timeframe
            name: Indicator name (e.g. "rsi", "ema_20", "adx")

        Returns:
            Indicator value or None
        """
        data = self._data.get(symbol, {}).get(timeframe)
        if not data:
            return None

        data.recalculate()
        return data.indicators.get(name)

    def get(self, symbol: str, timeframe: str, name: str, default: Any = None) -> Any:
        """Alias for get_indicator with default value."""
        val = self.get_indicator(symbol, timeframe, name)
        return val if val is not None else default

    # ══════════════════════════════════════════════════════════
    #  MULTI-TIMEFRAME ACCESS
    # ══════════════════════════════════════════════════════════

    def get_multi_timeframe(self, symbol: str) -> Dict[str, Features]:
        """
        Get features for all available timeframes.

        Returns:
            {"5m": Features, "15m": Features, "1H": Features, ...}
        """
        result = {}
        for tf in self._data.get(symbol, {}):
            features = self.get_features(symbol, tf)
            if features:
                result[tf] = features
        return result

    def get_candles(self, symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
        """Get raw candles for symbol+timeframe."""
        data = self._data.get(symbol, {}).get(timeframe)
        if not data:
            return []
        return data.candles[-limit:]

    # ══════════════════════════════════════════════════════════
    #  QUERIES
    # ══════════════════════════════════════════════════════════

    def get_symbols(self) -> List[str]:
        """Get all symbols in the store."""
        return list(self._data.keys())

    def get_timeframes(self, symbol: str) -> List[str]:
        """Get all timeframes for a symbol."""
        return list(self._data.get(symbol, {}).keys())

    def get_candle_count(self, symbol: str, timeframe: str = "5m") -> int:
        """Get number of candles for symbol+timeframe."""
        data = self._data.get(symbol, {}).get(timeframe)
        return data.candle_count if data else 0

    def get_last_price(self, symbol: str) -> float:
        """Get last known price for symbol (from any timeframe)."""
        for tf in ["1m", "5m", "15m", "1H"]:
            data = self._data.get(symbol, {}).get(tf)
            if data and data.last_price > 0:
                return data.last_price
        return 0.0

    def has_enough_data(self, symbol: str, timeframe: str = "5m", min_candles: int = 35) -> bool:
        """Check if we have enough data for indicator calculation."""
        data = self._data.get(symbol, {}).get(timeframe)
        return data is not None and data.candle_count >= min_candles

    # ══════════════════════════════════════════════════════════
    #  MAINTENANCE
    # ══════════════════════════════════════════════════════════

    def recalculate_all(self) -> int:
        """
        Force recalculation of all dirty indicators.

        Returns:
            Number of recalculated symbol+timeframe pairs
        """
        count = 0
        for symbol_data in self._data.values():
            for data in symbol_data.values():
                if data._dirty:
                    data.recalculate()
                    count += 1
        self._recalculations += count
        return count

    def recalculate(self, symbol: str, timeframe: str = "5m") -> bool:
        """
        Incremental recalculate indicators for one (symbol, timeframe).
        Used by MarketDataSync on each new candle.

        Returns:
            True if recalculated, False if no data or not dirty
        """
        data = self._data.get(symbol, {}).get(timeframe)
        if data is None:
            return False
        was_dirty = data._dirty
        data.recalculate()
        if was_dirty:
            self._recalculations += 1
        return was_dirty

    def get_last_candle_time(self, symbol: str, timeframe: str = "5m") -> Optional[float]:
        """Unix timestamp последней свечи, или None если данных нет."""
        data = self._data.get(symbol, {}).get(timeframe)
        if data is None or data.candle_count == 0:
            return None
        return data.candles[-1].timestamp

    def clear(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> None:
        """
        Clear data.

        Args:
            symbol: Clear specific symbol, or None for all
            timeframe: Clear specific timeframe, or None for all
        """
        if symbol and timeframe:
            self._data.get(symbol, {}).pop(timeframe, None)
        elif symbol:
            self._data.pop(symbol, None)
        else:
            self._data.clear()

    # ══════════════════════════════════════════════════════════
    #  STATISTICS
    # ══════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        total_candles = 0
        total_symbols = len(self._data)
        total_timeframes = 0

        for symbol_data in self._data.values():
            for data in symbol_data.values():
                total_candles += data.candle_count
                total_timeframes += 1

        return {
            "symbols": total_symbols,
            "timeframes": total_timeframes,
            "total_candles": total_candles,
            "updates": self._updates,
            "recalculations": self._recalculations,
        }

    def __repr__(self) -> str:
        stats = self.get_stats()
        return (
            f"FeatureStore(symbols={stats['symbols']}, "
            f"candles={stats['total_candles']})"
        )
