#!/usr/bin/env python3
"""
Universe Review Tool v0.
Standalone analysis script. Does NOT modify runtime.
Reports only. Human decides ADD/REMOVE/KEEP.

Usage:
    python3 tools/universe_review.py
    python3 tools/universe_review.py --symbols BTCUSDT,ETHUSDT  # check specific
    python3 tools/universe_review.py --days 30                  # shorter period
"""
from __future__ import annotations

import asyncio, json, logging, sys
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
_score_holder = {"score": None}
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched

# Universe V2 current
CURRENT_UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "BNBUSDT",
    "BEATUSDT", "COTIUSDT", "KAITOUSDT", "LAUSDT",
]


async def analyze_symbol(sym: str, days: int, sem: asyncio.Semaphore) -> dict:
    """Fetch data, run SignalGenerator, collect statistics."""
    import httpx
    async with sem:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < days * 24:
                params = {"category": "linear", "symbol": sym, "interval": "60", "limit": 200}
                if end_ms:
                    params["end"] = str(end_ms)
                try:
                    resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                except Exception:
                    break
                rows = resp.json().get("result", {}).get("list", [])
                if not rows:
                    break
                for row in rows:
                    all_c.append(Candle(
                        int(row[0]), float(row[1]), float(row[2]),
                        float(row[3]), float(row[4]),
                        float(row[5]) if row[5] else 0.0, sym, "1h",
                    ))
                end_ms = int(rows[-1][0]) - 1
                await asyncio.sleep(0.02)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > days * 24:
            all_c = all_c[-(days * 24):]

    result = {"symbol": sym, "candles": len(all_c)}

    if len(all_c) < 200:
        result["error"] = f"insufficient ({len(all_c)} candles)"
        return result

    # Average ADX, ATR, spread estimate (high-low / open)
    calc = IndicatorCalculator()
    closes = [c.close for c in all_c]
    adx_vals = []
    atr_vals = []
    spread_pcts = []
    for i in range(200, len(all_c)):
        w = all_c[:i + 1]
        p = closes[:i + 1]
        adx_vals.append(calc.adx(w, 14) if len(w) >= 14 else 0)
        atr_vals.append(calc.atr(w, 14) if len(w) >= 14 else 0)
        spread_pcts.append((all_c[i].high - all_c[i].low) / all_c[i].open * 100 if all_c[i].open else 0)

    result["adx_avg"] = round(sum(adx_vals) / len(adx_vals), 1)
    result["atr_avg"] = round(sum(atr_vals) / len(atr_vals), 4)
    result["spread_pct_avg"] = round(sum(spread_pcts) / len(spread_pcts), 2)

    # Run SignalGenerator
    sg = SignalGenerator()
    trades = []
    for i in range(200, len(all_c)):
        w = all_c[:i + 1]
        p = closes[:i + 1]
        ema20 = calc.ema(p, 20)
        ema50 = calc.ema(p, 50)
        ema200 = calc.ema(p, 200)
        rsi = calc.rsi(p, 14)
        atr = calc.atr(w, 14)
        adx_val = calc.adx(w, 14)
        macd = calc.macd(p)
        bb = calc.bollinger(p)
        vwap = calc.vwap(w)
        vr = calc.volume_ratio([c.volume for c in w], 20)
        c = all_c[i]
        fv = FeatureVector(
            timestamp_ms=c.timestamp, symbol=sym,
            open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            ema20=ema20, ema50=ema50, ema200=ema200 or 0.0, rsi=rsi,
            macd_line=macd.get("macd", 0.0), macd_signal=macd.get("signal", 0.0),
            atr=atr, bb_upper=bb.get("upper", 0.0), bb_lower=bb.get("lower", 0.0),
            bb_middle=bb.get("middle", 0.0), adx=adx_val,
            volume_ma=0.0, volume_ratio=vr, obv=0.0, vwap=vwap,
            ema_bullish=ema20 > ema50 if ema20 and ema50 else False,
            price_above_ema50=c.close > ema50 if ema50 else False,
            rsi_overbought=rsi > 70, rsi_oversold=rsi < 30,
            htf_ema50=None, htf_ema200=None, htf_trend=None, integrity_score=1.0,
        )
        _score_holder["score"] = None
        direction = sg.decide(sym, fv, bar_idx=i)
        if direction is None:
            continue

        if direction == "BUY":
            sl = c.close - atr * 2
            tp = c.close + atr * 2
        else:
            sl = c.close + atr * 2
            tp = c.close - atr * 2

        outcome = "OPEN"
        exit_p = c.close
        for j in range(i + 1, min(i + 168, len(all_c))):
            cc = all_c[j]
            if direction == "BUY":
                if cc.low <= sl: outcome = "SL"; exit_p = sl; break
                if cc.high >= tp: outcome = "TP"; exit_p = tp; break
            else:
                if cc.high >= sl: outcome = "SL"; exit_p = sl; break
                if cc.low <= tp: outcome = "TP"; exit_p = tp; break
        if outcome == "OPEN":
            exit_p = all_c[min(i + 168, len(all_c) - 1)].close
            outcome = "EXP"

        risk = abs(c.close - sl)
        r = ((exit_p - c.close) / risk if direction == "BUY" else (c.close - exit_p) / risk) if risk > 0 else 0
        trades.append({"outcome": outcome, "r": round(r, 2)})

    n = len(trades)
    wins = [t for t in trades if t["outcome"] == "TP"]
    losses = [t for t in trades if t["outcome"] == "SL"]
    nw = len(wins)
    nl = len(losses)
    wr = nw / n * 100 if n else 0
    net_r = sum(t["r"] for t in trades)
    gp = sum(t["r"] for t in wins)
    gl = sum(abs(t["r"]) for t in losses)
    pf = gp / max(gl, 0.01)
    avg_r = net_r / n if n else 0

    result.update({
        "trades": n, "wr": round(wr, 1), "pf": round(pf, 2),
        "net_r": round(net_r, 2), "avg_r": round(avg_r, 2),
    })
    return result


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Universe Review Tool v0")
    parser.add_argument("--symbols", help="Comma-separated symbols (default: Universe V2)")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--candidates", action="store_true", help="Also scan liquid non-Universe symbols")
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else CURRENT_UNIVERSE

    if args.candidates:
        print("Scanning liquid non-Universe symbols...")

    print("=" * 80)
    print(f"UNIVERSE REVIEW — {args.days} days, threshold={THRESHOLD}")
    print("=" * 80)

    sem = asyncio.Semaphore(5)
    results = []
    for i, sym in enumerate(symbols):
        print(f"\r  [{i+1}/{len(symbols)}] {sym}...", end="", flush=True)
        r = await analyze_symbol(sym, args.days, sem)
        results.append(r)

    print("\n")

    # Current Universe review
    print("CURRENT UNIVERSE (Universe V2)")
    print("-" * 100)
    hdr = f"{'Symbol':14s} {'Candles':>8s} {'Trades':>7s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s} {'ADX':>5s} {'ATR':>7s} {'Spread%':>7s} {'Status':>10s}"
    print(hdr)
    print("-" * 100)

    for r in results:
        if r.get("error"):
            print(f"{r['symbol']:14s} — {r['error']}")
            continue
        status = "KEEP ✅" if r["pf"] >= 1.2 and r["wr"] >= 45 and r["trades"] >= 3 else ("WATCH ⚠️" if r["pf"] >= 0.8 else "REVIEW ❌")
        print(f"{r['symbol']:14s} {r['candles']:>8d} {r['trades']:>7d} {r['wr']:>5.1f} {r['pf']:>6.2f} {r['net_r']:>+5.2f} {r.get('adx_avg',0):>5.1f} {r.get('atr_avg',0):>7.4f} {r.get('spread_pct_avg',0):>6.2f}% {status:>10s}")

    # Summary
    keep = [r for r in results if not r.get("error") and r["pf"] >= 1.2 and r["wr"] >= 45 and r["trades"] >= 3]
    watch = [r for r in results if not r.get("error") and r["pf"] >= 0.8 and not (r["pf"] >= 1.2 and r["wr"] >= 45 and r["trades"] >= 3)]
    review = [r for r in results if not r.get("error") and r["pf"] < 0.8]
    print(f"\nSummary: {len(keep)} KEEP | {len(watch)} WATCH | {len(review)} REVIEW")

    # Decision support
    print(f"\n{'='*80}")
    print("RECOMMENDATION (human decision required)")
    print(f"{'='*80}")
    if review:
        print(f"\nConsider removing: {', '.join(r['symbol'] for r in review)}")
    if watch:
        print(f"\nMonitor: {', '.join(r['symbol'] for r in watch)}")
    print(f"\nSymbols passing all criteria: {', '.join(r['symbol'] for r in keep)}" if keep else "\nNo symbols pass all criteria")

    # Save report
    report = {
        "time": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "results": results,
        "summary": {"keep": len(keep), "watch": len(watch), "review": len(review)},
    }
    report_path = ROOT / "docs" / "reports" / "UNIVERSE_REVIEW_REPORT.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
