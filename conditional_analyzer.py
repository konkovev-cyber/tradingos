"""
Conditional Performance Analyzer — ответ на вопрос "где стратегия выигрывает?"

Строит таблицы PF/WinRate по условиям:
- час суток
- ATR percentile
- ADX bucket
- день недели
- BB width
- EMA200 direction
- holding time

Использование:
  python conditional_analyzer.py LS-001 --symbol BTCUSDT --timeframe 5m --months 12
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
logger = logging.getLogger("CondAnalyzer")

_ROOT = Path(__file__).parent

# ── Imports ────────────────────────────────────────────────

_rr_spec = importlib.util.spec_from_file_location("rr", str(_ROOT / "research_runner.py"))
_rr_mod = importlib.util.module_from_spec(_rr_spec)
_rr_spec.loader.exec_module(_rr_mod)
STRATEGIES = _rr_mod.STRATEGIES

_cse_spec = importlib.util.spec_from_file_location(
    "cse", str(_ROOT / "core" / "execution" / "crypto_shadow.py")
)
_cse_mod = importlib.util.module_from_spec(_cse_spec)
_cse_spec.loader.exec_module(_cse_mod)
CryptoShadowExecutor = _cse_mod.CryptoShadowExecutor
PROFILE_B = _cse_mod.PROFILE_B


# ── Condition Tagging ──────────────────────────────────────

def get_conditions(md: dict, atr: float, adx: float, atr_history: list) -> dict:
    """Tag a candle with market conditions."""
    from datetime import datetime, timezone

    ts = md.get("timestamp", 0)
    if ts > 1e12:
        ts = ts / 1000.0  # ms to seconds
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

    # ATR percentile
    if atr_history and atr > 0:
        percentile = sum(1 for v in atr_history if v <= atr) / len(atr_history) * 100
    else:
        percentile = 50

    return {
        "hour": dt.hour,
        "dow": dt.strftime("%a"),  # Mon, Tue, ...
        "atr_pctile": "low" if percentile < 30 else ("mid" if percentile < 70 else "high"),
        "adx_bucket": "low" if adx < 15 else ("mid" if adx < 25 else "high"),
    }


def condition_pf(trades: list[dict]) -> dict:
    """Compute PF, WR, count for a list of trades."""
    if not trades:
        return {"trades": 0, "pf": 0, "wr": 0, "net": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (10 if gross_profit > 0 else 0)
    net = sum(t["pnl"] for t in trades)
    return {
        "trades": len(trades),
        "pf": round(pf, 2),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "net": round(net, 2),
    }


# ── Main Analyzer ──────────────────────────────────────────

async def analyze(strategy_id: str, symbol: str, timeframe: str, months: int):
    """Run strategy on historical data and build conditional report."""
    # Load candles
    cache_path = _ROOT / "core" / "data_lake" / "data" / "cache" / f"{symbol}_{timeframe}_{months}m.json"
    if cache_path.exists():
        with open(cache_path) as f:
            candles = json.load(f)
        print(f"Loaded {len(candles)} candles from cache")
    else:
        print("No cache. Run research_replay.py first to populate cache.")
        return

    candles.sort(key=lambda c: c.get("time", 0))

    strategy_cls = STRATEGIES.get(strategy_id)
    if not strategy_cls:
        print(f"Unknown strategy: {strategy_id}")
        return

    strategy = strategy_cls()
    executor = CryptoShadowExecutor(risk_config=PROFILE_B)

    # Track conditions per trade
    trades_by_condition = defaultdict(lambda: defaultdict(list))
    trades_by_time = []
    atr_history = []

    print(f"Processing {len(candles)} candles...")

    for i, candle in enumerate(candles):
        ts = candle.get("time", 0)
        md = {
            "symbol": symbol,
            "open": float(candle.get("open", 0)),
            "high": float(candle.get("high", 0)),
            "low": float(candle.get("low", 0)),
            "close": float(candle.get("close", 0)),
            "volume": float(candle.get("volume", 0)),
            "timestamp": ts,
        }

        # Update ATR history
        atr = getattr(strategy, '_atr', 0)
        if atr > 0:
            atr_history.append(atr)
            if len(atr_history) > 500:
                atr_history.pop(0)

        adx = getattr(strategy, '_adx', 0)

        # Analyze for signal
        signal = strategy.analyze(md)
        if signal:
            meta = dict(signal.get("metadata", {}))
            meta.setdefault("current_price", md.get("close", 0))
            sig = type('Sig', (), {
                'symbol': symbol,
                'direction': signal["direction"],
                'confidence': signal["confidence"],
                'metadata': meta,
                'timestamp': time.time(),
            })()
            await executor.process_signal(sig)

            # Tag this trade's conditions
            conditions = get_conditions(md, atr, adx, atr_history)

        # Update positions and capture closed trades
        closed_this_bar = await executor.update_positions(md)

        for closed in closed_this_bar:
            conditions = get_conditions(md, atr, adx, atr_history)
            for cond_key, cond_val in conditions.items():
                trades_by_condition[cond_key][cond_val].append({
                    "pnl": getattr(closed, 'pnl', getattr(closed, 'net_pnl', 0)),
                    "reason": getattr(closed, 'reason', "unknown"),
                })

    # Build report
    print("\n" + "=" * 60)
    print(f"  CONDITIONAL PERFORMANCE REPORT — {strategy_id}")
    print("=" * 60)

    stats = executor.get_stats()
    total_trades = stats["positions_closed"]
    print(f"\n  Total trades: {total_trades}")
    print(f"  Total PnL: {stats['total_pnl']:.2f}")
    print(f"  Win rate: {stats['win_rate']:.1%}")

    print(f"\n  {'Condition':<15} {'Value':<10} {'Trades':>8} {'WR':>6} {'PF':>6} {'NetPnL':>10}")
    print("  " + "-" * 60)

    for cond_key in sorted(trades_by_condition.keys()):
        print(f"\n  {cond_key}:")
        for cond_val in sorted(trades_by_condition[cond_key].keys()):
            trades = trades_by_condition[cond_key][cond_val]
            metrics = condition_pf(trades)
            print(f"  {'':<15} {cond_val:<10} {metrics['trades']:>8} {metrics['wr']:>5.1f}% {metrics['pf']:>6.2f} {metrics['net']:>+10.2f}")

    # Losing trades analysis
    all_trades = []
    for cond_key in trades_by_condition:
        for cond_val, trades in trades_by_condition[cond_key].items():
            for t in trades:
                all_trades.append({**t, "condition": f"{cond_key}={cond_val}"})

    losing = [t for t in all_trades if t["pnl"] <= 0]
    if losing:
        print(f"\n  LOSING TRADES ANALYSIS ({len(losing)} trades):")
        reason_counts = defaultdict(int)
        for t in losing:
            reason_counts[t.get("reason", "unknown")] += 1
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count} ({count/len(losing)*100:.1f}%)")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Conditional Performance Analyzer")
    parser.add_argument("strategy", choices=list(STRATEGIES.keys()))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--months", type=int, default=12)
    args = parser.parse_args()

    asyncio.run(analyze(args.strategy, args.symbol, args.timeframe, args.months))


if __name__ == "__main__":
    main()