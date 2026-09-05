"""
TradingOS Data Models — extracted from uBot_bingx models/context.py.

Содержит только:
- MarketState: рыночный контекст (regime, price, volatility)
- Features: предвычисленные индикаторы (EMA, RSI, MACD, ATR, BB, ADX, VWAP)

StrategyContext — НЕ включён (содержал execution deps).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from tradingos.data.models.candle import Candle


@dataclass
class MarketState:
    """
    Current market state for a symbol.
    Populated by Market Intelligence module.
    """
    symbol: str
    regime: str = "normal"
    regime_confidence: float = 0.0
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0
    trend_direction: str = "neutral"
    trend_strength: float = 0.0
    volatility: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    volume_24h: float = 0.0
    volume_ratio: float = 1.0
    funding_rate: float = 0.0
    open_interest: float = 0.0
    oi_change_pct: float = 0.0
    session: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
            "last_price": self.last_price,
            "bid": self.bid,
            "ask": self.ask,
            "spread_pct": self.spread_pct,
            "trend_direction": self.trend_direction,
            "trend_strength": self.trend_strength,
            "volatility": self.volatility,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "volume_24h": self.volume_24h,
            "volume_ratio": self.volume_ratio,
            "funding_rate": self.funding_rate,
            "open_interest": self.open_interest,
            "oi_change_pct": self.oi_change_pct,
            "session": self.session,
        }


@dataclass
class Features:
    """
    Pre-computed features for a symbol.

    Populated by FeatureStore.
    Strategies access via features context.
    """
    symbol: str
    timeframe: str = "1m"

    # === Price ===
    price: float = 0.0

    # === Core indicators ===
    rsi: float = 50.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema_20: float = 0.0
    ema_50: float = 0.0
    ema_200: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    vwap: float = 0.0
    bollinger_upper: float = 0.0
    bollinger_lower: float = 0.0
    bollinger_mid: float = 0.0

    # === Derived ===
    momentum: float = 0.0
    trend_up: bool = True
    volatility: float = 0.0

    # === Raw data ===
    candles: List[Candle] = field(default_factory=list)
    prices: List[float] = field(default_factory=list)
    candle_count: int = 0

    # === Extra key/value store ===
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price": self.price,
            "rsi": self.rsi,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ema_20": self.ema_20,
            "ema_50": self.ema_50,
            "ema_200": self.ema_200,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_hist": self.macd_hist,
            "adx": self.adx,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "vwap": self.vwap,
            "bollinger_upper": self.bollinger_upper,
            "bollinger_lower": self.bollinger_lower,
            "bollinger_mid": self.bollinger_mid,
            "momentum": self.momentum,
            "trend_up": self.trend_up,
            "volatility": self.volatility,
            "candle_count": self.candle_count,
        }
