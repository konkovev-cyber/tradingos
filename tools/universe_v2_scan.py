#!/usr/bin/env python3
"""
Universe V2 — scan candidates, test each, qualify.
"""
from __future__ import annotations

import asyncio, json, logging, math, sys
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

# 28 liquid symbols
ALL_SYMBOLS = [
    "BTCUSDT","ETHUSDT","DOGEUSDT","SOLUSDT","XRPUSDT","ADAUSDT","BNBUSDT",
    "SOXLUSDT","HYPEUSDT","BANKUSDT","SNDKUSDT","AKEUSDT","XAUUSDT",
    "COTIUSDT","ZECUSDT","NEARUSDT","DEXEUSDT","1000PEPEUSDT","ONDOUSDT",
    "KAITOUSDT","LAUSDT","CLUSDT","MUUSDT","SPCXUSDT","BEATUSDT",
    "PUMPFUNUSDT","SNXXUSDT","SKHYUSDT","SKHYNIXUSDT",
]

DAYS = 90


async def test_symbol(sym: str, sem: asyncio.Semaphore) -> dict | None:
    """Fetch 90d 1h data, run SignalGenerator, return stats."""
    import httpx
    async with sem:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < DAYS * 24:
                params = {"category":"linear","symbol":sym,"interval":"60","limit":200}
                if end_ms: params["end"]=str(end_ms)
                try:
                    resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                except Exception:
                    break
                rows = resp.json().get("result",{}).get("list",[])
                if not rows: break
                for row in rows:
                    all_c.append(Candle(int(row[0]),float(row[1]),float(row[2]),
                        float(row[3]),float(row[4]),float(row[5]) if row[5] else 0.0,sym,"1h"))
                end_ms = int(rows[-1][0])-1
                await asyncio.sleep(0.02)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > DAYS*24: all_c = all_c[-(DAYS*24):]

    if len(all_c) < 200:
        return {"symbol": sym, "error": f"insufficient data ({len(all_c)} candles)"}

    calc = IndicatorCalculator()
    sg = SignalGenerator()
    closes = [c.close for c in all_c]
    trades = []

    for i in range(200, len(all_c)):
        w=all_c[:i+1]; p=closes[:i+1]
        ema20=calc.ema(p,20); ema50=calc.ema(p,50); ema200=calc.ema(p,200)
        rsi=calc.rsi(p,14); atr=calc.atr(w,14); adx_val=calc.adx(w,14)
        macd=calc.macd(p); bb=calc.bollinger(p); vwap=calc.vwap(w)
        vr=calc.volume_ratio([c.volume for c in w],20)
        c=all_c[i]
        fv=FeatureVector(timestamp_ms=c.timestamp, symbol=sym,
            open=c.open,high=c.high,low=c.low,close=c.close,volume=c.volume,
            ema20=ema20,ema50=ema50,ema200=ema200 or 0.0,rsi=rsi,
            macd_line=macd.get("macd",0.0),macd_signal=macd.get("signal",0.0),atr=atr,
            bb_upper=bb.get("upper",0.0),bb_lower=bb.get("lower",0.0),bb_middle=bb.get("middle",0.0),
            adx=adx_val,volume_ma=0.0,volume_ratio=vr,obv=0.0,vwap=vwap,
            ema_bullish=ema20>ema50 if ema20 and ema50 else False,
            price_above_ema50=c.close>ema50 if ema50 else False,
            rsi_overbought=rsi>70,rsi_oversold=rsi<30,htf_ema50=None,htf_ema200=None,
            htf_trend=None,integrity_score=1.0)
        _score_holder["score"]=None
        direction=sg.decide(sym,fv,bar_idx=i)
        if direction is None: continue

        sl=c.close-atr*2 if direction=="BUY" else c.close+atr*2
        tp=c.close+atr*2 if direction=="BUY" else c.close-atr*2
        outcome="OPEN"; exit_p=c.close
        for j in range(i+1,min(i+168,len(all_c))):
            cc=all_c[j]
            if direction=="BUY":
                if cc.low<=sl: outcome="SL"; exit_p=sl; break
                if cc.high>=tp: outcome="TP"; exit_p=tp; break
            else:
                if cc.high>=sl: outcome="SL"; exit_p=sl; break
                if cc.low<=tp: outcome="TP"; exit_p=tp; break
        if outcome=="OPEN":
            exit_p=all_c[min(i+168,len(all_c)-1)].close; outcome="EXP"
        risk=abs(c.close-sl)
        if direction=="BUY":
            r=(exit_p-c.close)/risk if risk>0 else 0
        else:
            r=(c.close-exit_p)/risk if risk>0 else 0
        trades.append({"outcome":outcome,"r":round(r,2)})

    n=len(trades)
    wins=[t for t in trades if t["outcome"]=="TP"]
    losses=[t for t in trades if t["outcome"]=="SL"]
    nw=len(wins); nl=len(losses)
    wr=nw/n*100 if n else 0
    net_r=sum(t["r"] for t in trades)
    gp=sum(t["r"] for t in wins)
    gl=sum(abs(t["r"]) for t in losses)
    pf=gp/max(gl,0.01)
    avg_r=net_r/n if n else 0

    tier = ""
    if n >= 5 and pf >= 1.5 and wr >= 55: tier = "A"
    elif n >= 5 and pf >= 1.2 and wr >= 45: tier = "B"
    elif n >= 5 and pf < 1.2: tier = "C (low PF)"
    elif n < 5: tier = "C (insufficient)"
    else: tier = "C"

    return {
        "symbol":sym,"candles":len(all_c),"trades":n,"wr":round(wr,1),
        "pf":round(pf,2),"net_r":round(net_r,2),"avg_r":round(avg_r,2),
        "tier":tier
    }


