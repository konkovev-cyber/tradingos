#!/usr/bin/env python3
"""
Test: CryptoAdapter + LiquiditySweepStrategy on Bybit testnet.

Usage:
  python test_crypto_pipeline.py BTCUSDT

NOTE: This is a CLI integration test, not a unit test. It requires
a missing module (core.regime_brain) and is skipped by pytest.
Run it manually as a script, not via pytest.
"""
import pytest

# Skip entire module from pytest collection — it's a CLI script, not a test
pytest.skip("CLI integration test (not a unit test) — run manually, not via pytest",
            allow_module_level=True)

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path

# Add trading_brain_v4 to sys.path (needed for BybitAdapter, RegimeBrain, BaseStrategy)
_TB4 = "/root/trading_brain_v4"
if _TB4 not in sys.path:
    sys.path.insert(0, _TB4)

# Import CryptoAdapter via file path (it handles its own imports)
_ca_spec = importlib.util.spec_from_file_location(
    "crypto_adapter", str(Path(__file__).parent / "adapters" / "crypto_adapter.py")
)
_ca_mod = importlib.util.module_from_spec(_ca_spec)
_ca_spec.loader.exec_module(_ca_mod)
CryptoAdapter = _ca_mod.CryptoAdapter

# Import LiquiditySweepStrategy via file path
_ls_spec = importlib.util.spec_from_file_location(
    "liquidity_sweep", str(Path(__file__).parent / "core" / "strategy" / "liquidity_sweep.py")
)
_ls_mod = importlib.util.module_from_spec(_ls_spec)
_ls_spec.loader.exec_module(_ls_mod)
LiquiditySweepStrategy = _ls_mod.LiquiditySweepStrategy

# Import DataLake via file path
_dl_spec = importlib.util.spec_from_file_location(
    "sqlite_backend", str(Path(__file__).parent / "core" / "data_lake" / "sqlite_backend.py")
)
_dl_mod = importlib.util.module_from_spec(_dl_spec)
_dl_spec.loader.exec_module(_dl_mod)
DataLake = _dl_mod.DataLake

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TestCryptoPipeline")

DB_PATH = Path(__file__).parent / "tradingos_data.db"


async def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"

    print("=" * 60)
    print("  CRYPTO PIPELINE TEST — Bybit testnet")
    print("=" * 60)

    # 1. Init adapter
    print(f"\n[1] Initializing CryptoAdapter for {symbol}...")
    adapter = CryptoAdapter(
        api_key=os.environ.get("BYBIT_API_KEY", ""),
        api_secret=os.environ.get("BYBIT_API_SECRET", ""),
        testnet=True,
    )
    ok = await adapter.initialize()
    print(f"    Connected: {ok}")
    if not ok:
        print("    FAIL: cannot connect to Bybit testnet")
        return

    # 2. Collect OHLCV → Data Lake
    print(f"\n[2] Collecting OHLCV data...")
    written = await adapter.collect_once(symbol, timeframe="15m", limit=200)
    print(f"    Events written: {written}")
    if written == 0:
        print("    FAIL: no data collected")
        adapter.close()
        return

    # 3. Verify Data Lake
    print(f"\n[3] Verifying Data Lake...")
    lake = DataLake(DB_PATH)
    total = lake.count()
    candles = lake.count(event_type="CandleClosed")
    regimes = lake.count(event_type="RegimeDetected")
    print(f"    Total events: {total}")
    print(f"    CandleClosed: {candles}")
    print(f"    RegimeDetected: {regimes}")

    # 4. Run LiquiditySweep strategy on collected data
    print(f"\n[4] Running LiquiditySweep strategy...")
    strategy = LiquiditySweepStrategy(lookback=50, sweep_threshold=0.0015)

    # Read candles from Data Lake
    rows = lake.query(event_type="CandleClosed", symbol=symbol, limit=200)
    rows.reverse()  # oldest first

    signals = []
    for row in rows:
        payload = row.get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)

        market_data = {
            "symbol": symbol,
            "high": payload.get("high", 0),
            "low": payload.get("low", 0),
            "close": payload.get("close", 0),
            "volume": payload.get("volume", 0),
            "regime": payload.get("regime", "UNKNOWN"),
        }
        signal = strategy.analyze(market_data)
        if signal:
            signals.append(signal)

    # 5. Results
    print(f"\n[5] Results:")
    stats = strategy.get_stats()
    print(f"    Bars analyzed:  {stats['total_analyzed']}")
    print(f"    Signals:        {stats['accepted']}")
    print(f"    Accept rate:    {stats['accept_rate']:.2%}")
    print(f"    Sweeps found:   {stats['sweeps_detected']}")

    if signals:
        print(f"\n    Last {min(5, len(signals))} signals:")
        for s in signals[-5:]:
            meta = s.metadata or {}
            print(f"      {s.direction:5s} conf={s.confidence:.3f} "
                  f"type={meta.get('sweep_type','?'):15s} "
                  f"strength={meta.get('strength',0):.6f}")

    # 6. Summary
    verdict = "PASS" if stats["accepted"] > 0 else "MARGINAL (no sweeps in this window)"
    print(f"\n{'='*60}")
    print(f"  VERDICT: {verdict}")
    print(f"{'='*60}")

    adapter.close()
    lake.close()


if __name__ == "__main__":
    asyncio.run(main())
