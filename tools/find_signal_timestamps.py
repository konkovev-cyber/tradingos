#!/usr/bin/env python3
"""Найти точные timestamps сигналов из исторических данных."""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator


async def fetch_candles(symbol: str) -> list[Candle]:
    import httpx
    all_candles = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=15) as client:
        while len(all_candles) < 720:
            params = {"category": "linear", "symbol": symbol, "interval": "60", "limit": 200}
            if end_ms:
                params["end"] = str(end_ms)
            resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            data = resp.json()
            rows = data.get("result", {}).get("list", [])
            if not rows:
                break
            for row in rows:
                all_candles.append(Candle(
                    timestamp=int(row[0]),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]),
                    volume=float(row[5]) if row[5] else 0.0,
                    symbol=symbol, timeframe="1h",
                ))
            end_ms = int(rows[-1][0]) - 1
            await asyncio.sleep(0.1)
    all_candles.sort(key=lambda x: x.timestamp)
    return all_candles


async def main():
    for symbol in ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]:
        print(f"\n=== {symbol} ===")
        candles = await fetch_candles(symbol)
        print(f"Fetched {len(candles)} candles")
        print(f"  First: {datetime.fromtimestamp(candles[0].timestamp/1000, tz=timezone.utc)}")
        print(f"  Last:  {datetime.fromtimestamp(candles[-1].timestamp/1000, tz=timezone.utc)}")

        # Run SignalGenerator
        calc = IndicatorCalculator()
        sg = SignalGenerator()
        closes = [c.close for c in candles]

        for i in range(200, len(candles)):
            window = candles[:i+1]
            prices = closes[:i+1]

            ema20 = calc.ema(prices, 20)
            ema50 = calc.ema(prices, 50)
            ema200 = calc.ema(prices, 200)
            rsi = calc.rsi(prices, 14)
            atr = calc.atr(window, 14)
            adx_val = calc.adx(window, 14)
            macd = calc.macd(prices)
            bb = calc.bollinger(prices)
            vwap = calc.vwap(window)
            vol_ratio = calc.volume_ratio([c.volume for c in window], 20)
            c = candles[i]

            fv = FeatureVector(
                timestamp_ms=c.timestamp, symbol=symbol,
                open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                ema20=ema20, ema50=ema50, ema200=ema200 or 0.0, rsi=rsi,
                macd_line=macd.get("macd", 0.0), macd_signal=macd.get("signal", 0.0),
                atr=atr, bb_upper=bb.get("upper", 0.0), bb_lower=bb.get("lower", 0.0),
                bb_middle=bb.get("middle", 0.0), adx=adx_val,
                volume_ma=0.0, volume_ratio=vol_ratio, obv=0.0, vwap=vwap,
                ema_bullish=ema20 > ema50 if ema20 and ema50 else False,
                price_above_ema50=c.close > ema50 if ema50 else False,
                rsi_overbought=rsi > 70, rsi_oversold=rsi < 30,
                htf_ema50=None, htf_ema200=None, htf_trend=None, integrity_score=1.0,
            )

            direction = sg.decide(symbol, fv, bar_idx=0)
            if direction:
                dt = datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc)
                print(f"  SIGNAL: {dt} {direction} RSI={rsi:.0f} ADX={adx_val:.0f} ts={c.timestamp}")


asyncio.run(main())