async def main():
    print("="*60)
    print("UNIVERSE V2 — CANDIDATE SCAN")
    print(f"Candidates: {len(ALL_SYMBOLS)} | 90d 1h | threshold=0.55")
    print("="*60)

    sem = asyncio.Semaphore(5)  # max 5 concurrent fetches
    results = []
    for idx, sym in enumerate(ALL_SYMBOLS):
        print(f"\r  [{idx+1}/{len(ALL_SYMBOLS)}] {sym}...", end="", flush=True)
        r = await test_symbol(sym, sem)
        results.append(r)

    print("\n\n" + "="*100)
    print("RESULTS")
    print("="*100)
    hdr = f"{'Symbol':15s} {'Candles':>8s} {'Trades':>7s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s} {'AvgR':>6s} {'Tier':>18s}"
    print(hdr); print("-"*100)

    # Sort: Tier A first, then B, then C
    def sort_key(r):
        if r is None: return ""
        if r.get("error"): return "ZZZZ"+r["symbol"]
        return {"A": "AAAA", "B": "AAAB", "C": "ZZZC"}.get(r.get("tier","C"), "ZZZD")+r["symbol"]

    results.sort(key=sort_key)

    tiers = {"A": [], "B": [], "C": []}
    for r in results:
        if r is None:
            print(f"{'ERROR':15s} — no result")
            continue
        if r.get("error"):
            print(f"{r['symbol']:15s} — {r['error']}")
            continue
        flag = ""
        if r["tier"] == "A": flag = " ✅ TIER A"
        elif r["tier"] == "B": flag = " 📌 TIER B"
        print(f"{r['symbol']:15s} {r['candles']:>8d} {r['trades']:>7d} {r['wr']:>5.1f} {r['pf']:>6.2f} {r['net_r']:>+5.2f} {r['avg_r']:>+5.2f} {r['tier']:>18s}{flag}")
        t = r["tier"][0] if r["tier"][0] in "ABC" else "C"
        tiers[t].append(r["symbol"])

    print("\n" + "="*60)
    print("UNIVERSE V2 — QUALIFIED SYMBOLS")
    print("="*60)
    print(f"\nTier A (PF>=1.5, WR>=55%, trades>=5): {len(tiers['A'])}")
    for s in tiers["A"]: print(f"  ✅ {s}")
    print(f"\nTier B (PF>=1.2, WR>=45%, trades>=5): {len(tiers['B'])}")
    for s in tiers["B"]: print(f"  📌 {s}")
    print(f"\nTier C (not qualified): {sum(len(t) for t in tiers.values()) - len(tiers['A']) - len(tiers['B'])}")
    for t in ["C"]:
        for s in tiers[t]:
            r = next((r for r in results if r and r.get("symbol")==s), None)
            if r:
                print(f"  ⬜ {s} — {r.get('tier','?')} ({r.get('trades',0)} trades, PF={r.get('pf',0):.2f})")

    print(f"\n{'='*60}")
    print(f"UNIVERSE V2 = Universe 7 + qualified Tier A/B")
    v2 = set(tiers["A"] + tiers["B"])
    print(f"Tier A: {len(tiers['A'])}")
    print(f"Tier B: {len(tiers['B'])}")
    print(f"Total V2: {len(v2)} symbols")

    # Expected signal frequency
    if v2:
        # Sum all trades from V2 symbols
        v2_trades = sum(r["trades"] for r in results if r and r.get("symbol") in v2 and not r.get("error"))
        v2_sig_per_day = v2_trades / DAYS
        print(f"Expected signals/day: {v2_sig_per_day:.2f}")
        print(f"Expected first signal: ~{1/max(v2_sig_per_day,0.01):.1f} days")
        print(f"Expected signals/week: ~{v2_sig_per_day*7:.1f}")

    # Save V2 list
    v2_list = sorted(v2)
    Path("/root/tradingos/docs/migration/UNIVERSE_V2_CANDIDATES.json").write_text(json.dumps(v2_list, indent=2))
    print(f"\nSaved: docs/migration/UNIVERSE_V2_CANDIDATES.json")


if __name__ == "__main__":
    asyncio.run(main())
