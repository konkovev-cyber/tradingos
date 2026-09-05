"""
Candle — модель свечи (OHLCV).

Единственная модель для свечей во всей платформе.
FeatureStore, стратегии и бэктестер работают только с этой моделью.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Candle:
    """OHLCV candle data."""
    timestamp: float        # Unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str = ""
    timeframe: str = "1m"   # "1m", "5m", "15m", "1H", "4H", "1D"

    @property
    def body(self) -> float:
        """Absolute body size."""
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        """Full candle range (high - low)."""
        return self.high - self.low

    @property
    def upper_shadow(self) -> float:
        """Upper shadow size."""
        return self.high - max(self.close, self.open)

    @property
    def lower_shadow(self) -> float:
        """Lower shadow size."""
        return min(self.close, self.open) - self.low

    @property
    def is_bullish(self) -> bool:
        """True if close > open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """True if close < open."""
        return self.close < self.open

    @property
    def mid_price(self) -> float:
        """(high + low) / 2."""
        return (self.high + self.low) / 2

    @property
    def typical_price(self) -> float:
        """(high + low + close) / 3."""
        return (self.high + self.low + self.close) / 3

    @property
    def body_pct(self) -> float:
        """Body as percentage of range."""
        if self.range == 0:
            return 0.0
        return self.body / self.range * 100

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Candle":
        return cls(
            timestamp=data["timestamp"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            volume=data["volume"],
            symbol=data.get("symbol", ""),
            timeframe=data.get("timeframe", "1m"),
        )
