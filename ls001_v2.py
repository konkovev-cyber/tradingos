"""
LS-001 v2 — Liquidity Sweep с conditional-фильтром на основе Edge Mining.

Не меняет параметры стратегии. Добавляет фильтр условий,
в которых edge статистически подтверждён.

Filter: ADX<15 AND hour in {12,17,19,21} AND day != Wed
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("LS001v2")

_ROOT = Path(__file__).parent

_rr_spec = importlib.util.spec_from_file_location("rr", str(_ROOT / "research_runner.py"))
_rr_mod = importlib.util.module_from_spec(_rr_spec)
_rr_spec.loader.exec_module(_rr_mod)

_cse_spec = importlib.util.spec_from_file_location(
    "cse", str(_ROOT / "core" / "execution" / "crypto_shadow.py")
)
_cse_mod = importlib.util.module_from_spec(_cse_spec)
_cse_spec.loader.exec_module(_cse_mod)
CryptoShadowExecutor = _cse_mod.CryptoShadowExecutor
PROFILE_B = _cse_mod.PROFILE_B

# Conditional filter from Edge Mining
GOOD_HOURS = {12, 17, 19, 21}
BAD_DAYS = {"Wed"}
ADX_MAX = 15


def passes_filter(ts: float, adx: float) -> bool:
    """Returns True if candle conditions match the discovered edge."""
    if ts > 1e12:
        ts = ts / 1000.0
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt.hour not in GOOD_HOURS:
        return False
    if dt.strftime("%a") in BAD_DAYS:
        return False
    if adx >= ADX_MAX:
        return False
    return True


async def run(symbol: str, timeframe: str, months: int):
    cache_path = _ROOT / "core" / "data_lake" / "data" / "cache" / f"{symbol}_{timeframe}_{months}m.json"
    if not cache_path.exists():
        print(f"No cache: {cache_path}")
        return

    with open(cache_path) as f:
        candles = json.load(f)
    candles.sort(key=lambda c: c.get("time", 0))
    print(f"Loaded {len(candles)} candles from cache")

    strategy = _rr_mod.LS1Strategy()
    executor = CryptoShadowExecutor(risk_config=PROFILE_B)

    filtered = 0
    passed = 0
    for i, candle in enumerate(candles):
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
            filtered += 1
            if passes_filter(ts, adx):
                passed += 1
                meta = dict(signal.get("metadata", {}))
                meta.setdefault("current_price", md["close"])
                sig = type('Sig', (), {
                    'symbol': symbol,
                    'direction': signal["direction"],
                    'confidence': signal["confidence"],
                    'metadata': meta,
                    'timestamp': time.time(),
                })()
                await executor.process_signal(sig)

        await executor.update_positions(md)

    stats = executor.get_stats()
    print(f"\n=== LS-001 v2 (Conditional Filter) ===")
    print(f"Symbol: {symbol} {timeframe}")
    print(f"Candles: {len(candles)}")
    print(f"Signals generated: {filtered}")
    print(f"Signals after filter: {passed} ({passed/max(filtered,1)*100:.0f}% of signals)")
    print(f"Trades opened: {stats['positions_opened']}")
    print(f"Trades closed: {stats['positions_closed']}")
    print(f"Win rate: {stats['win_rate']:.1%}")
    print(f"Total PnL: {stats['total_pnl']:+.2f}")
    print(f"Avg MFE: {stats['avg_mfe']:.2f}")
    print(f"Avg MAE: {stats['avg_mae']:.2f}")
    print(f"Exit reasons: {stats['exit_reasons']}")

    # Compare to unfiltered
    print(f"\n--- Comparison ---")
    print(f"Unfiltered LS-001 B: 2,898 trades, 53% WR, PF 1.33, +5,210")
    print(f"Filtered LS-001 v2:   {stats['positions_closed']} trades, {stats['win_rate']:.1%} WR, PnL {stats['total_pnl']:+.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--months", type=int, default=12)
    args = parser.parse_args()
    asyncio.run(run(args.symbol, args.timeframe, args.months))