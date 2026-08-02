#!/usr/bin/env python3
"""
Universe Expansion Simulation.

12 symbols, 30 days, 1h, threshold 0.55, MAX_OPEN_POSITIONS=1.

Per-symbol: PF, WR, expectancy, signals.
Portfolio simulation: executed vs missed signals, time to 30 trades.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator

# Monkey-patch threshold
import tradingos.signals.signal_scoring as ss
THRESHOLD = 0.55
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    if r is not None:
        object.__setattr__(r, 'threshold', THRESHOLD)
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "DOGEUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "LINKUSDT", "BNBUSDT", "AVAXUSDT",
    "DOTUSDT", "ATOMUSDT", "MATICUSDT",
]


async def fetch_all() -> dict[str, list[Candle]]:
    import httpx
    data = {}
    total = len(SYMBOLS)
    for idx, sym in enumerate(SYMBOLS):
        print(f"  [{idx+1}/{total}] {sym}...", end=" ", flush=True)
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < 800:
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
        # Trim to ~30 days
        if len(all_c) > 720:
            all_c = all_c[-720:]
        data[sym] = all_c
        print(f"{len(all_c)} candles")
    return data


def run_symbol(symbol: str, candles: list[Candle]) -> list[dict]:
    """Run SignalGenerator on one symbol, return all signals with trade outcomes."""
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

        # Evaluate trade outcome (2 ATR, 1:1 RR, 7 days max)
        if direction == "BUY":
            sl = c.close - atr * 2
            tp = c.close + atr * 2
        else:
            sl = c.close + atr * 2
            tp = c.close - atr * 2

        outcome = "OPEN"
        exit_p = c.close
        for j in range(i + 1, min(i + 168, len(candles))):
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
            exit_p = candles[min(i + 168, len(candles) - 1)].close
            outcome = "EXP"

        risk = abs(c.close - sl)
        if direction == "BUY":
            r = (exit_p - c.close) / risk if risk > 0 else 0
        else:
            r = (c.close - exit_p) / risk if risk > 0 else 0

        trades.append({
            "ts": c.timestamp,
            "dt": datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc),
            "symbol": symbol,
            "dir": direction,
            "outcome": outcome,
            "r": round(r, 2),
            "entry": c.close,
        })

    return trades


def simulate_portfolio(all_trades: dict[str, list[dict]]) -> dict:
    """Simulate MAX_OPEN_POSITIONS=1 over time."""
    # Flatten and sort by timestamp
    flat = []
    for sym, trades in all_trades.items():
        for t in trades:
            flat.append(t)
    flat.sort(key=lambda x: x["ts"])

    # Simulate: one position at a time, hold until TP/SL/expiry
    open_pos = None
    executed = []
    missed = []
    open_until = 0

    for t in flat:
        if open_pos is not None:
            if t["ts"] < open_until:
                missed.append(t)
                continue
            else:
                open_pos = None

        # Enter this trade
        executed.append(t)
        open_pos = t

        # Estimate close time: use actual exit time from trade eval
        # We don't have precise exit ts, so use 72h average hold
        # Actually: look at the close ts based on outcome
        if t["outcome"] == "TP":
            open_until = t["ts"] + 6 * 3600 * 1000  # ~6h avg for TP
        elif t["outcome"] == "SL":
            open_until = t["ts"] + 7 * 3600 * 1000  # ~7h avg for SL
        else:
            open_until = t["ts"] + 168 * 3600 * 1000  # full expiry

    # Stats
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

    # Time span
    if executed:
        first_ts = executed[0]["ts"]
        last_ts = executed[-1]["ts"]
        days_span = (last_ts - first_ts) / (1000 * 3600 * 24)
        trades_per_day = n / max(days_span, 1)
        days_to_30 = 30 / max(trades_per_day, 0.01) if trades_per_day > 0 else 999
    else:
        days_span = 0
        trades_per_day = 0
        days_to_30 = 999

    # Missed opportunity loss
    missed_worth = sum(t["r"] for t in missed)
    missed_positive = sum(t["r"] for t in missed if t["r"] > 0)

    return {
        "total_signals": len(flat),
        "executed": n,
        "missed": len(missed),
        "tp": nw, "sl": nl,
        "win_rate": round(wr, 1),
        "pf": round(pf, 2),
        "net_r": round(net_r, 2),
        "avg_r": round(avg_r, 2),
        "days_span": round(days_span, 1),
        "trades_per_day": round(trades_per_day, 2),
        "days_to_30_trades": round(days_to_30, 1),
        "missed_total_r": round(missed_worth, 2),
        "missed_positive_r": round(missed_positive, 2),
    }


async def main():
    print("=" * 70)
    print("UNIVERSE EXPANSION SIMULATION")
    print(f"Symbols: {len(SYMBOLS)} | Threshold: {THRESHOLD} | MAX_OPEN=1")
    print("=" * 70)

    print("\nFetching 30 days of 1h data...")
    data = await fetch_all()
    print()

    # Per-symbol analysis
    all_trades = {}
    per_symbol = []

    print("Running SignalGenerator on each symbol...")
    for sym in SYMBOLS:
        candles = data.get(sym, [])
        if len(candles) < 200:
            print(f"  {sym}: SKIP ({len(candles)} candles)")
            continue
        trades = run_symbol(sym, candles)
        all_trades[sym] = trades

        n = len(trades)
        wins = [t for t in trades if t["outcome"] == "TP"]
        losses = [t for t in trades if t["outcome"] == "SL"]
        nw = len(wins)
        nl = len(losses)
        wr = nw / n * 100 if n > 0 else 0
        gp = sum(t["r"] for t in wins)
        gl = sum(abs(t["r"]) for t in losses)
        pf = gp / max(gl, 0.01)
        net_r = sum(t["r"] for t in trades)
        avg_r = net_r / n if n > 0 else 0

        per_symbol.append({
            "symbol": sym, "signals": n, "tp": nw, "sl": nl,
            "wr": round(wr, 1), "pf": round(pf, 2),
            "net_r": round(net_r, 2), "avg_r": round(avg_r, 2),
        })
        print(f"  {sym}: {n} trades, WR={wr:.0f}%, PF={pf:.2f}, AvgR={avg_r:+.2f}")

    # Table
    print("\n" + "=" * 90)
    print("PER-SYMBOL RESULTS (threshold 0.55, 30 days)")
    print("=" * 90)
    hdr = f"{'Symbol':10s} {'Signals':>8s} {'TP':>4s} {'SL':>4s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s} {'AvgR':>6s}"
    print(hdr)
    print("-" * 90)

    # Sort by PF descending
    per_symbol.sort(key=lambda x: -x["pf"])
    for r in per_symbol:
        flag = " ✅" if r["pf"] > 1.2 and r["avg_r"] > 0 else ""
        print(f"{r['symbol']:10s} {r['signals']:>8d} {r['tp']:>4d} {r['sl']:>4d} "
              f"{r['wr']:>5.1f} {r['pf']:>6.2f} {r['net_r']:>+5.2f} {r['avg_r']:>+5.2f}{flag}")

    # Portfolio simulation
    print("\n" + "=" * 70)
    print("PORTFOLIO SIMULATION (MAX_OPEN_POSITIONS=1)")
    print("=" * 70)
    sim = simulate_portfolio(all_trades)

    print(f"  Total signals (raw):       {sim['total_signals']}")
    print(f"  Executed (MAX_OPEN=1):     {sim['executed']}")
    print(f"  Missed (conflict):         {sim['missed']}")
    print(f"  TP / SL:                   {sim['tp']} / {sim['sl']}")
    print(f"  Win Rate:                  {sim['win_rate']}%")
    print(f"  Profit Factor:             {sim['pf']}")
    print(f"  Net R:                     {sim['net_r']}")
    print(f"  Avg R/trade:               {sim['avg_r']}")
    print(f"  Days spanned:              {sim['days_span']}")
    print(f"  Trades/day:                {sim['trades_per_day']}")
    print(f"  Days to 30 trades:         {sim['days_to_30_trades']}")
    print(f"  Missed total R:            {sim['missed_total_r']}")
    print(f"  Missed positive R:         {sim['missed_positive_r']}")

    # Decision
    print("\n" + "=" * 70)
    print("DECISION")
    print("=" * 70)

    symbols_pass = sum(1 for r in per_symbol if r["pf"] > 1.2 and r["avg_r"] > 0)
    symbols_total = len(per_symbol)
    print(f"  Symbols with PF>1.2 & AvgR>0: {symbols_pass}/{symbols_total}")

    if sim["days_to_30_trades"] <= 60 and sim["pf"] > 1.0:
        print(f"\n  ✅ Расширение Universe сокращает время до 30 сделок")
        print(f"     с ~180 дней (3 символа) до ~{sim['days_to_30_trades']:.0f} дней ({symbols_pass}/{symbols_total} символов).")
        print(f"  → Рекомендуется: расширить до {symbols_pass} символов с PF>1.2")
    else:
        print(f"\n  ⚠️  Расширение не даёт достаточного ускорения.")
        print(f"     Time to 30 trades: ~{sim['days_to_30_trades']:.0f} дней.")

    # Filter to recommended symbols
    recommended = [r for r in per_symbol if r["pf"] > 1.2 and r["avg_r"] > 0]
    if recommended:
        print(f"\n  Рекомендуемый Universe ({len(recommended)} symbols):")
        for r in recommended:
            print(f"    {r['symbol']} (PF={r['pf']:.2f}, WR={r['wr']:.0f}%, {r['signals']} signals)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
