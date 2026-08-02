#!/usr/bin/env python3
"""
Opportunity Expansion Scan.
For each liquid USDT pair not in Universe V2, compute current probability.
Full 90d backtest only for candidates with prob >= 0.45.
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

UNI_V2 = {'BTCUSDT','ETHUSDT','DOGEUSDT','SOLUSDT','XRPUSDT','ADAUSDT','BNBUSDT',
          'BEATUSDT','COTIUSDT','KAITUSDT','LAUSDT'}


async def get_current_prob(sym: str, sem: asyncio.Semaphore) -> dict:
    """Fetch 200 1h candles, compute current probability."""
    import httpx
    async with sem:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            params = {"category":"linear","symbol":sym,"interval":"60","limit":200}
            if end_ms: params["end"]=str(end_ms)
            try:
                resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            except Exception as e:
                return {"symbol":sym,"error":str(e)}
            rows = resp.json().get("result",{}).get("list",[])
            for row in rows:
                all_c.append(Candle(int(row[0]),float(row[1]),float(row[2]),
                    float(row[3]),float(row[4]),float(row[5]) if row[5] else 0.0,sym,"1h"))
        all_c.sort(key=lambda x: x.timestamp)
    if len(all_c) < 200:
        return {"symbol":sym,"error":f"insufficient ({len(all_c)})","candles":len(all_c)}

    calc = IndicatorCalculator()
    closes = [c.close for c in all_c]
    i = len(all_c) - 1  # last bar
    w = all_c[:i+1]; p = closes[:i+1]
    adx_val = calc.adx(w,14); rsi = calc.rsi(p,14); atr = calc.atr(w,14)
    ema20 = calc.ema(p,20); ema50 = calc.ema(p,50); ema200 = calc.ema(p,200)
    macd = calc.macd(p); bb = calc.bollinger(p); vwap = calc.vwap(w)
    vr = calc.volume_ratio([c.volume for c in w],20)
    c = all_c[i]
    fv = FeatureVector(timestamp_ms=c.timestamp, symbol=sym,
        open=c.open,high=c.high,low=c.low,close=c.close,volume=c.volume,
        ema20=ema20,ema50=ema50,ema200=ema200 or 0.0,rsi=rsi,
        macd_line=macd.get("macd",0.0),macd_signal=macd.get("signal",0.0),
        atr=atr,bb_upper=bb.get("upper",0.0),bb_lower=bb.get("lower",0.0),
        bb_middle=bb.get("middle",0.0),adx=adx_val,
        volume_ma=0.0,volume_ratio=vr,obv=0.0,vwap=vwap,
        ema_bullish=ema20>ema50 if ema20 and ema50 else False,
        price_above_ema50=c.close>ema50 if ema50 else False,
        rsi_overbought=rsi>70,rsi_oversold=rsi<30,
        htf_ema50=None,htf_ema200=None,htf_trend=None,integrity_score=1.0)

    _score_holder["score"] = None
    direction = sg.decide(sym,fv,bar_idx=i)
    score = _score_holder.get("score")
    prob = score.final_probability if score else 0
    return {"symbol":sym,"prob":round(prob,4),"adx":round(adx_val,1),"rsi":round(rsi,1),
            "atr":round(atr,4),"direction":direction or "NONE"}


async def main():
    global sg
    sg = SignalGenerator()

    # Get liquid symbols
    import httpx
    resp = await httpx.AsyncClient(timeout=30).get(
        "https://api.bybit.com/v5/market/tickers?category=linear")
    tickers = resp.json().get("result",{}).get("list",[])
    candidates = []
    for t in tickers:
        sym = t.get("symbol","")
        if not sym.endswith("USDT"): continue
        try:
            vol = float(t.get("turnover24h",0))
        except: continue
        if vol >= 20_000_000:
            candidates.append(sym)

    # Filter out Universe V2
    new_candidates = sorted(set(candidates) - UNI_V2)
    print(f"Scanning {len(new_candidates)} liquid non-Universe symbols...\n")

    sem = asyncio.Semaphore(5)
    results = []
    for i, sym in enumerate(new_candidates):
        print(f"\r  [{i+1}/{len(new_candidates)}] {sym}...", end="", flush=True)
        r = await get_current_prob(sym, sem)
        results.append(r)

    print("\n\n" + "="*80)
    print("OPPORTUNITY EXPANSION SCAN — candidates with prob >= 0.45")
    print("="*80)

    # Filter to those with prob >= 0.45 (near threshold)
    near = [r for r in results if r.get("prob",0) >= 0.45]
    near.sort(key=lambda x: -x["prob"])

    if near:
        print(f"\nFound {len(near)} candidates with prob >= 0.45:\n")
        print(f"{'Symbol':15s} {'Prob':>6s} {'Dir':>5s} {'ADX':>5s} {'RSI':>5s} {'ATR':>8s}")
        print("-"*46)
        for r in near:
            print(f"{r['symbol']:15s} {r['prob']:.4f} {r.get('direction','?'):>5s} {r.get('adx',0):>5.1f} {r.get('rsi',0):>5.1f} {r.get('atr',0):>8.4f}")
    else:
        # Show top 10 even below 0.45
        all_sorted = sorted([r for r in results if r.get("prob",0) > 0], key=lambda x: -x["prob"])
        print(f"\nNo candidates with prob >= 0.45 found.")
        print(f"Max probability across {len(results)} symbols: {all_sorted[0]['prob']:.4f}" if all_sorted else "No probabilities computed.")
        print(f"\nTop 10 closest:")
        print(f"{'Symbol':15s} {'Prob':>6s} {'Dir':>5s} {'ADX':>5s} {'RSI':>5s}")
        print("-"*38)
        for r in all_sorted[:10]:
            print(f"{r['symbol']:15s} {r['prob']:.4f} {r.get('direction','?'):>5s} {r.get('adx',0):>5.1f} {r.get('rsi',0):>5.1f}")

    # Summary
    print(f"\n{'='*80}")
    max_prob_all = max([r.get("prob",0) for r in results]) if results else 0
    print(f"Global max probability across {len(results)} liquid symbols: {max_prob_all:.4f}")
    if max_prob_all < 0.54:
        print("→ No liquid pair on Bybit currently above 0.54.")
        print("→ The problem is a market-wide regime condition, not Universe selection.")
    elif max_prob_all >= 0.55:
        print(f"→ Found a pair with prob >= 0.55! {near[0]['symbol']} at {max_prob_all:.4f}")
        print("→ Universe expansion could unlock the first trade.")
    else:
        print(f"→ Near-threshold pairs exist (0.50-0.54).")
        print("→ Still below entry threshold 0.55.")

if __name__ == "__main__":
    asyncio.run(main())
