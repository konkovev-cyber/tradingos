"""
Research Replay v2 — массовый исторический прогон стратегии.

Использует Bybit API с пагинацией по времени для получения 50000+ свечей.
Добавляет расширенный отчёт: monthly breakdown, trade frequency, worst/best trades.

Usage:
  python research_replay.py MR-001 --symbol BTCUSDT --timeframe 5m --months 12
  python research_replay.py VOL-001 --symbol BTCUSDT --timeframe 15m --months 6
  python research_replay.py TF-001 --symbol BTCUSDT --timeframe 1h --months 12
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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ResearchReplay")

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

_rr_spec = importlib.util.spec_from_file_location(
    "research_runner", str(_ROOT / "research_runner.py")
)
_rr_mod = importlib.util.module_from_spec(_rr_spec)
_rr_spec.loader.exec_module(_rr_mod)
STRATEGIES = _rr_mod.STRATEGIES

# ── Constants ──────────────────────────────────────────────

TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}
TIMEFRAME_BYBIT = {"5m": "5", "15m": "15", "1h": "60"}
MAX_PER_REQUEST = 200
COMMISSION_RATE = 0.001
SLIPPAGE_BPS = 0.05


# ── Data Fetcher ──────────────────────────────────────────

async def fetch_ohlcv_range(symbol: str, timeframe: str, months: int) -> list:
    """Fetch OHLCV data with caching. Downloads once, reuses on subsequent calls."""
    import importlib.util
    from pathlib import Path
    _dc_spec = importlib.util.spec_from_file_location(
        "data_cache", str(Path(__file__).parent / "core" / "data_lake" / "data_cache.py")
    )
    _dc_mod = importlib.util.module_from_spec(_dc_spec)
    _dc_spec.loader.exec_module(_dc_mod)
    DataCache = _dc_mod.DataCache

    cache = DataCache()
    cache_key = f"{symbol}_{timeframe}_{months}m"

    # Try cache first
    cached = cache.load(symbol, f"{timeframe}_{months}m")
    if cached:
        print(f"  Loaded {len(cached)} candles from cache")
        return cached

    import sys
    sys.path.insert(0, "/root/trading_brain_v4")
    from exchange.bybit.client import BybitClient

    client = BybitClient(testnet=True)
    interval = TIMEFRAME_BYBIT.get(timeframe, "5")
    seconds_per_candle = TIMEFRAME_SECONDS.get(timeframe, 300)

    now = time.time()
    start_ts = int((now - months * 30 * 24 * 3600) * 1000)
    end_ts = int(now * 1000)

    all_candles = []
    current_start = start_ts
    max_iterations = 20000  # safety cap
    iteration = 0

    while current_start < end_ts and iteration < max_iterations:
        iteration += 1
        try:
            raw = client.get_kline(
                symbol=symbol,
                interval=interval,
                start=current_start,
                limit=MAX_PER_REQUEST,
            )
            if not raw:
                break

            prev_count = len(all_candles)

            # Bybit returns newest-first. raw[0] = newest candle.
            # Advance start past the NEWEST candle to fetch older data next.
            newest_ts = float(raw[0][0])
            current_start = int(newest_ts) + seconds_per_candle * 1000

            # Process candles oldest-first for chronological order
            for row in reversed(raw):
                if len(row) >= 6:
                    ts = float(row[0])
                    if all_candles and all_candles[-1]["time"] >= ts:
                        continue
                    all_candles.append({
                        "time": ts,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    })

            # No new data → stop
            if len(all_candles) == prev_count:
                break

            if iteration % 50 == 0:
                print(f"  Fetched {len(all_candles)} candles", end="\r")

        except Exception as e:
            logger.warning(f"Fetch error at {current_start}: {e}")
            await asyncio.sleep(1)
            continue

    client.close()
    print(f"\n  Total: {len(all_candles)} candles")

    # Save to cache
    if all_candles:
        cache.save(symbol, f"{timeframe}_{months}m", all_candles)

    return all_candles


# ── Economic Filter ───────────────────────────────────────

def apply_economic_filter(trades: list) -> list:
    for t in trades:
        entry_cost = t["entry_price"] * t["quantity"] * COMMISSION_RATE
        exit_cost = t["exit_price"] * t["quantity"] * COMMISSION_RATE
        total_fees = entry_cost + exit_cost
        slippage = t["entry_price"] * t["quantity"] * SLIPPAGE_BPS / 100
        total_costs = total_fees + slippage
        gross_pnl = t["pnl"]
        net_pnl = gross_pnl - total_costs
        t.update({
            "gross_pnl": round(gross_pnl, 6),
            "fees": round(total_fees, 6),
            "slippage": round(slippage, 6),
            "net_pnl": round(net_pnl, 6),
        })
    return trades


# ── Replay Runner ─────────────────────────────────────────

class ReplayRunner:
    def __init__(self, strategy_id: str, symbol: str = "BTCUSDT", timeframe: str = "5m"):
        strategy_cls = STRATEGIES.get(strategy_id)
        if not strategy_cls:
            raise ValueError(f"Unknown strategy: {strategy_id}")
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.timeframe = timeframe
        self._strategy = strategy_cls()
        self._executor_a = CryptoShadowExecutor(risk_config=PROFILE_A)
        self._executor_b = CryptoShadowExecutor(risk_config=PROFILE_B)

    async def run(self, months: int = 12) -> dict:
        print(f"Fetching {months} months of {self.symbol} {self.timeframe}...")
        ohlcv = await fetch_ohlcv_range(self.symbol, self.timeframe, months)
        if not ohlcv:
            return {"status": "error", "message": "no data"}

        ohlcv.sort(key=lambda c: c.get("time", 0))
        print(f"Processing {len(ohlcv)} candles...")

        signals = []
        for i, candle in enumerate(ohlcv):
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
                sig = type('Sig', (), {
                    'symbol': self.symbol,
                    'direction': signal["direction"],
                    'confidence': signal["confidence"],
                    'metadata': signal.get("metadata", {}),
                    'timestamp': time.time(),
                })()
                await self._executor_a.process_signal(sig)
                await self._executor_b.process_signal(sig)
                signals.append(signal)

            await self._executor_a.update_positions(md)
            await self._executor_b.update_positions(md)

            if (i + 1) % 5000 == 0:
                print(f"  Processed {i+1}/{len(ohlcv)} candles, {len(signals)} signals")

        return self._build_report(len(ohlcv), len(signals))

    def _build_report(self, total_candles: int, total_signals: int) -> dict:
        def trades_from_closed(closed, profile):
            return apply_economic_filter([
                {
                    "position_id": c.position_id,
                    "entry_price": c.entry_price,
                    "exit_price": c.exit_price,
                    "pnl": c.pnl,
                    "pnl_pct": c.pnl_pct,
                    "reason": c.reason,
                    "mfe": c.mfe,
                    "mae": c.mae,
                    "bars_held": c.bars_held,
                    "quantity": 0.001,
                    "profile": profile,
                }
                for c in closed
            ])

        def compute_metrics(trades):
            base = {
                "trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "gross_pnl": 0.0, "fees": 0.0, "slippage": 0.0, "net_pnl": 0.0,
                "avg_mfe": 0.0, "avg_mae": 0.0, "avg_bars": 0.0,
                "exit_reasons": {"SL": 0, "TP": 0, "EXPIRY": 0},
                "worst_trade": {}, "best_trade": {},
                "monthly": {},
            }
            if not trades:
                return base

            gross_pnl = sum(t["gross_pnl"] for t in trades)
            total_fees = sum(t["fees"] for t in trades)
            total_slippage = sum(t["slippage"] for t in trades)
            net_pnl = sum(t["net_pnl"] for t in trades)
            wins = sum(1 for t in trades if t["net_pnl"] > 0)
            losses = sum(1 for t in trades if t["net_pnl"] <= 0)
            total = len(trades)

            # Monthly breakdown
            monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "gross_pnl": 0.0, "net_pnl": 0.0})
            for t in trades:
                ts = t.get("entry_ts", 0)
                if ts:
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    key = dt.strftime("%Y-%m")
                else:
                    key = "unknown"
                monthly[key]["trades"] += 1
                monthly[key]["wins"] += 1 if t["net_pnl"] > 0 else 0
                monthly[key]["gross_pnl"] += t["gross_pnl"]
                monthly[key]["net_pnl"] += t["net_pnl"]

            # Best/worst
            sorted_by_pnl = sorted(trades, key=lambda t: t["net_pnl"])
            worst = sorted_by_pnl[0] if sorted_by_pnl else {}
            best = sorted_by_pnl[-1] if sorted_by_pnl else {}

            return {
                "trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / total, 4) if total else 0,
                "gross_pnl": round(gross_pnl, 6),
                "fees": round(total_fees, 6),
                "slippage": round(total_slippage, 6),
                "net_pnl": round(net_pnl, 6),
                "avg_mfe": round(sum(t["mfe"] for t in trades) / total, 2) if total else 0,
                "avg_mae": round(sum(t["mae"] for t in trades) / total, 2) if total else 0,
                "avg_bars": round(sum(t["bars_held"] for t in trades) / total, 1) if total else 0,
                "exit_reasons": {
                    "SL": sum(1 for t in trades if t["reason"] == "SL"),
                    "TP": sum(1 for t in trades if t["reason"] == "TP"),
                    "EXPIRY": sum(1 for t in trades if t["reason"] == "EXPIRY"),
                },
                "worst_trade": {
                    "pnl": round(worst.get("net_pnl", 0), 6),
                    "reason": worst.get("reason", ""),
                    "bars": worst.get("bars_held", 0),
                } if worst else {},
                "best_trade": {
                    "pnl": round(best.get("net_pnl", 0), 6),
                    "reason": best.get("reason", ""),
                    "bars": best.get("bars_held", 0),
                } if best else {},
                "monthly": dict(monthly),
            }

        stats_a = self._executor_a.get_stats()
        stats_b = self._executor_b.get_stats()
        closed_a = self._executor_a.get_closed_positions()
        closed_b = self._executor_b.get_closed_positions()

        return {
            "strategy": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "total_candles": total_candles,
            "total_signals": total_signals,
            "profile_A": {"config": stats_a["config"], **compute_metrics(trades_from_closed(closed_a, "A"))},
            "profile_B": {"config": stats_b["config"], **compute_metrics(trades_from_closed(closed_b, "B"))},
        }


# ── Report Generator ─────────────────────────────────────

def generate_report(report: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append(f"  RESEARCH REPORT — {report['strategy']}")
    lines.append("=" * 70)
    lines.append(f"  Symbol:    {report['symbol']}")
    lines.append(f"  Timeframe: {report['timeframe']}")
    lines.append(f"  Candles:   {report['total_candles']}")
    lines.append(f"  Signals:   {report['total_signals']}")
    lines.append(f"  Signal rate: {report['total_signals']/max(report['total_candles'],1)*100:.2f}%")
    lines.append("")

    for profile in ["profile_A", "profile_B"]:
        p = report[profile]
        lines.append(f"  Profile {profile[-1]}: {p['config']}")
        lines.append(f"  " + "-" * 60)
        lines.append(f"    Trades:      {p['trades']}")
        lines.append(f"    Win rate:    {p['win_rate']:.1%}")
        lines.append(f"    Gross PnL:   {p['gross_pnl']:+.6f}")
        lines.append(f"    Fees:        {p['fees']:.6f}")
        lines.append(f"    Slippage:    {p['slippage']:.6f}")
        lines.append(f"    Net PnL:     {p['net_pnl']:+.6f}")
        lines.append(f"    Avg MFE:     {p['avg_mfe']:.2f}")
        lines.append(f"    Avg MAE:     {p['avg_mae']:.2f}")
        lines.append(f"    Avg bars:    {p['avg_bars']}")
        lines.append(f"    Exit:        {p['exit_reasons']}")
        if p.get("worst_trade"):
            w = p["worst_trade"]
            lines.append(f"    Worst trade: {w.get('pnl',0):+.6f} ({w.get('reason','?')}, {w.get('bars',0)} bars)")
        if p.get("best_trade"):
            b = p["best_trade"]
            lines.append(f"    Best trade:  {b.get('pnl',0):+.6f} ({b.get('reason','?')}, {b.get('bars',0)} bars)")
        lines.append("")

        # Monthly breakdown
        monthly = p.get("monthly", {})
        if monthly:
            lines.append(f"    Monthly breakdown:")
            lines.append(f"    {'Month':<10} {'Trades':>8} {'WinRate':>10} {'Net PnL':>12}")
            lines.append(f"    " + "-" * 42)
            for month in sorted(monthly.keys()):
                m = monthly[month]
                wr = m["wins"] / m["trades"] if m["trades"] else 0
                lines.append(f"    {month:<10} {m['trades']:>8} {wr:>9.1%} {m['net_pnl']:>+12.6f}")
            lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Research Replay v2")
    parser.add_argument("strategy", choices=list(STRATEGIES.keys()))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--months", type=int, default=6, help="Months of history")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    runner = ReplayRunner(args.strategy, args.symbol, args.timeframe)

    async def run():
        report = await runner.run(months=args.months)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(generate_report(report))

    asyncio.run(run())


if __name__ == "__main__":
    main()
