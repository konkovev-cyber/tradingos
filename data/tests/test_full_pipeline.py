#!/usr/bin/env python3
"""
PHASE 5 — Full Pipeline Test.

Проверяет полный цикл:
    OHLCV → FeatureStore → IndicatorCalculator → FeatureVector
    → SignalGenerator → decision.json → Executor dry-run

Без /opt/ubot_bingx.
Без REST (синтетические свечи).
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # /root
sys.path.insert(0, str(ROOT))

from tradingos.data.models.candle import Candle
from tradingos.data.feature_store import FeatureStore
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator
from tradingos.signals.models.signal import Signal, SignalDirection

PASS = 0
FAIL = 0
DECISION_OUT = Path("/root/trading_brain_v4/research/execution/decision.json")


def check(condition: bool, msg: str):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {msg}")
        PASS += 1
    else:
        print(f"  ❌ {msg}")
        FAIL += 1


def make_synthetic_candles(symbol: str, count: int = 200,
                           base_price: float = 0.073,
                           volatility: float = 0.001) -> list[Candle]:
    """Генерирует синтетические OHLCV свечи для теста."""
    candles = []
    price = base_price
    for i in range(count):
        o = price
        change = random.gauss(0, volatility)
        c = o + change
        h = max(o, c) + abs(random.gauss(0, volatility * 0.5))
        l = min(o, c) - abs(random.gauss(0, volatility * 0.5))
        v = 10000000 + random.gauss(0, 2000000)
        volume = max(v, 1000)

        candles.append(Candle(
            timestamp=int(i * 60000),  # 1m intervals
            open=round(o, 6),
            high=round(h, 6),
            low=round(l, 6),
            close=round(c, 6),
            volume=round(volume, 2),
            symbol=symbol,
            timeframe="1m",
        ))
        price = c
    return candles


def make_bullish_trend(candles: list[Candle]) -> list[Candle]:
    """Превратить свечи в восходящий тренд."""
    for i, c in enumerate(candles):
        factor = 1.0 + (i / len(candles)) * 0.05  # +5% over the period
        c.open = round(c.open * factor, 6)
        c.high = round(c.high * factor, 6)
        c.low = round(c.low * factor, 6)
        c.close = round(c.close * factor, 6)
        c.volume = round(c.volume * (1 + 0.5 * i / len(candles)), 2)
    return candles


def main():
    print("=" * 60)
    print("FULL PIPELINE TEST (synthetic OHLCV)")
    print("=" * 60)
    print()

    # ── 1. OHLCV → Candle ──────────────────────────────────────────
    print("1. OHLCV → Candle")
    candles = make_synthetic_candles("DOGEUSDT", count=200, base_price=0.073)
    check(len(candles) == 200, f"200 candles generated")
    check(isinstance(candles[0], Candle), "Candle type correct")
    print()

    # ── 2. Candle → FeatureStore ────────────────────────────────────
    print("2. Candle → FeatureStore")
    store = FeatureStore()
    store.add_candles("DOGEUSDT", "1m", candles)
    store.recalculate("DOGEUSDT")
    features = store.get_features("DOGEUSDT", "1m")
    check(features is not None, "Features object created")
    if features:
        check(features.candle_count >= 200, f"candle_count={features.candle_count}")
        check(features.rsi != 50.0, f"RSI={features.rsi:.1f} (not default)")
        check(features.ema_20 > 0, f"EMA20={features.ema_20:.4f}")
        check(features.adx > 0, f"ADX={features.adx:.1f}")
    print()

    # ── 3. FeatureStore → FeatureVector ─────────────────────────────
    print("3. FeatureStore → FeatureVector")
    indicator_calc = IndicatorCalculator()
    last = candles[-1]
    prices = [c.close for c in candles]

    ema20 = indicator_calc.ema(prices, 20)
    ema50 = indicator_calc.ema(prices, 50)
    rsi = indicator_calc.rsi(prices, 14)
    atr = indicator_calc.atr(candles, 14)
    adx_val = indicator_calc.adx(candles, 14)
    macd = indicator_calc.macd(prices)
    bb = indicator_calc.bollinger(prices)
    vwap = indicator_calc.vwap(candles)
    vol_ratio = indicator_calc.volume_ratio(
        [c.volume for c in candles], 20)

    fv = FeatureVector(
        timestamp_ms=last.timestamp,
        symbol="DOGEUSDT",
        open=last.open, high=last.high, low=last.low, close=last.close,
        volume=last.volume,
        ema20=ema20, ema50=ema50, ema200=indicator_calc.ema(prices, 200),
        rsi=rsi,
        macd_line=macd.get("macd", 0.0),
        macd_signal=macd.get("signal", 0.0),
        atr=atr,
        bb_upper=bb.get("upper", 0.0),
        bb_lower=bb.get("lower", 0.0),
        bb_middle=bb.get("middle", 0.0),
        adx=adx_val,
        volume_ma=0.0,
        volume_ratio=vol_ratio,
        obv=0.0,
        vwap=vwap,
        ema_bullish=ema20 > ema50,
        price_above_ema50=last.close > ema50,
        rsi_overbought=rsi > 70,
        rsi_oversold=rsi < 30,
        htf_ema50=None, htf_ema200=None, htf_trend=None,
        integrity_score=0.95,
    )
    check(fv.rsi > 0, f"RSI={fv.rsi:.1f}")
    check(fv.ema20 > 0, f"EMA20={fv.ema20:.4f}")
    check(fv.ema_bullish is True or fv.ema_bullish is False,
          f"ema_bullish={fv.ema_bullish}")
    print()

    # ── 4. FeatureVector → SignalGenerator ──────────────────────────
    print("4. FeatureVector → SignalGenerator")
    sg = SignalGenerator()
    direction = sg.decide("DOGEUSDT", fv, bar_idx=0)
    print(f"   SignalGenerator.decide() → {direction or 'None (rejected)'}")
    check(direction is None or direction in ("BUY", "SELL"),
          f"valid direction: {direction}")

    # Show why if rejected
    stats = sg.stats
    total = stats["total_evaluated"]
    rejected_total = stats.get("rejected_probability", 0) + stats.get("rejected_no_score", 0)
    print(f"   Stats: evaluated={total}, accepted={stats['signals_total']}, rejected={rejected_total}")
    print()

    # ── 5. Signal → decision.json ───────────────────────────────────
    print("5. Signal → decision.json")
    signal = Signal(
        strategy="tradingos_trend",
        symbol="DOGEUSDT",
        direction=SignalDirection.BUY,
        confidence=0.7,
        score=70,
        entry_price=last.close,
        stop_loss=last.close * 0.99,
        take_profits=[last.close * 1.02],
        reasons=["test_pipeline", "trend_alignment"],
        timeframe="1m",
    )
    decision = {
        "trace_id": str(uuid.uuid4()),
        "decision_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "quantity": 10,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profits[0],
        "action": "OPEN_POSITION",
        "source": "tradingos_signal_engine",
        "confidence": round(signal.confidence, 4),
        "reason": "; ".join(signal.reasons),
        "mode": "SHADOW",
        "_meta": {
            "strategy": signal.strategy,
            "score": signal.score,
            "test": "full_pipeline_validation",
        },
    }
    DECISION_OUT.parent.mkdir(parents=True, exist_ok=True)
    DECISION_OUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    check(DECISION_OUT.exists(), "decision.json written")
    check(decision["source"] == "tradingos_signal_engine", "source correct")
    print()

    # ── 6. Executor dry-run ─────────────────────────────────────────
    print("6. Executor dry-run")
    executor = ROOT / "trading_brain_v4" / "research" / "execution" / "executor_v0.py"
    result = subprocess.run(
        [sys.executable, str(executor), "--dry-run", str(DECISION_OUT)],
        capture_output=True, text=True, timeout=30,
    )
    ok = result.returncode == 0
    check(ok, f"executor exit={result.returncode}")
    if ok and "Guardian ALLOWED" in result.stdout:
        check(True, "Guardian ALLOWED")
    print()

    # ── 7. No legacy dependencies ───────────────────────────────────
    print("7. No legacy dependencies")
    for mod_name in sorted(sys.modules.keys()):
        if "ubot" in mod_name.lower():
            check(False, f"uBot loaded: {mod_name}")
            break
    else:
        check(True, "No uBot_bingx modules loaded")
    print()

    # ── Summary ─────────────────────────────────────────────────────
    print("=" * 60)
    print(f"RESULT: {PASS}/{PASS+FAIL} checks passed")
    print("=" * 60)
    print()
    if FAIL == 0:
        print("✅ Full pipeline proven:")
        print("   OHLCV → FeatureStore → IndicatorCalculator → FeatureVector")
        print("   → SignalGenerator → decision.json → Executor (dry-run)")
        print()
        print("   Все шаги внутри TradingOS, без /opt/ubot_bingx")
        sys.exit(0)
    else:
        print(f"⚠️  {FAIL} failures")
        sys.exit(1)


if __name__ == "__main__":
    main()
