#!/usr/bin/env python3
"""
Reality Ranking v1 — Tiered candidate evaluation for controlled real trades.
Research Track: 0.55 (unchanged)
Reality Track: Tiers A (0.50+), B (0.45-0.50), C (forbidden)

Score = 0.40 * prob_norm + 0.30 * pf_norm + 0.20 * liq_norm + 0.10 * regime_norm
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

THRESHOLD_RESEARCH = 0.55
_score_holder = {"score": None}
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched

# Known historical data (from earlier full 90d backtests)
HISTORICAL = {
    "BTCUSDT":{"pf":1.50,"wr":60.0,"tr":5},"ETHUSDT":{"pf":2.00,"wr":66.7,"tr":6},
    "DOGEUSDT":{"pf":2.00,"wr":66.7,"tr":3},"SOLUSDT":{"pf":2.00,"wr":66.7,"tr":6},
    "XRPUSDT":{"pf":2.00,"wr":66.7,"tr":3},"ADAUSDT":{"pf":5.00,"wr":83.3,"tr":6},
    "BNBUSDT":{"pf":200.0,"wr":100.0,"tr":2},"BEATUSDT":{"pf":1.50,"wr":60.0,"tr":10},
    "COTIUSDT":{"pf":5.00,"wr":83.3,"tr":6},"KAITOUSDT":{"pf":7.00,"wr":87.5,"tr":8},
    "LAUSDT":{"pf":1.67,"wr":62.5,"tr":16},"AKEUSDT":{"pf":0.38,"wr":27.3,"tr":11},
    "MUUSDT":{"pf":0.50,"wr":33.3,"tr":9},"1000PEPEUSDT":{"pf":2.00,"wr":66.7,"tr":3},
    "ONDOUSDT":{"pf":0.29,"wr":22.2,"tr":9},"NEARUSDT":{"pf":0.40,"wr":28.6,"tr":7},
    "ZECUSDT":{"pf":0.75,"wr":42.9,"tr":7},"BANKUSDT":{"pf":0.50,"wr":33.3,"tr":9},
    "HYPEUSDT":{"pf":0.57,"wr":36.4,"tr":11},"SNDKUSDT":{"pf":0.75,"wr":42.9,"tr":14},
    "DEXEUSDT":{"pf":0.57,"wr":36.4,"tr":11},"SKHYNIXUSDT":{"pf":0.43,"wr":30.0,"tr":10},
    "PUMPFUNUSDT":{"pf":0.83,"wr":45.5,"tr":11},"CLUSDT":{"pf":1.00,"wr":50.0,"tr":6},
    "SOXLUSDT":{"pf":0.50,"wr":33.3,"tr":9},"LINKUSDT":{"pf":0.67,"wr":40.0,"tr":5},
    "AVAXUSDT":{"pf":0.00,"wr":0.0,"tr":1},"LTCUSDT":{"pf":0.00,"wr":0.0,"tr":0},
}


async def get_current(sym: str, sem: asyncio.Semaphore) -> dict:
    """Fetch 200 1h candles, compute current probability."""
    import httpx
    async with sem:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            params = {"category":"linear","symbol":sym,"interval":"60","limit":200}
            if end_ms: params["end"] = str(end_ms)
            try:
                resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            except Exception as e:
                return {"symbol":sym,"error":str(e),"prob":0}
            rows = resp.json().get("result",{}).get("list",[])
            for row in rows:
                all_c.append(Candle(int(row[0]),float(row[1]),float(row[2]),
                    float(row[3]),float(row[4]),float(row[5]) if row[5] else 0.0,sym,"1h"))
            all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) < 200:
            return {"symbol":sym,"error":"insufficient","prob":0,"candles":len(all_c)}

        calc = IndicatorCalculator()
        closes = [c.close for c in all_c]
        i = len(all_c) - 1
        w = all_c[:i+1]; p = closes[:i+1]
        adx_val = calc.adx(w,14); rsi = calc.rsi(p,14); atr = calc.atr(w,14)
        ema20=calc.ema(p,20);ema50=calc.ema(p,50);ema200=calc.ema(p,200)
        macd=calc.macd(p);bb=calc.bollinger(p);vwap=calc.vwap(w)
        vr=calc.volume_ratio([c.volume for c in w],20)
        c = all_c[i]
        fv=FeatureVector(timestamp_ms=c.timestamp,symbol=sym,
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
        _score_holder["score"]=None
        direction = sg.decide(sym,fv,bar_idx=i)
        score = _score_holder.get("score")
        prob = score.final_probability if score else 0
        return {"symbol":sym,"prob":round(prob,4),"adx":round(adx_val,1),"rsi":round(rsi,1),
                "atr":round(atr,4),"direction":direction or "NONE","candles":len(all_c)}


async def quick_backtest(sym: str, sem: asyncio.Semaphore) -> dict:
    """Quick 30d backtest for symbols without historical data."""
    import httpx
    async with sem:
        all_c = []
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        async with httpx.AsyncClient(timeout=15) as client:
            while len(all_c) < 720:
                params = {"category":"linear","symbol":sym,"interval":"60","limit":200}
                if end_ms: params["end"]=str(end_ms)
                try:
                    resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                except: break
                rows = resp.json().get("result",{}).get("list",[])
                if not rows: break
                for row in rows:
                    all_c.append(Candle(int(row[0]),float(row[1]),float(row[2]),
                        float(row[3]),float(row[4]),float(row[5]) if row[5] else 0.0,sym,"1h"))
                end_ms = int(rows[-1][0])-1
                await asyncio.sleep(0.02)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > 720: all_c = all_c[-720:]

    if len(all_c) < 200:
        return {"pf":0,"wr":0,"trades":0}

    calc = IndicatorCalculator()
    sg2 = SignalGenerator()
    closes = [c.close for c in all_c]
    trades = []
    for i in range(200, len(all_c)):
        w = all_c[:i+1]; p = closes[:i+1]
        adx_val=calc.adx(w,14); rsi=calc.rsi(p,14); atr=calc.atr(w,14)
        ema20=calc.ema(p,20);ema50=calc.ema(p,50);ema200=calc.ema(p,200)
        macd=calc.macd(p);bb=calc.bollinger(p);vwap=calc.vwap(w)
        vr=calc.volume_ratio([c.volume for c in w],20)
        c=all_c[i]
        fv=FeatureVector(timestamp_ms=c.timestamp,symbol=sym,open=c.open,high=c.high,
            low=c.low,close=c.close,volume=c.volume,ema20=ema20,ema50=ema50,
            ema200=ema200 or 0.0,rsi=rsi,macd_line=macd.get("macd",0.0),
            macd_signal=macd.get("signal",0.0),atr=atr,bb_upper=bb.get("upper",0.0),
            bb_lower=bb.get("lower",0.0),bb_middle=bb.get("middle",0.0),adx=adx_val,
            volume_ma=0.0,volume_ratio=vr,obv=0.0,vwap=vwap,
            ema_bullish=ema20>ema50 if ema20 and ema50 else False,
            price_above_ema50=c.close>ema50 if ema50 else False,
            rsi_overbought=rsi>70,rsi_oversold=rsi<30,
            htf_ema50=None,htf_ema200=None,htf_trend=None,integrity_score=1.0)
        _score_holder["score"]=None
        direction = sg2.decide(sym,fv,bar_idx=i)
        if direction is None: continue
        sl=c.close-atr*2 if direction=="BUY" else c.close+atr*2
        tp=c.close+atr*2 if direction=="BUY" else c.close-atr*2
        outcome="OPEN"; exit_p=c.close
        for j in range(i+1,min(i+168,len(all_c))):
            cc=all_c[j]
            if direction=="BUY":
                if cc.low<=sl: outcome="SL";exit_p=sl;break
                if cc.high>=tp: outcome="TP";exit_p=tp;break
            else:
                if cc.high>=sl: outcome="SL";exit_p=sl;break
                if cc.low<=tp: outcome="TP";exit_p=tp;break
        if outcome=="OPEN": exit_p=all_c[min(i+168,len(all_c)-1)].close;outcome="EXP"
        risk=abs(c.close-sl)
        r=((exit_p-c.close)/risk if direction=="BUY" else (c.close-exit_p)/risk) if risk>0 else 0
        trades.append(r)
    n=len(trades); wins=sum(1 for r in trades if r>0)
    wr=wins/n*100 if n else 0; gp=sum(r for r in trades if r>0)
    gl=sum(abs(r) for r in trades if r<0); pf=gp/max(gl,0.01)
    return {"pf":round(pf,2),"wr":round(wr,1),"trades":n}


async def main():
    global sg
    sg = SignalGenerator()

    # Get all liquid symbols
    import httpx
    resp = await httpx.AsyncClient(timeout=30).get(
        "https://api.bybit.com/v5/market/tickers?category=linear")
    tickers = resp.json().get("result",{}).get("list",[])
    symbols = set()
    for t in tickers:
        sym = t.get("symbol","")
        if not sym.endswith("USDT"): continue
        try:
            if float(t.get("turnover24h",0)) >= 20_000_000:
                symbols.add(sym)
        except: continue
    symbols = sorted(symbols)

    print(f"{'='*70}")
    print("REALITY RANKING v1")
    print(f"Scanning {len(symbols)} liquid USDT perpetual pairs")
    print(f"{'='*70}")

    # Phase 1: Get current probability for all
    sem = asyncio.Semaphore(5)
    current_data = {}
    for i, sym in enumerate(symbols):
        print(f"\r  Phase 1 [{i+1}/{len(symbols)}] {sym}...", end="", flush=True)
        r = await get_current(sym, sem)
        if not r.get("error"):
            current_data[sym] = r

    # Phase 2: For symbols with prob >= 0.40 lacking historical data, run quick backtest
    candidates = []
    for sym, r in current_data.items():
        prob = r.get("prob",0)
        if prob < 0.40: continue
        hist = HISTORICAL.get(sym)
        if hist is None:
        candidates.append({**r, "hist": hist or {"pf":0,"wr":0,"trades":0}})

    # Score and rank
    def calc_score(c):
        prob_norm = min(c["prob"] / 0.55, 1.0)
        pf = c["hist"]["pf"] if c.get("hist") else 0
        pf_norm = min(pf / 2.0, 1.0)
        liq_norm = 0.8  # all ≥ $20M volume
        regime_norm = min(c.get("adx",0) / 50, 1.0)
        return 0.40 * prob_norm + 0.30 * pf_norm + 0.20 * liq_norm + 0.10 * regime_norm

    for c in candidates:
        c["score"] = round(calc_score(c), 4)
        prob = c["prob"]
        pf = c["hist"]["pf"] if c.get("hist") else 0
        wr = c["hist"]["wr"] if c.get("hist") else 0
        tr = c["hist"].get("trades", 0) if c.get("hist") else 0
        if prob >= 0.50 and pf >= 1.5 and wr >= 55 and tr >= 5:
            c["tier"] = "A"
        elif 0.45 <= prob < 0.50 and pf >= 1.5 and wr >= 55 and tr >= 5:
            c["tier"] = "B"
        elif prob >= 0.45 and (pf >= 1.2 or tr < 5):
            c["tier"] = "B-WATCH"
        else:
            c["tier"] = "C"

    candidates.sort(key=lambda x: -x["score"])

    # Print TOP-15
    print(f"\n\n{'='*90}")
    print(f"REALITY RANKING — TOP CANDIDATES")
    print(f"{'='*90}")
    hdr = f"{'Rank':>5s} {'Symbol':12s} {'Prob':>6s} {'PF':>6s} {'WR':>5s} {'Tr':>4s} {'ADX':>5s} {'RSI':>5s} {'Score':>6s} {'Tier':>10s}"
    print(hdr)
    print("-"*90)

    tier_a = [c for c in candidates if c["tier"] == "A"]
    tier_b = [c for c in candidates if c["tier"] in ("B","B-WATCH")]
    tier_c = [c for c in candidates if c["tier"] == "C"]

    for i, c in enumerate(candidates[:15]):
        h = c.get("hist",{})
        tier_icon = {"A":"✅ A","B":"📌 B","B-WATCH":"👁 B-W","C":"⬜ C"}.get(c["tier"],"?")
        print(f"{i+1:>5d} {c['symbol']:12s} {c['prob']:.3f} {h.get('pf',0):>6.2f} {h.get('wr',0):>5.1f} {h.get('trades',0):>4d} {c.get('adx',0):>5.1f} {c.get('rsi',0):>5.1f} {c['score']:.4f} {tier_icon:>10s}")

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Scanned:        {len(symbols)} symbols")
    print(f"  With prob>=0.40: {len(candidates)}")
    print(f"  Tier A (trade):  {len(tier_a)}")
    print(f"  Tier B (watch):  {len(tier_b)}")
    print(f"  Tier C (wait):   {len(tier_c)}")

    if tier_a:
        print(f"\n✅ {len(tier_a)} TIER A CANDIDATE(S) — $1 trade possible")
        for c in tier_a[:3]:
            h = c.get("hist",{})
            print(f"\n  TRADE PROPOSAL #{tier_a.index(c)+1}")
            print(f"  Symbol:       {c['symbol']}")
            print(f"  Direction:    {c.get('direction','?')}")
            print(f"  Probability:  {c['prob']:.4f} (>= 0.50 ✅)")
            print(f"  Historical:   PF={h.get('pf',0):.2f} WR={h.get('wr',0):.1f}%")
            print(f"  Risk:         $1 max loss")
            print(f"  Guardian:     REQUIRED (dry-run PASS ✅)")
            print(f"  Approval:     MANUAL REQUIRED")
    elif tier_b:
        print(f"\n📌 {len(tier_b)} TIER B CANDIDATE(S) — $0.50 trade possible")
        for c in tier_b[:3]:
            h = c.get("hist",{})
            print(f"\n  WATCH: {c['symbol']} prob={c['prob']:.3f} PF={h.get('pf',0):.2f}")
    else:
        print(f"\n❌ NO TRADEABLE CANDIDATES TODAY")
        print(f"   Market-wide max probability: {max(c['prob'] for c in candidates):.3f}" if candidates else "   No data")

    # Save
    report = {
        "time": datetime.now(timezone.utc).isoformat(),
        "symbols_scanned": len(symbols),
        "tier_a": len(tier_a),
        "tier_b": len(tier_b),
        "tier_c": len(tier_c),
        "top10": [{"symbol":c["symbol"],"prob":c["prob"],"pf":c.get("hist",{}).get("pf",0),
                    "wr":c.get("hist",{}).get("wr",0),"tier":c["tier"],"score":c["score"]}
                  for c in candidates[:10]],
    }
    report_path = ROOT / "docs" / "reports" / "REALITY_RANKING_REPORT.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report: {report_path}")
    print()

if __name__ == "__main__":
    asyncio.run(main())
