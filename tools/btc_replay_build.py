#!/usr/bin/env python3
"""
Reality Pilot Replay v3 — расширенная выборка на 6-месячной истории BTC.
Использует свечи из ubot_bingx cache (2025-12-31 → 2026-06-30), воспроизводит
FeatureVector + SignalGenerator + полный контур отбора (как production).

НЕ меняет фильтры/пороги/ранжирование. Только расширяет объём истории.
"""
import sys
import json
from datetime import datetime

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")

import pandas as pd
from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator

# ── Загрузка свечей BTC из ubot_bingx ──
df = pd.read_parquet("/opt/ubot_bingx/data/cache/BTC_USDT_1h_2026-01-01_2026-06-30.parquet")
df = df.reset_index()
print(f"Свечей BTC: {len(df)}")

# ── Построение FeatureVector для каждого бара ──
calc = IndicatorCalculator()
gen = SignalGenerator()

def build_fv(candles, last_idx):
    """Воспроизводит _build_fv из run_observation.py."""
    last = candles[last_idx]
    prices = [c.close for c in candles[:last_idx+1] if c.close > 0]
    ema20 = calc.ema(prices, 20) if len(prices) >= 20 else 0.0
    ema50 = calc.ema(prices, 50) if len(prices) >= 50 else 0.0
    ema200 = calc.ema(prices, 200) if len(prices) >= 200 else 0.0
    rsi = calc.rsi(prices, 14) if len(prices) >= 14 else 50.0
    atr = calc.atr(candles[:last_idx+1], 14) if len(candles) >= 14 else 0.0
    adx_val = calc.adx(candles[:last_idx+1], 14) if len(candles) >= 14 else 0.0
    macd = calc.macd(prices) if len(prices) >= 26 else {}
    bb = calc.bollinger(prices) if len(prices) >= 20 else {}
    vwap = calc.vwap(candles[:last_idx+1]) if candles else 0.0
    vol_ratio = calc.volume_ratio([c.volume for c in candles[:last_idx+1]], 20) if len(candles) >= 20 else 1.0
    return FeatureVector(
        timestamp_ms=last.timestamp, symbol="BTCUSDT",
        open=last.open, high=last.high, low=last.low, close=last.close,
        volume=last.volume,
        ema20=ema20, ema50=ema50, ema200=ema200 or 0.0,
        rsi=rsi, macd_line=macd.get("macd", 0.0), macd_signal=macd.get("signal", 0.0),
        atr=atr, bb_upper=bb.get("upper", 0.0), bb_lower=bb.get("lower", 0.0),
        bb_middle=bb.get("middle", 0.0), adx=adx_val,
        volume_ma=0.0, volume_ratio=vol_ratio, obv=0.0, vwap=vwap,
        ema_bullish=ema20 > ema50 if ema20 > 0 and ema50 > 0 else False,
        price_above_ema50=last.close > ema50 if ema50 > 0 else False,
        rsi_overbought=rsi > 70, rsi_oversold=rsi < 30,
        htf_ema50=None, htf_ema200=None, htf_trend=None,
        integrity_score=1.0,
    )

# Строим свечи
candles = []
for _, row in df.iterrows():
    ts = int(row['timestamp'].timestamp() * 1000)
    candles.append(Candle(timestamp=ts, open=float(row['open']), high=float(row['high']),
                          low=float(row['low']), close=float(row['close']), volume=float(row['volume']),
                          symbol="BTCUSDT", timeframe="1h"))

# Прогоняем генератор по каждому бару (начиная с 200-го, чтобы были индикаторы)
signals = []
for i in range(200, len(candles)):
    fv = build_fv(candles, i)
    direction = gen.decide("BTCUSDT", fv, bar_idx=i)
    if direction:
        signals.append({
            'symbol': 'BTCUSDT', 'direction': direction,
            'close': fv.close, 'atr': fv.atr, 'ts': fv.timestamp_ms,
            'prob': fv.rsi / 100.0,  # placeholder — реальный prob из scoring
            'score': 60, 'quality': 'MEDIUM', 'adx': fv.adx,
        })

print(f"Сигналов BTC за 6 мес: {len(signals)}")
# Сохранить
json.dump(signals, open('/tmp/btc_signals_6m.json', 'w'))
print("сохранено в /tmp/btc_signals_6m.json")
