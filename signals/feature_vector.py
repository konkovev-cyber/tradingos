"""
FeatureVector — immutable dataclass representing a single bar's precomputed features.

Rules:
- NEVER compute indicators inside SignalGenerator or Backtest loop
- Always get from FeatureStore via feature_store.get(bar_idx)
- Immutable: tuple-based, no mutation after creation
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FeatureVector:
    """
    Immutable snapshot of ALL precomputed indicators for a single bar.
    
    Fields:
        timestamp_ms: bar open timestamp (milliseconds)
        symbol: trading pair (e.g. "BTCUSDT")
        close: closing price
        open: opening price
        high: highest price
        low: lowest price
        volume: trading volume
        
        # EMA Layer
        ema20: 20-bar exponential moving average
        ema50: 50-bar exponential moving average
        ema200: 200-bar exponential moving average
        
        # Momentum
        rsi: Relative Strength Index (14)
        macd_line: MACD histogram value
        macd_signal: MACD signal line
        
        # Volatility
        atr: Average True Range (14)
        bb_upper: Bollinger Band upper (20, 2)
        bb_lower: Bollinger Band lower (20, 2)
        bb_middle: Bollinger Band middle (20)
        
        # Trend Strength
        adx: Average Directional Index (14)
        
        # Volume
        volume_ma: Volume moving average (20)
        volume_ratio: current_volume / volume_ma
        
        # Cumulative
        obv: On-Balance Volume
        
        # Derived
        vwap: Volume-Weighted Average Price (session)
        
        # Flags
        ema_bullish: ema20 > ema50
        price_above_ema50: close > ema50
        rsi_overbought: rsi > 70
        rsi_oversold: rsi < 30
    """
    timestamp_ms: int
    symbol: str
    
    # OHLCV
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    # EMA Layer
    ema20: float
    ema50: float
    ema200: Optional[float]
    
    # Momentum
    rsi: float
    macd_line: float
    macd_signal: float
    
    # Volatility
    atr: float
    bb_upper: float
    bb_lower: float
    bb_middle: float
    
    # Trend Strength
    adx: float
    
    # Volume
    volume_ma: float
    volume_ratio: float
    
    # Cumulative
    obv: float
    
    # VWAP
    vwap: float
    
    # Derived flags
    ema_bullish: bool
    price_above_ema50: bool
    rsi_overbought: bool
    rsi_oversold: bool
    
    # HTF Context (optional, joined later)
    htf_ema50: Optional[float] = None
    htf_ema200: Optional[float] = None
    htf_trend: Optional[str] = None  # "bullish" / "bearish" / "neutral"
    
    # Integrity
    integrity_score: float = 100.0
    
    def __post_init__(self):
        """Validate and compute derived fields."""
        object.__setattr__(self, 'ema_bullish', self.ema20 > self.ema50)
        object.__setattr__(self, 'price_above_ema50', self.close > self.ema50)
        object.__setattr__(self, 'rsi_overbought', self.rsi > 70)
        object.__setattr__(self, 'rsi_oversold', self.rsi < 30)
    
    @property
    def ema200_available(self) -> bool:
        return self.ema200 is not None and abs(self.ema200) > 1e-10
    
    @property
    def adx_available(self) -> bool:
        return self.adx is not None and not (self.adx == 0.0 and self.volume > 0)
    
    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization (Feature Snapshot)."""
        return {
            "timestamp_ms": self.timestamp_ms,
            "symbol": self.symbol,
            "close": self.close,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "ema20": self.ema20,
            "ema50": self.ema50,
            "ema200": self.ema200,
            "rsi": self.rsi,
            "macd_line": self.macd_line,
            "macd_signal": self.macd_signal,
            "atr": self.atr,
            "bb_upper": self.bb_upper,
            "bb_lower": self.bb_lower,
            "bb_middle": self.bb_middle,
            "adx": self.adx,
            "volume_ma": self.volume_ma,
            "volume_ratio": self.volume_ratio,
            "obv": self.obv,
            "vwap": self.vwap,
            "ema_bullish": self.ema_bullish,
            "price_above_ema50": self.price_above_ema50,
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
            "htf_ema50": self.htf_ema50,
            "htf_ema200": self.htf_ema200,
            "htf_trend": self.htf_trend,
            "integrity_score": self.integrity_score,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "FeatureVector":
        """Reconstruct from dict."""
        return cls(
            timestamp_ms=d["timestamp_ms"],
            symbol=d["symbol"],
            open=d["open"], high=d["high"], low=d["low"],
            close=d["close"], volume=d["volume"],
            ema20=d["ema20"], ema50=d["ema50"], ema200=d.get("ema200"),
            rsi=d["rsi"], macd_line=d["macd_line"], macd_signal=d["macd_signal"],
            atr=d["atr"], bb_upper=d["bb_upper"], bb_lower=d["bb_lower"],
            bb_middle=d["bb_middle"], adx=d["adx"],
            volume_ma=d["volume_ma"], volume_ratio=d["volume_ratio"],
            obv=d["obv"], vwap=d["vwap"],
            ema_bullish=d.get("ema_bullish", False),
            price_above_ema50=d.get("price_above_ema50", False),
            rsi_overbought=d.get("rsi_overbought", False),
            rsi_oversold=d.get("rsi_oversold", False),
        )
