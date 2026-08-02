#!/usr/bin/env python3
"""
Signal Quality Analysis — измеряет качество сигналов SignalGenerator.

Для каждого исторического сигнала:
- entry по цене закрытия бара сигнала
- SL = entry ± 2×ATR, TP = entry ± 2×ATR (1:1 RR)
- обход последующих баров до TP/SL/expiry
- MFE, MAE, R-multiple

НЕ стратегия. НЕ бэктест. Измерение существующей модели.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator

SIGNALS = [
    {"symbol": "BTCUSDT", "dir": "BUY",  "ts": 1784188800000},
    {"symbol": "ETHUSDT", "dir": "BUY",  "ts": 1783814400000},
    {"symbol": "ETHUSDT", "dir": "SELL", "ts": 1784397600000},
    {"symbol": "ETHUSDT", "dir": "SELL", "ts": 1784404800000},
    {"symbol": "DOGEUSDT","dir": "BUY",  "ts": 1784574000000},
]

SL_ATR = 2.0
TP_ATR = 2.0
MAX_HOURS = 168  # 7 дней


async def fetch_symbol(symbol: str) -> list[Candle]:
    """Fetch all needed 1h candles (35 days + extra margin)."""
    import httpx
    all_c = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=15) as client:
        while len(all_c) < 1000:
            params = {"category": "linear", "symbol": symbol, "interval": "60", "limit": 200}
            if end_ms:
                params["end"] = str(end_ms)
            resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            data = resp.json()
            rows = data.get("result", {}).get("list", [])
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
            await asyncio.sleep(0.1)
    all_c.sort(key=lambda x: x.timestamp)
    return all_c


def eval_one(signal: dict, candles: list[Candle]) -> dict:
    """Оценить один сигнал."""
    sym = signal["symbol"]
    direction = signal["dir"]
    ts = signal["ts"]

    candles = [c for c in candles if c.symbol == sym]
    # Find signal bar
    idx = None
    for i, c in enumerate(candles):
        if c.timestamp == ts:
            idx = i
            break
    if idx is None:
        return {"symbol": sym, "dir": direction, "error": "bar not found"}

    # Entry = close of signal bar
    entry = candles[idx].close

    # ATR = среднее за 14 баров ДО сигнала
    if idx < 14:
        return {"symbol": sym, "dir": direction, "error": "not enough history for ATR"}
    calc = IndicatorCalculator()
    atr_val = calc.atr(candles[idx - 14:idx + 1], 14)

    if direction == "BUY":
        sl = entry - atr_val * SL_ATR
        tp = entry + atr_val * TP_ATR
    else:
        sl = entry + atr_val * SL_ATR
        tp = entry - atr_val * TP_ATR

    mfe_pct = 0.0
    mae_pct = 0.0
    outcome = "OPEN"
    exit_price = entry
    bars = 0

    for j in range(idx + 1, min(idx + MAX_HOURS + 1, len(candles))):
        c = candles[j]
        bars = j - idx

        if direction == "BUY":
            hi = (c.high - entry) / entry * 100
            lo = (c.low - entry) / entry * 100
            mfe_pct = max(mfe_pct, hi)
            mae_pct = min(mae_pct, lo)
            if c.low <= sl:
                outcome = "SL"
                exit_price = sl
                break
            if c.high >= tp:
                outcome = "TP"
                exit_price = tp
                break
        else:
            hi = (c.high - entry) / entry * 100
            lo = (c.low - entry) / entry * 100
            mfe_pct = min(mfe_pct, lo)
            mae_pct = max(mae_pct, hi)
            if c.high >= sl:
                outcome = "SL"
                exit_price = sl
                break
            if c.low <= tp:
                outcome = "TP"
                exit_price = tp
                break

    if outcome == "OPEN":
        exit_price = candles[min(idx + MAX_HOURS, len(candles) - 1)].close
        outcome = "EXPIRED"

    risk = abs(entry - sl)
    if direction == "BUY":
        r_m = (exit_price - entry) / risk if risk > 0 else 0
        pnl = (exit_price - entry) / entry * 100
    else:
        r_m = (entry - exit_price) / risk if risk > 0 else 0
        pnl = (entry - exit_price) / entry * 100

    return {
        "symbol": sym,
        "dir": direction,
        "entry_ts": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%m-%d %H:%M"),
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp": round(tp, 6),
        "atr": round(atr_val, 6),
        "outcome": outcome,
        "exit": round(exit_price, 6),
        "pnl_pct": round(pnl, 2),
        "r": round(r_m, 2),
        "mfe": round(mfe_pct, 2),
        "mae": round(mae_pct, 2),
        "hours": bars,
    }


async def main():
    print("=" * 78)
    print("SIGNAL QUALITY ANALYSIS — 5 исторических сигналов")
    print("=" * 78)

    symbols = set(s["symbol"] for s in SIGNALS)
    data: dict[str, list[Candle]] = {}
    for sym in symbols:
        print(f"  Fetching {sym}...", end=" ")
        sys.stdout.flush()
        data[sym] = await fetch_symbol(sym)
        print(f"{len(data[sym])} candles")

    results = []
    for s in SIGNALS:
        r = eval_one(s, data[s["symbol"]])
        results.append(r)

    # Table
    hdr = f"{'Symbol':9s} {'Dir':4s} {'Entry':>10s} {'SL':>10s} {'TP':>10s} {'Out':6s} {'Pnl%':7s} {'R':6s} {'MFE%':6s} {'MAE%':6s} {'Hrs':4s}"
    print("\n" + hdr)
    print("-" * 78)
    for r in results:
        if "error" in r:
            print(f"  {r['symbol']:9s} {r['dir']:4s} ERROR: {r['error']}")
            continue
        print(f"{r['symbol']:9s} {r['dir']:4s} {r['entry']:>10.6f} {r['sl']:>10.6f} {r['tp']:>10.6f} "
              f"{r['outcome']:6s} {r['pnl_pct']:>+6.2f}% {r['r']:>+5.2f} {r['mfe']:>+5.2f}% {r['mae']:>+5.2f}% {r['hours']:4d}")

    # Summary
    wins = [r for r in results if r.get("outcome") == "TP"]
    losses = [r for r in results if r.get("outcome") == "SL"]
    n = len(results)
    nw = len(wins)
    nl = len(losses)
    wr = nw / n * 100
    gp = sum(r["pnl_pct"] for r in wins)
    gl = sum(abs(r["pnl_pct"]) for r in losses)
    pf = gp / max(gl, 0.01)
    net = sum(r["pnl_pct"] for r in results if "pnl_pct" in r)
    avg_r = sum(r["r"] for r in results if "r" in r) / max(n, 1)

    print(f"\n{'='*50}")
    print(f"SUMMARY")
    print(f"{'='*50}")
    print(f"  Signals:     {n}")
    print(f"  TP (wins):   {nw}")
    print(f"  SL (losses): {nl}")
    print(f"  Win Rate:    {wr:.0f}%")
    print(f"  Profit Fct:  {pf:.2f}")
    print(f"  Net PnL:     {net:+.2f}%")
    print(f"  Avg R:       {avg_r:+.2f}")
    print(f"  Expectancy:  {avg_r:+.2f}R/trade")
    print()

    if pf >= 1.5 and wr >= 30:
        print("  ✅ Сигналы имеют положительное ожидание.")
    elif pf >= 1.0:
        print("  ⚠️  Слабый PF. Нужно больше данных.")
    else:
        print("  ❌ Сигналы НЕ имеют положительного ожидания.")

    if net > 0:
        print(f"\n  При текущей частоте (~5/мес) ожидание: {avg_r:+.2f}R на сделку.")
        print(f"  Для 30 сделок нужно ~6 месяцев.")
    else:
        print(f"\n  Отрицательное ожидание. Threshold не причина.")

    print()


if __name__ == "__main__":
    asyncio.run(main())
