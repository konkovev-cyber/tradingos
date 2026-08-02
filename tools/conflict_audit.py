#!/usr/bin/env python3
"""
Conflict Resolution Audit — анализ потери сигналов при MAX_OPEN_POSITIONS=1.

Для каждого конфликтного события:
- какие сигналы конкурировали
- confidence, final_probability, expected R каждого
- какой был выбран (first-come)
- какой был бы лучшим

Сравнение 5 политик выбора.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator

import tradingos.signals.signal_scoring as ss
THRESHOLD = 0.55
_orig = ss.SignalScoringEngine.calculate_with_vectors

# Capture scores for each decide() call
_captured_signals: list[dict] = []

def _patched(self, **kw):
    r = _orig(self, **kw)
    if r is not None:
        object.__setattr__(r, 'threshold', THRESHOLD)
        # Store the score data
        _captured_signals.append({
            "ts": kw.get("fv", None).timestamp_ms if kw.get("fv") else 0,
            "symbol": kw.get("symbol", ""),
            "direction": kw.get("direction", ""),
            "confidence": r.base_probability,
            "final_probability": r.final_probability,
            "total_score": r.total_score,
        })
    return r

ss.SignalScoringEngine.calculate_with_vectors = _patched

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "DOGEUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "LINKUSDT", "BNBUSDT", "AVAXUSDT",
    "DOTUSDT", "ATOMUSDT",
]


async def fetch_all() -> dict[str, list[Candle]]:
    import httpx
    data = {}
    for sym in SYMBOLS:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < 720:
                params = {"category": "linear", "symbol": sym, "interval": "60", "limit": 200}
                if end_ms:
                    params["end"] = str(end_ms)
                resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                rows = resp.json().get("result", {}).get("list", [])
                if not rows:
                    break
                for row in rows:
                    all_c.append(Candle(
                        timestamp=int(row[0]), open=float(row[1]), high=float(row[2]),
                        low=float(row[3]), close=float(row[4]),
                        volume=float(row[5]) if row[5] else 0.0,
                        symbol=sym, timeframe="1h",
                    ))
                end_ms = int(rows[-1][0]) - 1
                await asyncio.sleep(0.03)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > 720:
            all_c = all_c[-720:]
        data[sym] = all_c
    return data


def run_symbol(symbol: str, candles: list[Candle]) -> list[dict]:
    if len(candles) < 200:
        return []
    calc = IndicatorCalculator()
    sg = SignalGenerator()
    closes = [c.close for c in candles]
    trades = []

    for i in range(200, len(candles)):
        window = candles[:i + 1]
        prices = closes[:i + 1]
        ema20 = calc.ema(prices, 20)
        ema50 = calc.ema(prices, 50)
        ema200 = calc.ema(prices, 200)
        rsi = calc.rsi(prices, 14)
        atr = calc.atr(window, 14)
        adx_val = calc.adx(window, 14)
        macd = calc.macd(prices)
        bb = calc.bollinger(prices)
        vwap = calc.vwap(window)
        vol_ratio = calc.volume_ratio([c.volume for c in window], 20)
        c = candles[i]
        fv = FeatureVector(
            timestamp_ms=c.timestamp, symbol=symbol,
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            ema20=ema20, ema50=ema50, ema200=ema200 or 0.0, rsi=rsi,
            macd_line=macd.get("macd", 0.0), macd_signal=macd.get("signal", 0.0),
            atr=atr, bb_upper=bb.get("upper", 0.0), bb_lower=bb.get("lower", 0.0),
            bb_middle=bb.get("middle", 0.0), adx=adx_val,
            volume_ma=0.0, volume_ratio=vol_ratio, obv=0.0, vwap=vwap,
            ema_bullish=ema20 > ema50 if ema20 and ema50 else False,
            price_above_ema50=c.close > ema50 if ema50 else False,
            rsi_overbought=rsi > 70, rsi_oversold=rsi < 30,
            htf_ema50=None, htf_ema200=None, htf_trend=None, integrity_score=1.0,
        )
        direction = sg.decide(symbol, fv, bar_idx=i)
        if direction is None:
            continue

        # Expected R (at signal time) = 1.0 for 1:1 RR with 2 ATR
        # But we can also compute actual outcome
        if direction == "BUY":
            sl = c.close - atr * 2
            tp = c.close + atr * 2
        else:
            sl = c.close + atr * 2
            tp = c.close - atr * 2

        outcome = "OPEN"
        exit_p = c.close
        outcome_ts = c.timestamp
        for j in range(i + 1, min(i + 168, len(candles))):
            cc = candles[j]
            if direction == "BUY":
                if cc.low <= sl:
                    outcome = "SL"; exit_p = sl; outcome_ts = cc.timestamp; break
                if cc.high >= tp:
                    outcome = "TP"; exit_p = tp; outcome_ts = cc.timestamp; break
            else:
                if cc.high >= sl:
                    outcome = "SL"; exit_p = sl; outcome_ts = cc.timestamp; break
                if cc.low <= tp:
                    outcome = "TP"; exit_p = tp; outcome_ts = cc.timestamp; break
        if outcome == "OPEN":
            exit_p = candles[min(i + 168, len(candles) - 1)].close
            outcome_ts = candles[min(i + 168, len(candles) - 1)].timestamp

        risk = abs(c.close - sl)
        if direction == "BUY":
            r = (exit_p - c.close) / risk if risk > 0 else 0
        else:
            r = (c.close - exit_p) / risk if risk > 0 else 0

        # Find captured score for this signal
        score_info = {}
        for s in _captured_signals:
            if s["ts"] == c.timestamp and s["symbol"] == symbol and s["direction"] == direction:
                score_info = s
                break

        trades.append({
            "ts": c.timestamp,
            "dt": datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc),
            "symbol": symbol,
            "dir": direction,
            "outcome": outcome,
            "r": round(r, 2),
            "entry": c.close,
            "atr": atr,
            "confidence": score_info.get("confidence", 0),
            "final_probability": score_info.get("final_probability", 0),
            "total_score": score_info.get("total_score", 0),
            "expected_r": 1.0,  # fixed 1:1 RR
            "adx": adx_val,
            "close_ts": outcome_ts,
        })
    return trades


def simulate_policy(trades: list[dict], policy: str) -> dict:
    """Simulate MAX_OPEN=1 with a given selection policy."""
    flat = sorted(trades, key=lambda x: x["ts"])
    executed = []
    missed = []
    open_until = 0

    i = 0
    while i < len(flat):
        if flat[i]["ts"] < open_until:
            i += 1
            continue

        # Find all signals in the same hour (conflict window = 1h)
        window_start = flat[i]["ts"]
        window_end = window_start + 3600 * 1000
        batch = [t for t in flat if window_start <= t["ts"] < window_end]

        if not batch:
            i += 1
            continue

        # Select one according to policy
        if policy == "first_come":
            chosen = batch[0]
        elif policy == "max_confidence":
            chosen = max(batch, key=lambda t: t.get("confidence", 0))
        elif policy == "max_probability":
            chosen = max(batch, key=lambda t: t.get("final_probability", 0))
        elif policy == "max_expected_r":
            chosen = max(batch, key=lambda t: t.get("expected_r", 0))
        elif policy == "max_adx":
            chosen = max(batch, key=lambda t: t.get("adx", 0))
        elif policy == "random":
            import random
            chosen = random.choice(batch)
        else:
            chosen = batch[0]

        executed.append(chosen)
        missed.extend(t for t in batch if t is not chosen)

        # Estimate hold duration from actual outcome
        hold_ms = chosen["close_ts"] - chosen["ts"]
        open_until = chosen["ts"] + max(hold_ms, 3600 * 1000)

        # Skip past this batch
        i = flat.index(batch[-1]) + 1

    n = len(executed)
    wins = [t for t in executed if t["outcome"] == "TP"]
    losses = [t for t in executed if t["outcome"] == "SL"]
    nw = len(wins)
    nl = len(losses)
    wr = nw / n * 100 if n > 0 else 0
    net_r = sum(t["r"] for t in executed)
    avg_r = net_r / n if n > 0 else 0
    gp = sum(t["r"] for t in wins)
    gl = sum(abs(t["r"]) for t in losses)
    pf = gp / max(gl, 0.01)

    # Max DD (R-based)
    dd = 0
    peak = 0
    cum = 0
    for t in executed:
        cum += t["r"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)

    return {
        "policy": policy,
        "total_signals": len(flat),
        "executed": n,
        "missed": len(missed),
        "tp": nw,
        "sl": nl,
        "win_rate": round(wr, 1),
        "pf": round(pf, 2),
        "net_r": round(net_r, 2),
        "avg_r": round(avg_r, 2),
        "max_dd_r": round(dd, 2),
        "missed_positive_r": round(sum(t["r"] for t in missed if t["r"] > 0), 2),
    }


def format_conflicts(trades: list[dict]) -> list[dict]:
    """Extract all conflict events for reporting."""
    flat = sorted(trades, key=lambda x: x["ts"])
    conflicts = []
    i = 0
    while i < len(flat):
        window_start = flat[i]["ts"]
        window_end = window_start + 3600 * 1000
        batch = [t for t in flat if window_start <= t["ts"] < window_end]
        if len(batch) > 1:
            conflicts.append({
                "ts": window_start,
                "dt": datetime.fromtimestamp(window_start / 1000, tz=timezone.utc).strftime("%m-%d %H:%M"),
                "signals": [
                    {
                        "symbol": t["symbol"],
                        "dir": t["dir"],
                        "confidence": t.get("confidence", 0),
                        "final_prob": t.get("final_probability", 0),
                        "adx": t.get("adx", 0),
                        "outcome": t["outcome"],
                        "r": t["r"],
                    }
                    for t in batch
                ],
            })
        i = flat.index(batch[-1]) + 1
    return conflicts


async def main():
    print("=" * 78)
    print("CONFLICT RESOLUTION AUDIT")
    print("=" * 78)

    print("\nFetching 30 days 1h data...")
    data = await fetch_all()
    print(f"Loaded {sum(len(v) for v in data.values())} candles across {len(data)} symbols\n")

    # Run strategy
    all_trades = []
    for sym in SYMBOLS:
        candles = data.get(sym, [])
        if len(candles) < 200:
            continue
        trades = run_symbol(sym, candles)
        all_trades.extend(trades)

    print(f"Total signals: {len(all_trades)}")

    # Extract conflicts
    conflicts = format_conflicts(all_trades)
    print(f"Conflict events: {len(conflicts)}")
    print(f"Signals in conflicts: {sum(len(c['signals']) for c in conflicts)}")
    print(f"Signals without conflict: {len(all_trades) - sum(len(c['signals']) for c in conflicts)}")

    # Show each conflict
    print("\n" + "=" * 78)
    print("CONFLICT EVENTS")
    print("=" * 78)
    for c in conflicts:
        print(f"\n  {c['dt']} — {len(c['signals'])} signals:")
        for s in c['signals']:
            mark = " ◀ CHOSEN" if s == c['signals'][0] else ""
            print(f"    {s['symbol']:9s} {s['dir']:4s} "
                  f"conf={s['confidence']:.3f} prob={s['final_prob']:.3f} "
                  f"ADX={s['adx']:.0f} → {s['outcome']:4s} R={s['r']:+.2f}{mark}")
        best = max(c['signals'], key=lambda s: s['r'])
        worst = min(c['signals'], key=lambda s: s['r'])
        chosen_r = c['signals'][0]['r']
        print(f"    Best:  {best['symbol']} R={best['r']:+.2f} "
              f"Worst: {worst['symbol']} R={worst['r']:+.2f} "
              f"Chosen: R={chosen_r:+.2f} "
              f"Loss vs best: {best['r'] - chosen_r:+.2f}R")

    # Policy comparison
    policies = [
        "first_come", "max_confidence",
        "max_probability", "max_expected_r", "max_adx", "random"
    ]

    print("\n" + "=" * 90)
    print("POLICY COMPARISON")
    print("=" * 90)
    hdr = f"{'Policy':20s} {'Exec':>5s} {'Miss':>5s} {'TP':>4s} {'SL':>4s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s} {'AvgR':>6s} {'MaxDD':>6s}"
    print(hdr)
    print("-" * 90)

    results = []
    # For random, run 10x and average
    random_results = []
    import random as rnd
    for seed in range(10):
        rnd.seed(seed)
        rr = simulate_policy(all_trades, "random")
        random_results.append(rr)

    for p in policies:
        if p == "random":
            continue  # handle separately
        r = simulate_policy(all_trades, p)
        results.append(r)
        flag = ""
        print(f"{r['policy']:20s} {r['executed']:>5d} {r['missed']:>5d} {r['tp']:>4d} {r['sl']:>4d} "
              f"{r['win_rate']:>5.1f} {r['pf']:>6.2f} {r['net_r']:>+5.2f} {r['avg_r']:>+5.2f} {r['max_dd_r']:>+5.2f}{flag}")

    # Average random
    avg_random = {
        "policy": "random (avg 10)",
        "executed": round(sum(r["executed"] for r in random_results) / len(random_results)),
        "missed": round(sum(r["missed"] for r in random_results) / len(random_results)),
        "tp": round(sum(r["tp"] for r in random_results) / len(random_results)),
        "sl": round(sum(r["sl"] for r in random_results) / len(random_results)),
        "win_rate": round(sum(r["win_rate"] for r in random_results) / len(random_results), 1),
        "pf": round(sum(r["pf"] for r in random_results) / len(random_results), 2),
        "net_r": round(sum(r["net_r"] for r in random_results) / len(random_results), 2),
        "avg_r": round(sum(r["avg_r"] for r in random_results) / len(random_results), 2),
        "max_dd_r": round(min(r["max_dd_r"] for r in random_results), 2),
    }
    print(f"{avg_random['policy']:20s} {avg_random['executed']:>5d} {avg_random['missed']:>5d} {avg_random['tp']:>4d} {avg_random['sl']:>4d} "
          f"{avg_random['win_rate']:>5.1f} {avg_random['pf']:>6.2f} {avg_random['net_r']:>+5.2f} {avg_random['avg_r']:>+5.2f} {avg_random['max_dd_r']:>+5.2f}")

    # Improvement analysis
    baseline = results[0]
    print("\n" + "=" * 78)
    print("IMPROVEMENT VS FIRST-COME-FIRST-SERVED")
    print("=" * 78)
    for r in results[1:]:
        imp = (r["net_r"] - baseline["net_r"]) / max(abs(baseline["net_r"]), 0.01) * 100 if baseline["net_r"] != 0 else 0
        print(f"  {r['policy']:20s} NetR={r['net_r']:+.2f} vs baseline={baseline['net_r']:+.2f}  ({imp:+.1f}%)")

    # Random
    imp = (avg_random["net_r"] - baseline["net_r"]) / max(abs(baseline["net_r"]), 0.01) * 100 if baseline["net_r"] != 0 else 0
    print(f"  {'random':20s} NetR={avg_random['net_r']:+.2f} vs baseline={baseline['net_r']:+.2f}  ({imp:+.1f}%)")

    print()
    best_policy = max(results[1:], key=lambda r: r["net_r"])
    print(f"  Best net R: {best_policy['policy']} ({best_policy['net_r']:+.2f})")
    print(f"  vs baseline ({baseline['policy']}): {baseline['net_r']:+.2f}")
    print(f"  Difference: {best_policy['net_r'] - baseline['net_r']:+.2f}R "
          f"over {baseline['executed']} trades")


if __name__ == "__main__":
    asyncio.run(main())
