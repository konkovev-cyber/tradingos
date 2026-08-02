#!/usr/bin/env python3
"""
90 DAY EDGE VALIDATION — проверка устойчивости SignalGenerator на 90 днях.
11 symbols, 1h, threshold 0.55, MAX_OPEN=1.
Разбивка: 3 периода по 30 дней, посимвольно.
"""
from __future__ import annotations

import asyncio, logging, sys, random as rnd
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
def _patched(self, **kw):
    r = _orig(self, **kw)
    if r is not None:
        object.__setattr__(r, 'threshold', THRESHOLD)
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched

ALL_SYMBOLS = [
    "BTCUSDT","ETHUSDT","DOGEUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","LINKUSDT","BNBUSDT","AVAXUSDT","DOTUSDT","ATOMUSDT",
]
SYMBOLS = [s for s in ALL_SYMBOLS if True]
DAYS = 90
UNIVERSE_NAME = "11"  # "11" or "7"


async def fetch_all() -> dict[str, list[Candle]]:
    import httpx
    data = {}
    for idx, sym in enumerate(SYMBOLS):
        print(f"  [{idx+1}/{len(SYMBOLS)}] {sym}...", end=" ", flush=True)
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < DAYS * 24:
                params = {"category": "linear", "symbol": sym, "interval": "60", "limit": 200}
                if end_ms: params["end"] = str(end_ms)
                resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                rows = resp.json().get("result", {}).get("list", [])
                if not rows: break
                for row in rows:
                    all_c.append(Candle(int(row[0]), float(row[1]), float(row[2]), float(row[3]),
                        float(row[4]), float(row[5]) if row[5] else 0.0, sym, "1h"))
                end_ms = int(rows[-1][0]) - 1
                await asyncio.sleep(0.03)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > DAYS * 24: all_c = all_c[-(DAYS*24):]
        data[sym] = all_c
        print(f"{len(all_c)} candles")
    return data


def run_symbol(symbol: str, candles: list[Candle]) -> list[dict]:
    if len(candles) < 200: return []
    calc = IndicatorCalculator()
    sg = SignalGenerator()
    closes = [c.close for c in candles]
    trades = []

    for i in range(200, len(candles)):
        w = candles[:i+1]; p = closes[:i+1]
        ema20=calc.ema(p,20); ema50=calc.ema(p,50); ema200=calc.ema(p,200)
        rsi=calc.rsi(p,14); atr=calc.atr(w,14); adx=calc.adx(w,14)
        macd=calc.macd(p); bb=calc.bollinger(p); vwap=calc.vwap(w)
        vr=calc.volume_ratio([c.volume for c in w],20)
        c=candles[i]
        fv=FeatureVector(timestamp_ms=c.timestamp, symbol=symbol, open=c.open, high=c.high,
            low=c.low, close=c.close, volume=c.volume, ema20=ema20, ema50=ema50,
            ema200=ema200 or 0.0, rsi=rsi, macd_line=macd.get("macd",0.0),
            macd_signal=macd.get("signal",0.0), atr=atr, bb_upper=bb.get("upper",0.0),
            bb_lower=bb.get("lower",0.0), bb_middle=bb.get("middle",0.0), adx=adx,
            volume_ma=0.0, volume_ratio=vr, obv=0.0, vwap=vwap,
            ema_bullish=ema20>ema50 if ema20 and ema50 else False,
            price_above_ema50=c.close>ema50 if ema50 else False,
            rsi_overbought=rsi>70, rsi_oversold=rsi<30,
            htf_ema50=None, htf_ema200=None,htf_trend=None, integrity_score=1.0)
        d = sg.decide(symbol, fv, bar_idx=i)
        if d is None: continue

        sl=c.close-atr*2 if d=="BUY" else c.close+atr*2
        tp=c.close+atr*2 if d=="BUY" else c.close-atr*2

        outcome="OPEN"; exit_p=c.close; close_ts=c.timestamp
        for j in range(i+1, min(i+168, len(candles))):
            cc=candles[j]
            if d=="BUY":
                if cc.low<=sl: outcome="SL"; exit_p=sl; close_ts=cc.timestamp; break
                if cc.high>=tp: outcome="TP"; exit_p=tp; close_ts=cc.timestamp; break
            else:
                if cc.high>=sl: outcome="SL"; exit_p=sl; close_ts=cc.timestamp; break
                if cc.low<=tp: outcome="TP"; exit_p=tp; close_ts=cc.timestamp; break
        if outcome=="OPEN":
            exit_p=candles[min(i+168,len(candles)-1)].close
            close_ts=candles[min(i+168,len(candles)-1)].timestamp

        risk=abs(c.close-sl); r=((exit_p-c.close)/risk if d=="BUY" else (c.close-exit_p)/risk) if risk>0 else 0
        trades.append({"ts":c.timestamp,"symbol":symbol,"dir":d,"outcome":outcome,
            "r":round(r,2),"entry":c.close,"close_ts":close_ts})
    return trades


