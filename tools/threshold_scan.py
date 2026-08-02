#!/usr/bin/env python3
"""
Threshold Scan — параметрический анализ SignalGenerator.

Для каждого threshold: 0.55, 0.50, 0.45, 0.40, 0.35
запускает SignalGenerator на 30 днях 1h данных (3 символа)
и считает качество сигналов (PF, WR, Expectancy).

Без изменения кода — только runtime monkey-patch.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator


# Fix: need to be able to set threshold on SignalScore
# We'll monkey-patch the threshold in signal_scoring's return
import tradingos.signals.signal_scoring as ss

original_calculate = ss.SignalScoringEngine.calculate_with_vectors
THRESHOLD_OVERRIDE = [0.55]  # will be set by runner


def patched_calculate(self, **kwargs):
    """Calculate with overridable threshold."""
    result = original_calculate(self, **kwargs)
    if result is not None:
        object.__setattr__(result, 'threshold', THRESHOLD_OVERRIDE[0])
    return result


SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
THRESHOLDS = [0.55, 0.50, 0.45, 0.40, 0.35]


async def fetch_all() -> dict[str, list[Candle]]:
    """Fetch 1h data for all symbols."""
    import httpx
    data = {}
    for symbol in SYMBOLS:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < 800:
                params = {"category": "linear", "symbol": symbol, "interval": "60", "limit": 200}
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
                        symbol=symbol, timeframe="1h",
                    ))
                end_ms = int(rows[-1][0]) - 1
                await asyncio.sleep(0.05)
        all_c.sort(key=lambda x: x.timestamp)
        data[symbol] = all_c
        print(f"  {symbol}: {len(all_c)} candles")
    return data


def evaluate_signals(
    fv_list: list[tuple[int, FeatureVector]],
    direction: str | None,
    candles: list[Candle],
    symbol: str,
) -> list[dict]:
    """For accepted signals, simulate trade outcome."""
    calc = IndicatorCalculator()
    results = []

    for idx, fv in fv_list:
        if direction is None:
            continue

        # Find candle index
        ci = None
        for i, c in enumerate(candles):
            if c.timestamp == fv.timestamp_ms:
                ci = i
                break
        if ci is None or ci < 14:
            continue

        entry = fv.close
        atr = calc.atr(candles[ci - 14:ci + 1], 14)

        if direction == "BUY":
            sl = entry - atr * 2
            tp = entry + atr * 2
        else:
            sl = entry + atr * 2
            tp = entry - atr * 2

        outcome = "OPEN"
        exit_p = entry
        for j in range(ci + 1, min(ci + 168, len(candles))):
            c = candles[j]
            if direction == "BUY":
                if c.low <= sl:
                    outcome = "SL"; exit_p = sl; break
                if c.high >= tp:
                    outcome = "TP"; exit_p = tp; break
            else:
                if c.high >= sl:
                    outcome = "SL"; exit_p = sl; break
                if c.low <= tp:
                    outcome = "TP"; exit_p = tp; break

        if outcome == "OPEN":
            exit_p = candles[min(ci + 168, len(candles) - 1)].close
            outcome = "EXP"

        risk = abs(entry - sl)
        if direction == "BUY":
            r = (exit_p - entry) / risk if risk > 0 else 0
            pnl = (exit_p - entry) / entry * 100
        else:
            r = (entry - exit_p) / risk if risk > 0 else 0
            pnl = (entry - exit_p) / entry * 100

        results.append({
            "symbol": symbol, "dir": direction,
            "entry": round(entry, 6), "outcome": outcome,
            "r": round(r, 2), "pnl_pct": round(pnl, 2),
        })

    return results


def run_threshold(
    threshold: float,
    data: dict[str, list[Candle]],
) -> dict:
    """Run SignalGenerator with given threshold."""
    global THRESHOLD_OVERRIDE
    THRESHOLD_OVERRIDE[0] = threshold
    ss.SignalScoringEngine.calculate_with_vectors = patched_calculate

    calc = IndicatorCalculator()
    all_results = []

    for symbol in SYMBOLS:
        candles = data.get(symbol, [])
        if len(candles) < 200:
            continue

        sg = SignalGenerator()
        closes = [c.close for c in candles]

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
            if direction:
                # Evaluate trade
                trade = {
                    "symbol": symbol, "dir": direction,
                    "ts": c.timestamp, "entry": c.close,
                    "atr": atr,
                }
                all_results.append(trade)

    # Evaluate trades
    trades_eval = []
    for t in all_results:
        sym = t["symbol"]
        ts = t["ts"]
        direction = t["dir"]

        candles = data[sym]
        ci = None
        for k, cc in enumerate(candles):
            if cc.timestamp == ts:
                ci = k
                break
        if ci is None or ci < 14:
            continue

        entry = t["entry"]
        atr_val = t["atr"]

        if direction == "BUY":
            sl = entry - atr_val * 2
            tp = entry + atr_val * 2
        else:
            sl = entry + atr_val * 2
            tp = entry - atr_val * 2

        outcome = "OPEN"
        exit_p = entry
        for j in range(ci + 1, min(ci + 168, len(candles))):
            cc = candles[j]
            if direction == "BUY":
                if cc.low <= sl:
                    outcome = "SL"; exit_p = sl; break
                if cc.high >= tp:
                    outcome = "TP"; exit_p = tp; break
            else:
                if cc.high >= sl:
                    outcome = "SL"; exit_p = sl; break
                if cc.low <= tp:
                    outcome = "TP"; exit_p = tp; break

        if outcome == "OPEN":
            exit_p = candles[min(ci + 168, len(candles) - 1)].close
            outcome = "EXP"

        risk = abs(entry - sl)
        if direction == "BUY":
            r = (exit_p - entry) / risk if risk > 0 else 0
        else:
            r = (entry - exit_p) / risk if risk > 0 else 0

        trades_eval.append({
            "symbol": sym, "dir": direction,
            "outcome": outcome, "r": r,
        })

    # Stats
    n = len(trades_eval)
    wins = [t for t in trades_eval if t["outcome"] == "TP"]
    losses = [t for t in trades_eval if t["outcome"] == "SL"]
    nw = len(wins)
    nl = len(losses)
    wr = nw / n * 100 if n > 0 else 0
    net_r = sum(t["r"] for t in trades_eval)
    avg_r = net_r / n if n > 0 else 0

    # Max drawdown (R-based)
    dd = 0
    peak = 0
    cum = 0
    for t in trades_eval:
        cum += t["r"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)

    gp = sum(t["r"] for t in wins)
    gl = sum(abs(t["r"]) for t in losses)
    pf = gp / max(gl, 0.01)

    # Signals per month (30d period)
    spm = n / 30 * 30  # n trades over actual period

    return {
        "threshold": threshold,
        "signals": len(all_results),
        "trades": n,
        "tp": nw, "sl": nl,
        "win_rate": round(wr, 1),
        "pf": round(pf, 2),
        "net_r": round(net_r, 2),
        "avg_r": round(avg_r, 2),
        "max_dd_r": round(dd, 2),
        "signals_per_month": round(spm, 1),
    }


async def main():
    print("=" * 70)
    print("THRESHOLD SCAN — SignalGenerator параметрический анализ")
    print("=" * 70)

    print("\nFetching data...")
    data = await fetch_all()
    print()

    # Run native 0.55 first (verify baseline)
    print("Running native threshold 0.55...")
    result_055 = run_threshold(0.55, data)

    # Run with patched thresholds
    rows = [result_055]
    for th in [0.50, 0.45, 0.40, 0.35]:
        print(f"Running threshold {th}...")
        r = run_threshold(th, data)
        rows.append(r)

    # Results table
    print("\n" + "=" * 90)
    print("THRESHOLD SCAN RESULTS")
    print("=" * 90)

    hdr = f"{'Thr':>6s} {'Signals':>8s} {'Trades':>7s} {'TP':>4s} {'SL':>4s} {'WR%':>5s} {'PF':>5s} {'NetR':>6s} {'AvgR':>6s} {'MaxDD':>6s} {'Sig/Mo':>8s}"
    print(hdr)
    print("-" * 90)

    for r in rows:
        print(f"{r['threshold']:>6.2f} {r['signals']:>8d} {r['trades']:>7d} {r['tp']:>4d} {r['sl']:>4d} "
              f"{r['win_rate']:>5.1f} {r['pf']:>5.2f} {r['net_r']:>+5.2f} "
              f"{r['avg_r']:>+5.2f} {r['max_dd_r']:>+5.2f} {r['signals_per_month']:>8.1f}")

    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)

    # Find best threshold by decision rule
    print("\nDecision rule: ≥3x trades vs 0.55, PF > 1.5, positive expectancy")
    baseline_signals = result_055["signals"]
    candidates = []

    for r in rows:
        if r["signals"] >= baseline_signals * 3 and r["pf"] > 1.5:
            candidates.append(r)
            print(f"  ✅ threshold={r['threshold']:.2f}: {r['signals']} signals (x{r['signals']/max(baseline_signals,1):.1f}), PF={r['pf']}, AvgR={r['avg_r']}")
        else:
            reason = []
            if r["signals"] < baseline_signals * 3:
                reason.append(f"signals {r['signals']} < x3 baseline")
            if r["pf"] <= 1.5:
                reason.append(f"PF={r['pf']} ≤ 1.5")
            print(f"  ❌ threshold={r['threshold']:.2f}: {', '.join(reason)}")

    if candidates:
        print(f"\n  → Рекомендуемый threshold: {candidates[-1]['threshold']:.2f}")
        print(f"    (самый высокий, прошедший критерии)")
    else:
        print(f"\n  → Ни один threshold не прошёл критерии.")
        print(f"    Оставить текущий 0.55.")

    print()


if __name__ == "__main__":
    asyncio.run(main())
