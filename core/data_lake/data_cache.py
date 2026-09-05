"""
Data Cache — локальное хранилище исторических OHLCV данных.

Скачивает данные один раз, сохраняет в Parquet-совместимом JSON.
Последующие запросы читают из кэша.

Usage:
  cache = DataCache()
  candles = cache.get("BTCUSDT", "5m", months=12)
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("DataCache")

CACHE_DIR = Path(__file__).parent / "data" / "cache"


class DataCache:
    """Локальный кэш OHLCV данных."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self._dir = cache_dir or CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return self._dir / f"{symbol}_{timeframe}.json"

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self._cache_path(symbol, timeframe).exists()

    def save(self, symbol: str, timeframe: str, candles: list[dict]):
        """Save candles to cache."""
        path = self._cache_path(symbol, timeframe)
        with open(path, "w") as f:
            json.dump(candles, f)
        logger.info(f"Cached {len(candles)} candles to {path}")

    def load(self, symbol: str, timeframe: str) -> Optional[list[dict]]:
        """Load candles from cache."""
        path = self._cache_path(symbol, timeframe)
        if not path.exists():
            return None
        with open(path) as f:
            candles = json.load(f)
        logger.info(f"Loaded {len(candles)} candles from cache {path}")
        return candles

    def get_metadata(self, symbol: str, timeframe: str) -> dict:
        """Get cache metadata without loading full data."""
        path = self._cache_path(symbol, timeframe)
        if not path.exists():
            return {"exists": False}
        size = path.stat().st_size
        return {
            "exists": True,
            "path": str(path),
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
        }

    def clear(self, symbol: str, timeframe: str):
        path = self._cache_path(symbol, timeframe)
        if path.exists():
            path.unlink()
            logger.info(f"Cleared cache for {symbol} {timeframe}")
