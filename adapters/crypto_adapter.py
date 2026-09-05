"""
CryptoAdapter — Bybit testnet → Data Lake bridge.

Architecture:
  BybitAdapter (trading_brain_v4) → CryptoAdapter → Data Lake events

Event types produced:
  - CandleClosed: OHLCV data
  - RegimeDetected: market regime classification
  - Heartbeat: connection health

Usage:
  adapter = CryptoAdapter()
  await adapter.collect_once("BTCUSDT")
"""

import importlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Fix sys.path: trading_brain_v4 must be BEFORE tradingos for core/ imports ──
_TB4 = "/root/trading_brain_v4"
_TRADINGOS = str(Path(__file__).parent.parent)

# Remove tradingos from sys.path temporarily so core resolves from trading_brain_v4
_removed_tradingos = False
if _TRADINGOS in sys.path:
    sys.path.remove(_TRADINGOS)
    _removed_tradingos = True

# Clear cached core module if it was from tradingos
if "core" in sys.modules:
    _core_file = getattr(sys.modules["core"], "__file__", "")
    if "tradingos" in _core_file:
        del sys.modules["core"]
        # Also clear any submodules that were loaded from tradingos/core
        for key in list(sys.modules.keys()):
            if key.startswith("core.") and "tradingos" in str(getattr(sys.modules[key], "__file__", "")):
                del sys.modules[key]

if _TB4 not in sys.path:
    sys.path.insert(0, _TB4)

# Now import from trading_brain_v4
from exchange.bybit.adapter import BybitAdapter
from core.regime_brain import RegimeBrain, Regime

# Restore tradingos to sys.path (after trading_brain_v4)
if _removed_tradingos and _TRADINGOS not in sys.path:
    sys.path.insert(1, _TRADINGOS)

# DataLake from tradingos — import via file path
_dl_spec = importlib.util.spec_from_file_location(
    "sqlite_backend", str(Path(__file__).parent.parent / "core" / "data_lake" / "sqlite_backend.py")
)
_dl_mod = importlib.util.module_from_spec(_dl_spec)
_dl_spec.loader.exec_module(_dl_mod)
DataLake = _dl_mod.DataLake

logger = logging.getLogger("CryptoAdapter")

DB_PATH = Path(__file__).parent.parent / "tradingos_data.db"


class CryptoAdapter:
    """Bybit testnet → Data Lake adapter."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        db_path: Optional[Path] = None,
    ):
        self._bybit = BybitAdapter(
            api_key=api_key or os.environ.get("BYBIT_API_KEY", ""),
            api_secret=api_secret or os.environ.get("BYBIT_API_SECRET", ""),
            testnet=testnet,
        )
        self._lake = DataLake(db_path or DB_PATH)
        self._regime_brain = RegimeBrain()
        self._trace_id = str(uuid.uuid4())

        # Internal state for indicators
        self._prev_close: Optional[float] = None
        self._true_ranges: list[float] = []
        self._atr: float = 0.0
        self._adx: float = 0.0
        self._bars: list[dict] = []

    async def initialize(self) -> bool:
        """Check Bybit connection."""
        return await self._bybit.initialize()

    def close(self):
        self._bybit.close()
        self._lake.close()

    # ── OHLCV → Data Lake ──────────────────────────────────

    async def collect_once(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        limit: int = 200,
    ) -> int:
        """Fetch OHLCV from Bybit and write CandleClosed events to Data Lake.
        Returns number of events written.
        """
        ohlcv = await self._bybit.get_ohlcv(symbol, timeframe, limit)
        if not ohlcv:
            logger.warning(f"No data for {symbol}")
            return 0

        written = 0
        for candle in ohlcv:
            event = self._candle_to_event(candle, symbol, timeframe)
            if event:
                self._lake.write(event)
                written += 1

        logger.info(f"Wrote {written} CandleClosed events for {symbol}")
        return written

    def _candle_to_event(
        self, candle: dict, symbol: str, timeframe: str
    ) -> Optional[dict]:
        """Convert a single OHLCV candle to a Data Lake event."""
        try:
            ts = candle.get("time", 0)
            if ts > 1e12:
                ts = ts / 1000.0

            open_p = float(candle.get("open", 0))
            high = float(candle.get("high", 0))
            low = float(candle.get("low", 0))
            close = float(candle.get("close", 0))
            volume = float(candle.get("volume", 0))

            # Compute ATR
            if self._prev_close is not None:
                tr = max(
                    high - low,
                    abs(high - self._prev_close),
                    abs(low - self._prev_close),
                )
                self._true_ranges.append(tr)
                if len(self._true_ranges) > 14:
                    self._true_ranges = self._true_ranges[-14:]
                if len(self._true_ranges) == 14:
                    self._atr = sum(self._true_ranges) / 14

            # Compute ADX proxy
            price_change = 0.0
            if self._prev_close and self._prev_close > 0:
                price_change = (close - self._prev_close) / self._prev_close
            if self._atr > 0:
                self._adx = min(100, abs(price_change) / self._atr * 1000)

            self._prev_close = close

            # Regime detection
            bar = {
                "timestamp": ts,
                "price": close,
                "open": open_p,
                "high": high,
                "low": low,
                "volume": volume,
                "atr": self._atr,
                "adx": self._adx,
                "price_change": price_change,
            }
            regime = self._regime_brain.compute(bar)

            # Build CandleClosed event
            event = {
                "event_type": "CandleClosed",
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "source_module": "crypto_adapter",
                "symbol": symbol,
                "timeframe": timeframe,
                "severity": "info",
                "trace_id": self._trace_id,
                "payload": {
                    "open": open_p,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "price": close,
                    "atr": round(self._atr, 2),
                    "adx": round(self._adx, 2),
                    "price_change": round(price_change, 6),
                    "regime": regime.name,
                    "regime_confidence": round(regime.confidence, 4),
                },
            }

            # Also write RegimeDetected event
            regime_event = {
                "event_type": "RegimeDetected",
                "timestamp": event["timestamp"],
                "source_module": "crypto_adapter",
                "symbol": symbol,
                "timeframe": timeframe,
                "severity": "info",
                "trace_id": self._trace_id,
                "payload": {
                    "regime": regime.name,
                    "confidence": round(regime.confidence, 4),
                    "price": close,
                    "atr": round(self._atr, 2),
                    "adx": round(self._adx, 2),
                },
            }
            self._lake.write(regime_event)

            return event

        except Exception as e:
            logger.debug(f"Failed to convert candle: {e}")
            return None

    # ── Heartbeat ──────────────────────────────────────────

    def heartbeat(self, symbol: str = "BTCUSDT") -> dict:
        """Write a Heartbeat event."""
        event = {
            "event_type": "Heartbeat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_module": "crypto_adapter",
            "symbol": symbol,
            "severity": "info",
            "trace_id": self._trace_id,
            "payload": {
                "status": "alive",
                "bars_collected": len(self._bars),
                "atr": round(self._atr, 2),
                "adx": round(self._adx, 2),
            },
        }
        self._lake.write(event)
        return event

    # ── Stats ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "atr": round(self._atr, 2),
            "adx": round(self._adx, 2),
            "bars_collected": len(self._bars),
            "trace_id": self._trace_id,
        }

    def reset(self):
        self._prev_close = None
        self._true_ranges = []
        self._atr = 0.0
        self._adx = 0.0
        self._bars = []
        self._trace_id = str(uuid.uuid4())
