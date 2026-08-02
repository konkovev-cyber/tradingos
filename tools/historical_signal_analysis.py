#!/usr/bin/env python3
"""
Historical Signal Generator Analysis.

Измеряет ожидаемую частоту сигналов на исторических данных.

НЕ является стратегией.
НЕ является бэктестом.
НЕ меняет архитектуру.

Просто запускает существующий SignalGenerator на прошлых данных
и считает статистику.

Usage:
    python3 tools/historical_signal_analysis.py --days 30
    python3 tools/historical_signal_analysis.py --days 7 --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time as time_module
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent  # /root/tradingos
import sys
sys.path.insert(0, str(ROOT.parent))  # /root для from tradingos.*

from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator


SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
INTERVAL_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}


async def fetch_history(
    symbol: str,
    interval: str = "1h",
    days: int = 30,
    proxy: str = "",
) -> list[dict]:
    """Fetch historical klines from Bybit."""
    import httpx

    kline_interval = INTERVAL_MAP.get(interval, "60")
    limit = 200
    total_bars_needed = days * (1440 // int(interval.replace("m", "").replace("h", "60").replace("d", "1440")))
    # Simplified: for 1h, bars = days * 24
    if interval == "1h":
        total_bars_needed = days * 24
    elif interval == "15m":
        total_bars_needed = days * 96

    all_candles = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    
    client_args = {"timeout": 15}
    if proxy:
        client_args["proxies"] = proxy

    async with httpx.AsyncClient(**client_args) as client:
        while len(all_candles) < total_bars_needed:
            params = {
                "category": "linear",
                "symbol": symbol,
                "interval": kline_interval,
                "limit": limit,
            }
            if end_ms:
                params["end"] = str(end_ms)

            resp = await client.get(
                "https://api.bybit.com/v5/market/kline", params=params
            )
            data = resp.json()
            if data.get("retCode") != 0:
                print(f"  API error: {data.get('retMsg')}")
                break

            rows = data.get("result", {}).get("list", [])
            if not rows:
                break

            for row in rows:
                all_candles.append({
                    "timestamp": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]) if row[5] else 0.0,
                })

            # Move cursor backward
            oldest_ts = int(rows[-1][0])
            end_ms = oldest_ts - 1

            await asyncio.sleep(0.1)  # rate limit

    # Sort chronologically
    all_candles.sort(key=lambda x: x["timestamp"])
    return all_candles[-total_bars_needed:]


def candles_to_feature_vectors(
    candles_raw: list[dict], symbol: str
) -> list[FeatureVector]:
    """Convert raw OHLCV list to FeatureVectors using IndicatorCalculator."""
    from tradingos.data.models.candle import Candle

    # Convert dicts to Candle objects
    candles = [
        Candle(
            timestamp=c["timestamp"],
            open=c["open"], high=c["high"],
            low=c["low"], close=c["close"],
            volume=c["volume"],
            symbol=symbol, timeframe="1h",
        )
        for c in candles_raw
    ]

    calc = IndicatorCalculator()
    n = len(candles)
    if n < 200:
        return []

    closes = [c.close for c in candles]
    fvs = []

    # We need at least 200 bars for EMA200, 26 for MACD, 20 for BB
    min_bars = max(200, 26, 20)

    for i in range(min_bars, n):
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
        vol_ratio = calc.volume_ratio(
            [c.volume for c in window], 20
        )

        c = candles[i]
        fv = FeatureVector(
            timestamp_ms=c.timestamp,
            symbol=symbol,
            open=c.open, high=c.high,
            low=c.low, close=c.close,
            volume=c.volume,
            ema20=ema20, ema50=ema50, ema200=ema200 or 0.0,
            rsi=rsi,
            macd_line=macd.get("macd", 0.0),
            macd_signal=macd.get("signal", 0.0),
            atr=atr,
            bb_upper=bb.get("upper", 0.0),
            bb_lower=bb.get("lower", 0.0),
            bb_middle=bb.get("middle", 0.0),
            adx=adx_val,
            volume_ma=0.0, volume_ratio=vol_ratio,
            obv=0.0, vwap=vwap,
            ema_bullish=ema20 > ema50 if ema20 and ema50 else False,
            price_above_ema50=c.close > ema50 if ema50 else False,
            rsi_overbought=rsi > 70,
            rsi_oversold=rsi < 30,
            htf_ema50=None, htf_ema200=None, htf_trend=None,
            integrity_score=1.0,
        )
        fvs.append(fv)

    return fvs


async def analyze(symbol: str, interval: str, days: int):
    """Полный анализ одного символа."""
    print(f"\n--- {symbol} ({interval}, {days} days) ---")
    print(f"Fetching data...")

    candles = await fetch_history(symbol, interval, days)
    print(f"  Received: {len(candles)} candles")

    if len(candles) < 200:
        print(f"  SKIP: need ≥200 candles, got {len(candles)}")
        return

    fvs = candles_to_feature_vectors(candles, symbol)
    print(f"  FeatureVectors: {len(fvs)}")

    if not fvs:
        return

    sg = SignalGenerator()
    stats = Counter()
    signals = []

    for fv in fvs:
        direction = sg.decide(symbol, fv, bar_idx=0)

        if direction == "BUY":
            stats["BUY"] += 1
            signals.append((fv.timestamp_ms, "BUY", fv.rsi, fv.adx))
        elif direction == "SELL":
            stats["SELL"] += 1
            signals.append((fv.timestamp_ms, "SELL", fv.rsi, fv.adx))
        else:
            stats["NONE"] += 1

    total = sum(stats.values())
    signal_count = stats.get("BUY", 0) + stats.get("SELL", 0)
    days_span = days

    print(f"\n  RESULTS:")
    print(f"  Period covered:     {days_span} days")
    print(f"  Total bars:         {total}")
    print(f"  Signals:            {signal_count}")
    print(f"  BUY:                {stats.get('BUY', 0)}")
    print(f"  SELL:               {stats.get('SELL', 0)}")
    print(f"  NONE (rejected):    {stats.get('NONE', 0)}")
    print(f"  Signals/day:        {signal_count / max(days_span, 1):.2f}")
    print(f"  Accept rate:        {signal_count / max(total, 1) * 100:.2f}%")

    if signals:
        print(f"\n  Signal details (first 10):")
        for ts, d, rsi, adx in signals[:10]:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"    {dt} {d} RSI={rsi:.0f} ADX={adx:.0f}")
        if len(signals) > 10:
            print(f"    ... and {len(signals) - 10} more")

    sg_stats = sg.stats
    print(f"\n  SignalGenerator internals:")
    print(f"    Total evaluated:    {sg_stats['total_evaluated']}")
    print(f"    Accepeted signals:  {sg_stats['signals_total']}")
    print(f"    Buy:                {sg_stats['buy_signals']}")
    print(f"    Sell:               {sg_stats['sell_signals']}")
    print(f"    Rejected prob:      {sg_stats.get('rejected_probability', 0)}")
    print(f"    Rejected no score:  {sg_stats.get('rejected_no_score', 0)}")
    print(f"    Cooldown skipped:   {sg_stats.get('cooldown_skipped', 0)}")

    return {
        "symbol": symbol,
        "interval": interval,
        "days": days_span,
        "total_bars": total,
        "signals": signal_count,
        "buy": stats.get("BUY", 0),
        "sell": stats.get("SELL", 0),
        "none": stats.get("NONE", 0),
        "signals_per_day": signal_count / max(days_span, 1),
        "accept_rate": signal_count / max(total, 1) * 100,
    }


async def main():
    parser = argparse.ArgumentParser(description="SignalGenerator frequency analysis")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS, help="Symbols to analyze")
    parser.add_argument("--interval", default="1h", choices=["15m", "1h", "4h"])
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    args = parser.parse_args()

    print("=" * 60)
    print("SIGNAL GENERATOR FREQUENCY ANALYSIS")
    print(f"Interval: {args.interval} | Days: {args.days} | Symbols: {args.symbols}")
    print("=" * 60)

    all_results = []
    for symbol in args.symbols:
        r = await analyze(symbol, args.interval, args.days)
        if r:
            all_results.append(r)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in all_results:
        print(f"  {r['symbol']}: {r['signals']} signals in {r['days']}d "
              f"({r['signals_per_day']:.2f}/day, accept {r['accept_rate']:.1f}%)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
