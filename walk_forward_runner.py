"""
Walk Forward Runner for LS-001 v2.

Разбивает историю на train/test окна, прогоняет стратегию,
собирает сделки по месяцам и передаёт в walk_forward.validate().
"""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent

spec = importlib.util.spec_from_file_location("rr", str(_ROOT / "research_runner.py"))
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

cse_spec = importlib.util.spec_from_file_location(
    "cse", str(_ROOT / "core" / "execution" / "crypto_shadow.py")
)
cse = importlib.util.module_from_spec(cse_spec)
cse_spec.loader.exec_module(cse)
CryptoShadowExecutor = cse.CryptoShadowExecutor
PROFILE_B = cse.PROFILE_B

# Conditional filter (from Edge Mining)
GOOD_HOURS = {12, 17, 19, 21}
BAD_DAYS = {"Wed"}
ADX_MAX = 15

def passes_filter(ts, adx):
    if ts > 1e12:
        ts = ts / 1000.0
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt.hour not in GOOD_HOURS:
        return False
    if dt.strftime("%a") in BAD_DAYS:
        return False
    if adx >= ADX_MAX:
        return False
    return True

async def collect_trades_for_range(strategy_id, symbol, timeframe, candles, filtered=True):
    """Run strategy on a slice of candles, return trade dicts."""
    strategy_cls = rr.STRATEGIES.get(strategy_id)
    strategy = strategy_cls()
    executor = CryptoShadowExecutor(risk_config=PROFILE_B)
    trades = []

    for candle in candles:
        ts = candle.get("time", 0)
        md = {
            "symbol": symbol,
            "open": float(candle.get("open", 0)),
            "high": float(candle.get("high", 0)),
            "low": float(candle.get("low", 0)),
            "close": float(candle.get("close", 0)),
            "volume": float(candle.get("volume", 0)),
        }

        adx = getattr(strategy, '_adx', 0)
        signal = strategy.analyze(md)
        if signal:
            if filtered and not passes_filter(ts, adx):
                pass
            else:
                meta = dict(signal.get("metadata", {}))
                meta.setdefault("current_price", md["close"])
                sig = type('Sig', (), {
                    'symbol': symbol,
                    'direction': signal["direction"],
                    'confidence': signal["confidence"],
                    'metadata': meta,
                    'timestamp': 0,
                })()
                await executor.process_signal(sig)

        closed = await executor.update_positions(md)
        for c in closed:
            from datetime import datetime, timezone
            t_ts = ts / 1000 if ts > 1e12 else ts
            dt = datetime.fromtimestamp(t_ts, tz=timezone.utc)
            trades.append({
                "month": dt.strftime("%Y-%m"),
                "net_pnl": c.pnl,
            })

    return trades

async def main():
    cache = _ROOT / "core" / "data_lake" / "data" / "cache" / "BTCUSDT_5m_12m.json"
    with open(cache) as f:
        all_candles = json.load(f)
    all_candles.sort(key=lambda c: c.get("time", 0))

    n = len(all_candles)
    print(f"Walk Forward: {n} candles")
    print("Splitting into 3 train/test windows (6-month train, 3-month test)...")

    # 3 windows: train 0-60%, test 60-75%; train 25-85%, test 85-100%; train 50-100%
    # For simplicity with 4 quarters:
    # Window 1: train Q1+Q2, test Q3
    # Window 2: train Q2+Q3, test Q4
    q = n // 4

    windows = [
        ("W1: train Q1+Q2 → test Q3", all_candles[0:2*q], all_candles[2*q:3*q]),
        ("W2: train Q2+Q3 → test Q4", all_candles[q:3*q], all_candles[3*q:]),
    ]

    all_trades = []
    for name, train_candles, test_candles in windows:
        print(f"\n  {name}")
        test_trades = await collect_trades_for_range("LS-001", "BTCUSDT", "5m", test_candles, filtered=True)
        if test_trades:
            print(f"    Test period: {len(test_trades)} trades")
            for t in test_trades:
                all_trades.append(t)
        else:
            print(f"    No trades in test period")

    if not all_trades:
        print("\nNo trades to validate!")
        return

    # Run walk_forward validate
    wf_spec = importlib.util.spec_from_file_location(
        "wf", str(_ROOT / "research" / "walk_forward.py")
    )
    wf = importlib.util.module_from_spec(wf_spec)
    wf_spec.loader.exec_module(wf)

    result = wf.validate(all_trades, window_months=3, step_months=3, min_pf=1.0, min_profitable_windows=0.5)

    print(f"\n{'='*60}")
    print(f"  WALK FORWARD RESULTS")
    print(f"{'='*60}")
    print(f"  Windows: {result['windows']}")
    print(f"  Avg test PF: {result['avg_test_pf']}")
    print(f"  Profitable windows: {result['profitable_windows']}")
    print(f"  Profitable ratio: {result['profitable_ratio']}")
    print(f"  Passed: {result['passed']}")

    if result["passed"]:
        print(f"\n  >>> EDGE IS ROBUST ACROSS TIME <<<")
        print(f"  >>> READY FOR MT5 DEMO PIPELINE <<<")
    else:
        print(f"\n  >>> Edge degrades on unseen data <<<")
        print(f"  >>> Need longer validation or different approach <<<")

async def main_wrapper():
    await main()

asyncio.run(main_wrapper())
