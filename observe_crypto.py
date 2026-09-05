#!/usr/bin/env python3
"""
Crypto Shadow Observation — 48h A/B сбор статистики.

Запускает два риск-профиля (A и B) на одних сигналах.
Сравнивает win rate, MFE/MAE, exit reasons.

Запуск:
  python observe_crypto.py [--symbol BTCUSDT] [--timeframe 5m] [--interval 60]
  python observe_crypto.py --once
  python observe_crypto.py --profile C
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ObserveCrypto")

_ROOT = Path(__file__).parent

# ── Absolute-path imports ──────────────────────────────────

_ca_spec = importlib.util.spec_from_file_location(
    "crypto_adapter", str(_ROOT / "adapters" / "crypto_adapter.py")
)
_ca_mod = importlib.util.module_from_spec(_ca_spec)
_ca_spec.loader.exec_module(_ca_mod)
CryptoAdapter = _ca_mod.CryptoAdapter

_ls_spec = importlib.util.spec_from_file_location(
    "liquidity_sweep", str(_ROOT / "core" / "strategy" / "liquidity_sweep.py")
)
_ls_mod = importlib.util.module_from_spec(_ls_spec)
_ls_spec.loader.exec_module(_ls_mod)
LiquiditySweepStrategy = _ls_mod.LiquiditySweepStrategy

_cse_spec = importlib.util.spec_from_file_location(
    "crypto_shadow", str(_ROOT / "core" / "execution" / "crypto_shadow.py")
)
_cse_mod = importlib.util.module_from_spec(_cse_spec)
_cse_spec.loader.exec_module(_cse_mod)
CryptoShadowExecutor = _cse_mod.CryptoShadowExecutor
PROFILE_A = _cse_mod.PROFILE_A
PROFILE_B = _cse_mod.PROFILE_B
PROFILE_C = _cse_mod.PROFILE_C

# PIE v1.1 Position Intelligence
_pie_spec = importlib.util.spec_from_file_location(
    "position_intelligence", str(_ROOT / "core" / "intelligence" / "position_intelligence.py")
)
_pie_mod = importlib.util.module_from_spec(_pie_spec)
_pie_spec.loader.exec_module(_pie_mod)
PIEPositionIntelligence = _pie_mod.PIEPositionIntelligence


# ── A/B Observer ─────────────────────────────────────────────

class ABObserver:
    """A/B наблюдатель: два риск-профиля на одних сигналах."""

    PROFILES = {
        "A": PROFILE_A,
        "B": PROFILE_B,
        "C": PROFILE_C,
    }

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "5m",
        profiles: list[str] = None,
        pie_mode: str = "observe",
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self._adapter = CryptoAdapter(
            api_key=os.environ.get("BYBIT_API_KEY", ""),
            api_secret=os.environ.get("BYBIT_API_SECRET", ""),
            testnet=True,
        )
        self._strategy = LiquiditySweepStrategy(lookback=50, sweep_threshold=0.0015)

        # Create one executor per profile
        profile_names = profiles or ["A", "B"]
        self._executors = {
            name: CryptoShadowExecutor(
                risk_config=self.PROFILES[name],
                db_path=_ROOT / "tradingos_data.db",
            )
            for name in profile_names
        }

        # PIE v1.1 Position Intelligence
        self._pie = PIEPositionIntelligence(
            mode=pie_mode,
            db_path=_ROOT / "tradingos_data.db",
        )

        self._cycle = 0
        self._started_at = time.time()

        # Persistent state
        self._last_ts: float = 0.0
        self._prev_close: float = 0.0
        self._true_ranges: list[float] = []
        self._atr: float = 0.0
        self._adx: float = 0.0

    async def initialize(self) -> bool:
        return await self._adapter.initialize()

    async def run_once(self) -> dict:
        """Один цикл: fetch → strategy → A/B shadow update."""
        self._cycle += 1

        ohlcv = await self._adapter._bybit.get_ohlcv(
            self.symbol, self.timeframe, limit=200
        )
        if not ohlcv:
            return {"cycle": self._cycle, "status": "no_data"}

        ohlcv.sort(key=lambda c: c.get("time", 0))
        new_candles = [c for c in ohlcv if c.get("time", 0) > self._last_ts]

        if not new_candles:
            latest = ohlcv[-1]
            market_data = self._candle_to_market_data(latest)
            for ex in self._executors.values():
                await ex.update_positions(market_data)
            return {"cycle": self._cycle, "status": "no_new_data"}

        # Process each new candle
        signals = []
        max_ts = 0
        for candle in new_candles:
            market_data = self._candle_to_market_data(candle)
            if market_data is None:
                continue

            ts = candle.get("time", 0)
            if ts > 1e12:
                ts = ts / 1000.0
            if ts > max_ts:
                max_ts = ts

            # Run strategy once
            signal = self._strategy.analyze(market_data)
            if signal:
                signal.metadata["atr"] = self._atr
                signal.metadata["adx"] = self._adx
                signal.metadata["current_price"] = market_data["close"]
                signals.append(signal)

            # Update ALL executors (A and B)
            for name, ex in self._executors.items():
                closed = await ex.update_positions(market_data)
                # Update PIE for closed positions
                for close_result in closed:
                    self._pie.on_position_close(
                        position_id=close_result.position_id,
                        exit_price=close_result.exit_price,
                        exit_reason=close_result.reason,
                    )
                # Update PIE for open positions
                for pos in await ex.get_positions():
                    self._pie.on_bar_update(
                        position_id=pos.position_id,
                        current_price=market_data["close"],
                        high=market_data["high"],
                        low=market_data["low"],
                        bars_held=pos.bars_held,
                        trend=market_data.get("trend", "UNKNOWN"),
                    )

        if max_ts > self._last_ts:
            self._last_ts = max_ts

        # Feed same signals to ALL executors
        for signal in signals:
            for name, ex in self._executors.items():
                pos_id = await ex.process_signal(signal)
                if pos_id:
                    # Register position in PIE
                    self._pie.on_position_open(
                        position_id=pos_id,
                        symbol=signal.symbol,
                        side=signal.direction,
                        entry_price=signal.metadata.get("current_price", 0),
                        metadata=signal.metadata,
                    )

        # Collect stats from all profiles
        elapsed = time.time() - self._started_at
        result = {
            "cycle": self._cycle,
            "elapsed_hours": round(elapsed / 3600, 2),
            "new_candles": len(new_candles),
            "new_signals": len(signals),
        }
        for name, ex in self._executors.items():
            s = ex.get_stats()
            result[f"profile_{name}"] = {
                "total_signals": s["total_signals"],
                "opened": s["positions_opened"],
                "closed": s["positions_closed"],
                "open": s["open_positions"],
                "wins": s["wins"],
                "losses": s["losses"],
                "win_rate": s["win_rate"],
                "pnl": s["total_pnl"],
                "avg_mfe": s["avg_mfe"],
                "avg_mae": s["avg_mae"],
                "avg_mae_bc": s["avg_mae_before_close"],
                "avg_bars": s["avg_bars_held"],
                "mfe_mae": s["avg_mfe_mae_ratio"],
                "exit_reasons": s["exit_reasons"],
            }

        # PIE position reports
        pie_reports = self._pie.get_all_positions_report()
        if pie_reports:
            result["pie_positions"] = pie_reports

        return result

    def _candle_to_market_data(self, candle: dict) -> Optional[dict]:
        ts = candle.get("time", 0)
        if ts > 1e12:
            ts = ts / 1000.0

        high = float(candle.get("high", 0))
        low = float(candle.get("low", 0))
        close = float(candle.get("close", 0))
        volume = float(candle.get("volume", 0))

        if high == 0 or low == 0:
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

        # Simple trend detection for PIE
        if price_change > 0.001:
            trend = "BULLISH"
        elif price_change < -0.001:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        return {
            "symbol": self.symbol,
            "timestamp": ts,
            "open": float(candle.get("open", 0)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "atr": self._atr,
            "adx": self._adx,
            "price_change": price_change,
            "regime": "UNKNOWN",
            "trend": trend,
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
            logger.info("Stopped by user.")
        finally:
            self._print_summary()
            self._adapter.close()

    def _print_result(self, result: dict):
        elapsed = result.get("elapsed_hours", 0)
        cycle = result["cycle"]
        new_c = result.get("new_candles", 0)
        sig = result.get("new_signals", 0)

        parts = [f"[{cycle:4d}] {elapsed:5.1f}h  candles={new_c:3d}  sig={sig:2d}"]
        for name in self._executors:
            p = result.get(f"profile_{name}", {})
            parts.append(f"  {name}: pnl={p.get('pnl',0):+.6f} wr={p.get('win_rate',0):.1%}")
        
        # PIE summary
        pie_reports = result.get("pie_positions", [])
        if pie_reports:
            parts.append(f"  PIE:{len(pie_reports)}")
            for r in pie_reports[:2]:  # Show first 2
                parts.append(f"    {r['symbol']} {r['side']} pnl={r['current_pnl_pct']:+.2%} "
                           f"MFE={r['max_profit_seen']:.2%} health={r['health_score']:.0f}")
        
        print("".join(parts))

    def _print_summary(self):
        print()
        print("=" * 80)
        print(f"  A/B SHADOW EXECUTION SUMMARY  (cycle {self._cycle})")
        print("=" * 80)

        for name, ex in self._executors.items():
            s = ex.get_stats()
            closed = ex.get_closed_positions()
            print(f"\n  Profile {name}: {s['config']}")
            print(f"  " + "-" * 60)
            print(f"    Total signals:     {s['total_signals']}")
            print(f"    Positions opened:  {s['positions_opened']}")
            print(f"    Positions closed:  {s['positions_closed']}")
            print(f"    Open positions:    {s['open_positions']}")
            print(f"    Wins:              {s['wins']}")
            print(f"    Losses:            {s['losses']}")
            print(f"    Win rate:          {s['win_rate']:.1%}")
            print(f"    Total PnL:         {s['total_pnl']:+.6f}")
            print(f"    Avg MFE:           {s['avg_mfe']:.2f}")
            print(f"    Avg MAE:           {s['avg_mae']:.2f}")
            print(f"    Avg MAE@close:     {s['avg_mae_before_close']:.2f}")
            print(f"    Avg bars held:     {s['avg_bars_held']}")
            print(f"    MFE/MAE ratio:     {s['avg_mfe_mae_ratio']:.2f}")
            print(f"    Exit reasons:      {s['exit_reasons']}")

            if closed:
                print(f"\n    Last {min(5, len(closed))} closed:")
                for c in closed[-5:]:
                    print(f"      {c.position_id[:6]} {c.reason:6s} "
                          f"entry={c.entry_price:.1f} exit={c.exit_price:.1f} "
                          f"pnl={c.pnl:+.6f} mfe={c.mfe:.1f} mae={c.mae:.1f} "
                          f"bars={c.bars_held}")

        # PIE Analytics
        pie_reports = self._pie.get_all_positions_report()
        if pie_reports:
            print(f"\n  PIE Position Intelligence:")
            print(f"  " + "-" * 60)
            for r in pie_reports:
                print(f"    {r['symbol']} {r['side']} @ {r['entry_price']:.1f}")
                print(f"      Current: {r['current_pnl_pct']:+.2%}  MFE: {r['max_profit_seen']:.2%}  "
                      f"MAE: {r['max_loss_seen']:.2%}  Retrace: {r['profit_retracement']:.0%}")
                print(f"      Health: {r['health_score']:.0f}/100  Bars: {r['bars_held']}")

        # Closed analytics
        closed_analytics = self._pie.get_closed_analytics(limit=10)
        if closed_analytics:
            print(f"\n  PIE Closed Analytics (last {len(closed_analytics)}):")
            print(f"  " + "-" * 60)
            for a in closed_analytics:
                print(f"    {a['symbol']} {a['side']} entry={a['entry_price']:.1f} "
                      f"MFE={a['max_profit_seen']:.2%} MAE={a['max_loss_seen']:.2%} "
                      f"retrace={a['profit_retracement']:.0%} events={a['events_count']}")

        print("=" * 80)
        print()

    def close(self):
        self._adapter.close()


# ── CLI ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crypto A/B Shadow Observation with PIE v1.1")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval (s)")
    parser.add_argument("--cycles", type=int, default=0, help="Max cycles (0=infinite)")
    parser.add_argument("--once", action="store_true", help="Single run")
    parser.add_argument("--profiles", default="A,B", help="Comma-separated profiles (A,B,C)")
    parser.add_argument("--pie-mode", default="observe", choices=["observe", "shadow", "active"],
                       help="PIE position intelligence mode")

    args = parser.parse_args()
    profiles = [p.strip() for p in args.profiles.split(",")]

    observer = ABObserver(
        symbol=args.symbol,
        timeframe=args.timeframe,
        profiles=profiles,
        pie_mode=args.pie_mode,
    )

    async def run():
        ok = await observer.initialize()
        if not ok:
            print("FAIL: cannot connect to Bybit testnet")
            return

        if args.once:
            result = await observer.run_once()
            print(json.dumps(result, indent=2))
            observer._print_summary()
        else:
            print(f"Starting A/B shadow observation: {args.symbol} @ {args.timeframe}")
            print(f"Profiles: {profiles}")
            print(f"Poll interval: {args.interval}s  Max cycles: {args.cycles or 'infinite'}")
            print()
            await observer.run_daemon(interval=args.interval, max_cycles=args.cycles)

        observer.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