def simulate(trades: list[dict]) -> dict:
    flat=sorted(trades, key=lambda x: x["ts"])
    executed=[]; missed=[]; open_until=0
    i=0
    while i<len(flat):
        if flat[i]["ts"]<open_until: i+=1; continue
        ws=flat[i]["ts"]; we=ws+3600*1000
        batch=[t for t in flat if ws<=t["ts"]<we]
        if not batch: i+=1; continue
        chosen=batch[0]; executed.append(chosen)
        missed.extend(t for t in batch if t is not chosen)
        hold_ms=chosen["close_ts"]-chosen["ts"]
        open_until=chosen["ts"]+max(hold_ms,3600*1000)
        i=flat.index(batch[-1])+1

    n=len(executed); w=[t for t in executed if t["outcome"]=="TP"]
    l=[t for t in executed if t["outcome"]=="SL"]
    nw=len(w); nl=len(l); wr=nw/n*100 if n else 0
    net_r=sum(t["r"] for t in executed); avg_r=net_r/n if n else 0
    gp=sum(t["r"] for t in w); gl=sum(abs(t["r"]) for t in l)
    pf=gp/max(gl,0.01)

    dd=0; peak=0; cum=0
    for t in executed: cum+=t["r"]; peak=max(peak,cum); dd=min(dd,cum-peak)
    return {"signals":len(flat),"executed":n,"missed":len(missed),"tp":nw,"sl":nl,
        "wr":round(wr,1),"pf":round(pf,2),"net_r":round(net_r,2),"avg_r":round(avg_r,2),
        "max_dd":round(dd,2)}


async def main():
    print("="*70)
    print("90 DAY EDGE VALIDATION")
    print(f"Symbols: {len(SYMBOLS)} | 1h | threshold={THRESHOLD} | MAX_OPEN=1")
    print("="*70)

    print(f"\nFetching {DAYS} days...")
    data=await fetch_all()

    # Run all
    all_trades=[]
    print("\nRunning SignalGenerator...")
    for sym in SYMBOLS:
        candles=data.get(sym,[])
        if len(candles)<200: continue
        t=run_symbol(sym,candles)
        all_trades.extend(t)
        print(f"  {sym}: {len(t)} signals")
    print(f"\nTotal signals: {len(all_trades)}")

    # 1. Overall
    print("\n"+"="*70)
    print(f"1. OVERALL RESULT ({UNIVERSE_NAME} symbols, 90 days)")
    print("="*70)
    overall=simulate(all_trades)
    for k,v in overall.items(): print(f"  {k}: {v}")

    # 2. Split by period
    print("\n"+"="*70)
    print("2. PERIOD BREAKDOWN")
    print("="*70)
    all_sorted=sorted(all_trades, key=lambda x: x["ts"])
    if all_sorted:
        start_ms=all_sorted[0]["ts"]
        period_ms=30*24*3600*1000
        hdr=f"{'Period':12s} {'Signals':>8s} {'Exec':>6s} {'Miss':>6s} {'TP':>4s} {'SL':>4s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s}"
        print(hdr); print("-"*70)
        periods=[]
        for pi in range(3):
            p_start=start_ms+pi*period_ms
            p_end=p_start+period_ms
            p_trades=[t for t in all_trades if p_start<=t["ts"]<p_end]
            if p_trades:
                r=simulate(p_trades)
                periods.append(r)
                print(f"{f'Period {pi+1} (d{pi*30}-{(pi+1)*30})':12s} {r['signals']:>8d} {r['executed']:>6d} {r['missed']:>6d} {r['tp']:>4d} {r['sl']:>4d} {r['wr']:>5.1f} {r['pf']:>6.2f} {r['net_r']:>+5.2f}")
            else:
                periods.append(None)
                print(f"{f'Period {pi+1}':12s} no signals")

    # 3. Per symbol
    print("\n"+"="*70)
    print("3. PER-SYMBOL BREAKDOWN")
    print("="*70)
    sym_trades=defaultdict(list)
    for t in all_trades: sym_trades[t["symbol"]].append(t)
    hdr=f"{'Symbol':10s} {'Signals':>8s} {'Exec':>6s} {'TP':>4s} {'SL':>4s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s}"
    print(hdr); print("-"*70)
    sym_results=[]
    for sym in SYMBOLS:
        st=sym_trades.get(sym,[])
        if not st: continue
        r=simulate(st)
        sym_results.append(r)
        print(f"{sym:10s} {r['signals']:>8d} {r['executed']:>6d} {r['tp']:>4d} {r['sl']:>4d} {r['wr']:>5.1f} {r['pf']:>6.2f} {r['net_r']:>+5.2f}")

    # 4. Stability check
    print("\n"+"="*70)
    print("4. STABILITY CHECK")
    print("="*70)
    valid_periods=[p for p in periods if p is not None]
    all_pf=[p["pf"] for p in valid_periods]
    all_netr=[p["net_r"] for p in valid_periods]
    profitable_periods=sum(1 for nr in all_netr if nr>0)

    print(f"  Periods with data: {len(valid_periods)}/3")
    print(f"  PF per period: {[round(p,2) for p in all_pf]}")
    print(f"  NetR per period: {[round(n,2) for n in all_netr]}")
    print(f"  Profitable periods: {profitable_periods}/{len(valid_periods)}")

    if overall["pf"]>=1.3 and profitable_periods>=max(2,len(valid_periods)-1):
        print(f"\n  ✅ EDGE CONFIRMED: PF={overall['pf']} over {overall['executed']} trades,"
              f" {profitable_periods}/{len(valid_periods)} periods profitable")
        print(f"  → Micro Live ready")
    elif overall["pf"]>=1.0:
        print(f"\n  ⚠️  WEAK EDGE: PF={overall['pf']}. Insufficient evidence.")
    else:
        print(f"\n  ❌ NO EDGE: PF={overall['pf']}. 30-day result was noise.")

    print()

if __name__=="__main__":
    import sys
    u = sys.argv[1] if len(sys.argv) > 1 else "11"
    UNIVERSE_NAME = u
    if u == "7":
        SYMBOLS = ["BTCUSDT","ETHUSDT","DOGEUSDT","SOLUSDT","XRPUSDT","ADAUSDT","BNBUSDT"]
    else:
        SYMBOLS = ALL_SYMBOLS
    asyncio.run(main())
