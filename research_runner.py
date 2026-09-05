"""
Research Runner — запускает стратегию в Shadow режиме на Bybit testnet.

Usage:
  python research_runner.py MR-001 --symbol BTCUSDT --timeframe 5m
  python research_runner.py VOL-001 --symbol BTCUSDT --timeframe 15m
  python research_runner.py TF-001 --symbol BTCUSDT --timeframe 1h
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ResearchRunner")

_ROOT = Path(__file__).parent

# ── Imports ────────────────────────────────────────────────

_ca_spec = importlib.util.spec_from_file_location(
    "crypto_adapter", str(_ROOT / "adapters" / "crypto_adapter.py")
)
_ca_mod = importlib.util.module_from_spec(_ca_spec)
_ca_spec.loader.exec_module(_ca_mod)
CryptoAdapter = _ca_mod.CryptoAdapter

_cse_spec = importlib.util.spec_from_file_location(
    "crypto_shadow", str(_ROOT / "core" / "execution" / "crypto_shadow.py")
)
_cse_mod = importlib.util.module_from_spec(_cse_spec)
_cse_spec.loader.exec_module(_cse_mod)
CryptoShadowExecutor = _cse_mod.CryptoShadowExecutor
PROFILE_A = _cse_mod.PROFILE_A
PROFILE_B = _cse_mod.PROFILE_B
PROFILE_C = _cse_mod.PROFILE_C


# ── Strategy Implementations ──────────────────────────────

class MR1Strategy:
    """VWAP Mean Reversion — simplified for BTCUSDT."""

    def __init__(self):
        self._prev_close = 0.0
        self._true_ranges = []
        self._atr = 0.0
        self._adx = 0.0
        self._prices = []
        self._stats = {"total": 0, "signals": 0}

    def get_name(self):
        return "MR-001"

    def analyze(self, md: dict) -> Optional[dict]:
        self._stats["total"] += 1
        close = md.get("close", 0)
        high = md.get("high", 0)
        low = md.get("low", 0)
        volume = md.get("volume", 0)

        if close == 0:
            return None

        # Track ATR/ADX
        if self._prev_close > 0:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            self._true_ranges.append(tr)
            if len(self._true_ranges) > 14:
                self._true_ranges = self._true_ranges[-14:]
            if len(self._true_ranges) == 14:
                self._atr = sum(self._true_ranges) / 14

        price_change = 0.0
        if self._prev_close > 0:
            price_change = (close - self._prev_close) / self._prev_close
        if self._atr > 0:
            self._adx = min(100, abs(price_change) / self._atr * 1000)

        self._prev_close = close
        self._prices.append(close)
        if len(self._prices) > 20:
            self._prices.pop(0)

        # Need enough data
        if len(self._prices) < 20 or self._atr == 0:
            return None

        # Bollinger Bands (20, 2)
        sma20 = sum(self._prices) / len(self._prices)
        variance = sum((p - sma20) ** 2 for p in self._prices) / len(self._prices)
        std20 = variance ** 0.5
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20

        # Confirmation: previous bar was outside BB, this bar closes back inside
        prev_outside_upper = len(self._prices) >= 2 and self._prices[-2] > upper
        prev_outside_lower = len(self._prices) >= 2 and self._prices[-2] < lower

        if prev_outside_upper and close < upper:
            direction = "SELL"
            confidence = min(1.0, (upper - close) / std20)
            self._stats["signals"] += 1
            return {
                "direction": direction,
                "confidence": confidence,
                "price": close,
                "atr": self._atr,
                "adx": self._adx,
                "metadata": {
                    "bb_upper": round(upper, 2),
                    "bb_lower": round(lower, 2),
                    "deviation_std": round((self._prices[-2] - sma20) / std20, 2),
                    "atr": round(self._atr, 2),
                    "adx": round(self._adx, 4),
                    "strategy": "MR-001",
                },
            }
        elif prev_outside_lower and close > lower:
            direction = "BUY"
            confidence = min(1.0, (close - lower) / std20)
            self._stats["signals"] += 1
            return {
                "direction": direction,
                "confidence": confidence,
                "price": close,
                "atr": self._atr,
                "adx": self._adx,
                "metadata": {
                    "bb_upper": round(upper, 2),
                    "bb_lower": round(lower, 2),
                    "deviation_std": round((sma20 - self._prices[-2]) / std20, 2),
                    "atr": round(self._atr, 2),
                    "adx": round(self._adx, 4),
                    "strategy": "MR-001",
                },
            }
        return None

    def get_stats(self):
        return self._stats


class VOL1Strategy:
    """Volatility Compression Breakout — simplified."""

    def __init__(self):
        self._atr_values = []
        self._atr = 0.0
        self._prev_close = 0.0
        self._true_ranges = []
        self._highs = []
        self._lows = []
        self._stats = {"total": 0, "signals": 0}

    def get_name(self):
        return "VOL-001"

    def analyze(self, md: dict) -> Optional[dict]:
        self._stats["total"] += 1
        high = md.get("high", 0)
        low = md.get("low", 0)
        close = md.get("close", 0)

        if high == 0 or low == 0:
            return None

        if self._prev_close > 0:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            self._true_ranges.append(tr)
            if len(self._true_ranges) > 14:
                self._true_ranges = self._true_ranges[-14:]
            if len(self._true_ranges) == 14:
                self._atr = sum(self._true_ranges) / 14
                self._atr_values.append(self._atr)
                if len(self._atr_values) > 50:
                    self._atr_values.pop(0)

        self._prev_close = close
        self._highs.append(high)
        self._lows.append(low)
        if len(self._highs) > 20:
            self._highs.pop(0)
            self._lows.pop(0)

        if len(self._atr_values) < 50 or self._atr == 0:
            return None

        # ATR percentile
        atr_percentile = sum(1 for v in self._atr_values if v <= self._atr) / len(self._atr_values)

        # Compression: ATR in bottom 20%
        if atr_percentile > 0.2:
            return None

        # Donchian breakout
        dc_high = max(self._highs)
        dc_low = min(self._lows)

        if close > dc_high:
            self._stats["signals"] += 1
            return {
                "direction": "BUY",
                "confidence": 0.6,
                "price": close,
                "atr": self._atr,
                "adx": 0,
                "metadata": {
                    "atr_percentile": round(atr_percentile, 2),
                    "dc_high": round(dc_high, 2),
                    "strategy": "VOL-001",
                },
            }
        elif close < dc_low:
            self._stats["signals"] += 1
            return {
                "direction": "SELL",
                "confidence": 0.6,
                "price": close,
                "atr": self._atr,
                "adx": 0,
                "metadata": {
                    "atr_percentile": round(atr_percentile, 2),
                    "dc_low": round(dc_low, 2),
                    "strategy": "VOL-001",
                },
            }
        return None

    def get_stats(self):
        return self._stats


class TF1Strategy:
    """EMA Trend Pullback — simplified."""

    def __init__(self):
        self._prices = []
        self._prev_close = 0.0
        self._true_ranges = []
        self._atr = 0.0
        self._adx = 0.0
        self._stats = {"total": 0, "signals": 0}

    def get_name(self):
        return "TF-001"

    def analyze(self, md: dict) -> Optional[dict]:
        self._stats["total"] += 1
        close = md.get("close", 0)
        high = md.get("high", 0)
        low = md.get("low", 0)

        if close == 0:
            return None

        if self._prev_close > 0:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            self._true_ranges.append(tr)
            if len(self._true_ranges) > 14:
                self._true_ranges = self._true_ranges[-14:]
            if len(self._true_ranges) == 14:
                self._atr = sum(self._true_ranges) / 14

        price_change = 0.0
        if self._prev_close > 0:
            price_change = (close - self._prev_close) / self._prev_close
        if self._atr > 0:
            self._adx = min(100, abs(price_change) / self._atr * 1000)

        self._prev_close = close
        self._prices.append(close)
        if len(self._prices) > 200:
            self._prices.pop(0)

        if len(self._prices) < 200 or self._atr == 0:
            return None

        # EMA50, EMA200 proxies
        ema50 = sum(self._prices[-50:]) / 50
        ema200 = sum(self._prices) / len(self._prices)

        # ADX > 25 = trend
        if self._adx < 25:
            return None

        # Trend direction
        if ema50 > ema200:
            # Uptrend: price pulled back to EMA50
            pullback_dist = (close - ema50) / ema50
            if -0.005 < pullback_dist < 0.005:  # within 0.5% of EMA50
                self._stats["signals"] += 1
                return {
                    "direction": "BUY",
                    "confidence": 0.5,
                    "price": close,
                    "atr": self._atr,
                    "adx": self._adx,
                    "metadata": {
                        "ema50": round(ema50, 2),
                        "ema200": round(ema200, 2),
                        "pullback_pct": round(pullback_dist * 100, 4),
                        "strategy": "TF-001",
                    },
                }
        else:
            # Downtrend
            pullback_dist = (ema50 - close) / ema50
            if -0.005 < pullback_dist < 0.005:
                self._stats["signals"] += 1
                return {
                    "direction": "SELL",
                    "confidence": 0.5,
                    "price": close,
                    "atr": self._atr,
                    "adx": self._adx,
                    "metadata": {
                        "ema50": round(ema50, 2),
                        "ema200": round(ema200, 2),
                        "pullback_pct": round(pullback_dist * 100, 4),
                        "strategy": "TF-001",
                    },
                }
        return None

    def get_stats(self):
        return self._stats


class LS1Strategy:
    """Liquidity Sweep — wraps core/strategy/liquidity_sweep.py."""

    def __init__(self):
        import importlib.util
        _ls_spec = importlib.util.spec_from_file_location(
            "liquidity_sweep", str(_ROOT / "core" / "strategy" / "liquidity_sweep.py")
        )
        _ls_mod = importlib.util.module_from_spec(_ls_spec)
        _ls_spec.loader.exec_module(_ls_mod)
        self._strategy = _ls_mod.LiquiditySweepStrategy(lookback=50, sweep_threshold=0.0015)
        self._stats = {"total": 0, "signals": 0}
        self._prev_close = 0.0
        self._true_ranges = []
        self._atr = 0.0
        self._adx = 0.0

    def get_name(self):
        return "LS-001"

    def analyze(self, md: dict) -> Optional[dict]:
        self._stats["total"] += 1
        close = md.get("close", 0)
        high = md.get("high", 0)
        low = md.get("low", 0)

        # Compute ATR/ADX inline (same as MR1Strategy)
        if self._prev_close > 0:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
            self._true_ranges.append(tr)
            if len(self._true_ranges) > 14:
                self._true_ranges = self._true_ranges[-14:]
            if len(self._true_ranges) == 14:
                self._atr = sum(self._true_ranges) / 14

        price_change = 0.0
        if self._prev_close > 0:
            price_change = (close - self._prev_close) / self._prev_close
        if self._atr > 0:
            self._adx = min(100, abs(price_change) / self._atr * 1000)

        self._prev_close = close

        signal = self._strategy.analyze(md)
        if signal:
            self._stats["signals"] += 1
            return {
                "direction": signal.direction,
                "confidence": signal.confidence,
                "price": close,
                "atr": self._atr,
                "adx": self._adx,
                "metadata": {
                    **signal.metadata,
                    "atr": round(self._atr, 2),
                    "adx": round(self._adx, 4),
                    "current_price": close,
                    "strategy": "LS-001",
                },
            }
        return None

    def get_stats(self):
        return self._stats


# ── Strategy Registry ──────────────────────────────────────

STRATEGIES = {
    "LS-001": LS1Strategy,
    "MR-001": MR1Strategy,
    "VOL-001": VOL1Strategy,
    "TF-001": TF1Strategy,
}


# ── Runner ─────────────────────────────────────────────────

class ResearchRunner:
    """Запускает стратегию в Shadow режиме."""

    def __init__(self, strategy_id: str, symbol: str = "BTCUSDT", timeframe: str = "5m"):
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.timeframe = timeframe

        strategy_cls = STRATEGIES.get(strategy_id)
        if not strategy_cls:
            raise ValueError(f"Unknown strategy: {strategy_id}. Available: {list(STRATEGIES.keys())}")
        self._strategy = strategy_cls()
        self._executor = CryptoShadowExecutor(
            risk_config=PROFILE_A,
            db_path=_ROOT / "tradingos_data.db",
        )
        self._adapter = CryptoAdapter(
            api_key=os.environ.get("BYBIT_API_KEY", ""),
            api_secret=os.environ.get("BYBIT_API_SECRET", ""),
            testnet=True,
        )
        self._cycle = 0
        self._started_at = time.time()
        self._last_ts = 0.0

    async def initialize(self) -> bool:
        return await self._adapter.initialize()

    async def run_once(self) -> dict:
        self._cycle += 1

        ohlcv = await self._adapter._bybit.get_ohlcv(
            self.symbol, self.timeframe, limit=200
        )
        if not ohlcv:
            return {"cycle": self._cycle, "status": "no_data"}

        ohlcv.sort(key=lambda c: c.get("time", 0))
        new_candles = [c for c in ohlcv if c.get("time", 0) > self._last_ts]

        if not new_candles:
            return {"cycle": self._cycle, "status": "no_new_data"}

        max_ts = 0
        signals = []
        for candle in new_candles:
            ts = candle.get("time", 0)
            if ts > 1e12:
                ts = ts / 1000.0
            if ts > max_ts:
                max_ts = ts

            md = {
                "symbol": self.symbol,
                "open": float(candle.get("open", 0)),
                "high": float(candle.get("high", 0)),
                "low": float(candle.get("low", 0)),
                "close": float(candle.get("close", 0)),
                "volume": float(candle.get("volume", 0)),
            }

            signal = self._strategy.analyze(md)
            if signal:
                signal["metadata"]["current_price"] = md["close"]
                # Capture full feature snapshot from strategy state
                strat = self._strategy
                signal["feature_snapshot"] = {
                    "atr": round(getattr(strat, '_atr', 0), 2),
                    "adx": round(getattr(strat, '_adx', 0), 2),
                    "close": md["close"],
                    "high": md["high"],
                    "low": md["low"],
                    "volume": md["volume"],
                    "timestamp": ts,
                }
                signals.append(signal)

            await self._executor.update_positions(md)

        if max_ts > self._last_ts:
            self._last_ts = max_ts

        for s in signals:
            # Convert dict signal to StrategySignal-like object
            class SigObj:
                def __init__(self, d):
                    self.symbol = self.symbol
                    self.direction = d["direction"]
                    self.confidence = d["confidence"]
                    self.metadata = d.get("metadata", {})
                    self.timestamp = time.time()
            sig = SigObj.__new__(SigObj)
            sig.symbol = self.symbol
            sig.direction = s["direction"]
            sig.confidence = s["confidence"]
            sig.metadata = s.get("metadata", {})
            sig.timestamp = time.time()
            await self._executor.process_signal(sig)

        elapsed = time.time() - self._started_at
        stats = self._executor.get_stats()
        strat_stats = self._strategy.get_stats()

        return {
            "cycle": self._cycle,
            "elapsed_hours": round(elapsed / 3600, 2),
            "new_candles": len(new_candles),
            "new_signals": len(signals),
            "strategy": {
                "total_analyzed": strat_stats["total"],
                "total_signals": strat_stats["signals"],
            },
            "executor": {
                "opened": stats["positions_opened"],
                "closed": stats["positions_closed"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": stats["win_rate"],
                "pnl": stats["total_pnl"],
                "avg_mfe": stats["avg_mfe"],
                "avg_mae": stats["avg_mae"],
                "exit_reasons": stats["exit_reasons"],
            },
        }

    async def run_daemon(self, interval: int = 60, max_cycles: int = 0):
        cycle = 0
        try:
            while True:
                cycle += 1
                if max_cycles and cycle > max_cycles:
                    break
                result = await self.run_once()
                self._print_result(result)
                if cycle % 10 == 0:
                    self._print_summary()
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Stopped.")
        finally:
            self._print_summary()
            self._adapter.close()

    def _print_result(self, result: dict):
        elapsed = result.get("elapsed_hours", 0)
        cycle = result["cycle"]
        nc = result.get("new_candles", 0)
        ns = result.get("new_signals", 0)
        ex = result.get("executor", {})
        print(
            f"[{self.strategy_id}][{cycle:4d}] {elapsed:5.1f}h  "
            f"candles={nc:3d}  sig={ns:2d}  "
            f"opened={ex.get('opened',0):2d}  closed={ex.get('closed',0):2d}  "
            f"pnl={ex.get('pnl',0):+.6f}  wr={ex.get('win_rate',0):.1%}"
        )

    def _print_summary(self):
        stats = self._executor.get_stats()
        print()
        print("=" * 60)
        print(f"  {self.strategy_id} — SHADOW SUMMARY  (cycle {self._cycle})")
        print("=" * 60)
        print(f"  Total signals:     {stats['total_signals']}")
        print(f"  Positions opened:  {stats['positions_opened']}")
        print(f"  Positions closed:  {stats['positions_closed']}")
        print(f"  Wins:              {stats['wins']}")
        print(f"  Losses:            {stats['losses']}")
        print(f"  Win rate:          {stats['win_rate']:.1%}")
        print(f"  Total PnL:         {stats['total_pnl']:+.6f}")
        print(f"  Avg MFE:           {stats['avg_mfe']:.2f}")
        print(f"  Avg MAE:           {stats['avg_mae']:.2f}")
        print(f"  Exit reasons:      {stats['exit_reasons']}")
        print("=" * 60)
        print()

    def close(self):
        self._adapter.close()


# ── CLI ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Research Runner")
    parser.add_argument("strategy", choices=list(STRATEGIES.keys()), help="Strategy ID")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--cycles", type=int, default=0)
    parser.add_argument("--once", action="store_true")

    args = parser.parse_args()

    runner = ResearchRunner(args.strategy, args.symbol, args.timeframe)

    async def run():
        ok = await runner.initialize()
        if not ok:
            print("FAIL: cannot connect")
            return

        if args.once:
            result = await runner.run_once()
            print(json.dumps(result, indent=2))
            runner._print_summary()
        else:
            print(f"Starting {args.strategy} on {args.symbol} @ {args.timeframe}")
            print(f"Interval: {args.interval}s  Cycles: {args.cycles or 'infinite'}")
            print()
            await runner.run_daemon(interval=args.interval, max_cycles=args.cycles)

        runner.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
