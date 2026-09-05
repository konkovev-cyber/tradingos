#!/usr/bin/env python3
"""
BingX OHLCV Poller — источник свечей для TradingOS Data Layer (BingX-контур).

Отличие от ohlcv_poller.py: свечи берутся через BingXClient.get_kline().
FeatureStore, IndicatorCalculator, SignalGenerator — чистые библиотеки, без сети.

Архитектура:

    BingX OHLCV Poller (REST, раз в 60с)
         │
         ▼
    Candle dataclass
         │
         ▼
    FeatureStore (чистая библиотека)
         │
         ▼
    FeatureVector
         │
         ▼
    SignalGenerator
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure imports work when run as script
# NOTE: /opt/ubot_bingx NOT in path — it has its own `exchange` package that conflicts
for _p in ("/root/trading_brain_v4", "/root/tradingos", "/root"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tradingos.data.models.candle import Candle
from tradingos.data.feature_store import FeatureStore
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator

log = logging.getLogger("bingx_ohlcv_poller")

# Конфигурация
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
DEFAULT_INTERVAL = "1m"  # BingX interval format: 1m, 5m, 15m, 1h
DEFAULT_LIMIT = 200  # сколько свечей хранить для расчёта индикаторов


class BingXOHLCVPoller:
    """Периодический опрос BingX REST API для получения свечей.

    Не является DataHub. Не содержит execution-логики.
    Только poll → Candle → FeatureStore.
    """

    def __init__(self, symbols: list[str] | None = None):
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.feature_store = FeatureStore()
        self.indicator_calc = IndicatorCalculator()
        self.signal_generators: dict[str, SignalGenerator] = {}
        self.running = False
        self._stats = {"polls": 0, "errors": 0, "signals": 0}

    async def _fetch_kline(self, symbol: str) -> list[Candle]:
        """Получить последние свечи через BingXClient.get_kline()."""
        # Ensure exchange package importable (redundant but safe for script execution)
        for _p in ("/root/trading_brain_v4",):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from exchange.bingx import client as _bx_client
        BingXClient = _bx_client.BingXClient

        # Load credentials
        env_path = "/opt/ubot_bingx/.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for l in f:
                    l = l.strip()
                    if l and not l.startswith("#") and "=" in l:
                        k, v = l.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

        client = BingXClient()
        data = client.get_kline(symbol, interval=DEFAULT_INTERVAL, limit=DEFAULT_LIMIT)
        client.close()

        if not isinstance(data, list) or not data:
            return []

        # BingX kline format: [{open, high, low, close, volume, time, ...}]
        candles = []
        for row in data:
            try:
                ts = int(row.get("time") or row.get("timestamp") or 0)
                o = float(row.get("open", 0))
                h = float(row.get("high", 0))
                l = float(row.get("low", 0))
                c = float(row.get("close", 0))
                v = float(row.get("volume", 0))
            except (IndexError, ValueError, TypeError):
                continue
            if ts == 0 or c == 0:
                continue

            candle = Candle(
                timestamp=ts,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=v,
                symbol=symbol,
                timeframe=DEFAULT_INTERVAL,
            )
            candles.append(candle)

        # Sort chronological (BingX returns newest last, but be safe)
        candles.sort(key=lambda c: c.timestamp)
        return candles

    def _candle_to_feature_vector(self, symbol: str) -> FeatureVector | None:
        """Создать FeatureVector из последних данных FeatureStore.

        Это bridging-функция между Features (из FeatureStore)
        и FeatureVector (который ожидает SignalGenerator).
        """
        features = self.feature_store.get_features(symbol, DEFAULT_INTERVAL)
        if not features or not features.candles:
            return None

        last = features.candles[-1]

        return FeatureVector(
            timestamp_ms=last.timestamp,
            symbol=symbol,
            open=last.open,
            high=last.high,
            low=last.low,
            close=last.close,
            volume=last.volume,
            ema20=features.ema_20,
            ema50=features.ema_50,
            ema200=features.ema_200,
            rsi=features.rsi,
            macd_line=features.macd,
            macd_signal=features.macd_signal,
            atr=features.atr,
            bb_upper=features.bollinger_upper,
            bb_lower=features.bollinger_lower,
            bb_middle=features.bollinger_mid,
            adx=features.adx,
            volume_ma=0.0,  # будет заполнено из history
            volume_ratio=0.0,
            obv=0.0,
            vwap=features.vwap,
            ema_bullish=features.trend_up,
            price_above_ema50=last.close > features.ema_50 if features.ema_50 else False,
            rsi_overbought=features.rsi > 70,
            rsi_oversold=features.rsi < 30,
            htf_ema50=None,
            htf_ema200=None,
            htf_trend=None,
            integrity_score=getattr(features, 'integrity_score', 1.0),
        )

    def _build_feature_vector_from_store(self, symbol: str) -> FeatureVector | None:
        """Построить FeatureVector напрямую из FeatureStore + IndicatorCalculator.

        Используется если get_features() недоступен.
        """
        candles = self.feature_store.get_candles(symbol, DEFAULT_INTERVAL)
        if not candles or len(candles) < 20:
            return None

        last = candles[-1]
        prices = [c.close for c in candles if c.close > 0]

        if len(prices) < 20:
            return None

        ema20 = self.indicator_calc.ema(prices, 20) if len(prices) >= 20 else 0.0
        ema50 = self.indicator_calc.ema(prices, 50) if len(prices) >= 50 else 0.0
        ema200 = self.indicator_calc.ema(prices, 200) if len(prices) >= 200 else 0.0
        rsi = self.indicator_calc.rsi(prices, 14) if len(prices) >= 14 else 50.0
        atr = self.indicator_calc.atr(candles, 14) if len(candles) >= 14 else 0.0
        adx = self.indicator_calc.adx(candles, 14) if len(candles) >= 14 else 0.0
        macd = self.indicator_calc.macd(prices) if len(prices) >= 26 else {}
        bb = self.indicator_calc.bollinger(prices) if len(prices) >= 20 else {}
        vwap = self.indicator_calc.vwap(candles) if candles else 0.0
        vol_ratio = self.indicator_calc.volume_ratio(
            [c.volume for c in candles], 20
        ) if len(candles) >= 20 else 1.0

        return FeatureVector(
            timestamp_ms=last.timestamp,
            symbol=symbol,
            open=last.open,
            high=last.high,
            low=last.low,
            close=last.close,
            volume=last.volume,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200 if ema200 else 0.0,
            rsi=rsi,
            macd_line=macd.get("macd", 0.0),
            macd_signal=macd.get("signal", 0.0),
            atr=atr,
            bb_upper=bb.get("upper", 0.0),
            bb_lower=bb.get("lower", 0.0),
            bb_middle=bb.get("middle", 0.0),
            adx=adx,
            volume_ma=0.0,
            volume_ratio=vol_ratio,
            obv=0.0,
            vwap=vwap,
            ema_bullish=ema20 > ema50 if ema20 and ema50 else False,
            price_above_ema50=last.close > ema50 if ema50 > 0 else False,
            rsi_overbought=rsi > 70,
            rsi_oversold=rsi < 30,
            htf_ema50=None,
            htf_ema200=None,
            htf_trend=None,
            integrity_score=1.0,
        )

    async def poll_once(self) -> dict:
        """Один цикл опроса: REST → Candle → FeatureStore → FeatureVector → Signal.

        Returns:
            dict с результатами по каждому символу
        """
        results = {}
        for symbol in self.symbols:
            try:
                candles = await self._fetch_kline(symbol)
                if not candles:
                    results[symbol] = {"status": "no_data", "candles": 0}
                    continue

                # FeatureStore.add_candles — чистое добавление свечей
                tf = DEFAULT_INTERVAL
                self.feature_store.add_candles(symbol, tf, candles)

                # Принудительный пересчёт индикаторов
                self.feature_store.recalculate(symbol)

                fv = self._build_feature_vector_from_store(symbol)
                if fv is None:
                    results[symbol] = {"status": "insufficient_data", "candles": len(candles)}
                    continue

                # SignalGenerator
                if symbol not in self.signal_generators:
                    self.signal_generators[symbol] = SignalGenerator()

                sg = self.signal_generators[symbol]
                direction = sg.decide(symbol, fv, bar_idx=0)

                results[symbol] = {
                    "status": "ok",
                    "candles": len(candles),
                    "price": fv.close,
                    "rsi": round(fv.rsi, 1),
                    "adx": round(fv.adx, 1),
                    "signal": direction or "none",
                    "signal_score": sg.stats.get("signals_total", 0),
                }

                if direction:
                    self._stats["signals"] += 1

            except Exception as e:
                log.warning(f"poll_once({symbol}): {e}")
                results[symbol] = {"status": "error", "error": str(e)}
                self._stats["errors"] += 1

        self._stats["polls"] += 1
        return results

    async def run_forever(self, interval: int = 60):
        """Запустить бесконечный цикл опроса.

        Args:
            interval: секунд между опросами (default 60 = 1м свеча)
        """
        self.running = True
        log.info(f"OHLCV Poller started: {self.symbols} every {interval}s")

        while self.running:
            tick = time.time()
            results = await self.poll_once()
            elapsed = time.time() - tick

            # Логирование
            signals = [(s, r.get("signal")) for s, r in results.items()
                       if r.get("signal") not in (None, "none")]
            if signals:
                log.info(f"Signals: {signals}")

            wait = max(1, interval - int(elapsed))
            await asyncio.sleep(wait)

    def stop(self):
        self.running = False

    def get_stats(self) -> dict:
        return {**self._stats, "symbols": self.symbols}


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    poller = BingXOHLCVPoller()
    try:
        await poller.run_forever(interval=60)
    except KeyboardInterrupt:
        poller.stop()


if __name__ == "__main__":
    asyncio.run(main())
