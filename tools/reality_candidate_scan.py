#!/usr/bin/env python3
"""
Reality Candidate Scanner — find tradeable opportunities for $1 controlled risk.
Research track:  threshold 0.55 (unchanged)
Reality track:   prob >= 0.50, historical PF >= 1.5, WR >= 55%, SL/TP mandatory.

Usage:
    python3 tools/reality_candidate_scan.py
    python3 tools/reality_candidate_scan.py --all     # scan all liquid USDT pairs
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

THRESHOLD_RESEARCH = 0.55
THRESHOLD_REALITY = 0.50
_score_holder = {"score": None}
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched

# Historical PF/WR database (from prior full backtests)
HISTORICAL = {
    "BTCUSDT": {"pf": 1.50, "wr": 60.0, "trades": 5},
    "ETHUSDT": {"pf": 2.00, "wr": 66.7, "trades": 6},
    "DOGEUSDT": {"pf": 2.00, "wr": 66.7, "trades": 3},
    "SOLUSDT": {"pf": 2.00, "wr": 66.7, "trades": 6},
    "XRPUSDT": {"pf": 2.00, "wr": 66.7, "trades": 3},
    "ADAUSDT": {"pf": 5.00, "wr": 83.3, "trades": 6},
    "BNBUSDT": {"pf": 200.0, "wr": 100.0, "trades": 2},
    "BEATUSDT": {"pf": 1.50, "wr": 60.0, "trades": 10},
    "COTIUSDT": {"pf": 5.00, "wr": 83.3, "trades": 6},
    "KAITOUSDT": {"pf": 7.00, "wr": 87.5, "trades": 8},
    "LAUSDT": {"pf": 1.67, "wr": 62.5, "trades": 16},
    "AKEUSDT": {"pf": 0.38, "wr": 27.3, "trades": 11},
    "MUUSDT": {"pf": 0.50, "wr": 33.3, "trades": 9},
    "1000PEPEUSDT": {"pf": 2.00, "wr": 66.7, "trades": 3},
    "ONDOUSDT": {"pf": 0.29, "wr": 22.2, "trades": 9},
    "NEARUSDT": {"pf": 0.40, "wr": 28.6, "trades": 7},
    "ZECUSDT": {"pf": 0.75, "wr": 42.9, "trades": 7},
    "BANKUSDT": {"pf": 0.50, "wr": 33.3, "trades": 9},
    "HYPEUSDT": {"pf": 0.57, "wr": 36.4, "trades": 11},
    "SNDKUSDT": {"pf": 0.75, "wr": 42.9, "trades": 14},
    "DEXEUSDT": {"pf": 0.57, "wr": 36.4, "trades": 11},
    "SKHYNIXUSDT": {"pf": 0.43, "wr": 30.0, "trades": 10},
    "PUMPFUNUSDT": {"pf": 0.83, "wr": 45.5, "trades": 11},
    "CLUSDT": {"pf": 1.00, "wr": 50.0, "trades": 6},
    "SOXLUSDT": {"pf": 0.50, "wr": 33.3, "trades": 9},
}


async def get_current_data(sym: str, sem: asyncio.Semaphore) -> dict:
    """Fetch 200 1h candles, compute current probability and features."""
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
        i = len(all_c) - 1
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
    import argparse
    parser = argparse.ArgumentParser(description="Reality Candidate Scanner")
    parser.add_argument("--all", action="store_true", help="Scan all liquid USDT pairs")
    args = parser.parse_args()

    global sg
    sg = SignalGenerator()

    # Determine symbols to scan
    if args.all:
        import httpx
        resp = await httpx.AsyncClient(timeout=30).get(
            "https://api.bybit.com/v5/market/tickers?category=linear")
        tickers = resp.json().get("result",{}).get("list",[])
        symbols = []
        for t in tickers:
            sym = t.get("symbol","")
            if not sym.endswith("USDT"): continue
            try:
                vol = float(t.get("turnover24h",0))
            except: continue
            if vol >= 20_000_000:
                symbols.append(sym)
        symbols = sorted(set(symbols))
    else:
        symbols = list(HISTORICAL.keys())

    print(f"{'='*70}")
    print(f"REALITY CANDIDATE SCANNER")
    print(f"{'='*70}")
    print(f"\nScanning {len(symbols)} symbols...\n")

    sem = asyncio.Semaphore(5)
    results = []
    for i, sym in enumerate(symbols):
        print(f"\r  [{i+1}/{len(symbols)}] {sym}...", end="", flush=True)
        r = await get_current_data(sym, sem)
        results.append(r)

    # Evaluate each candidate against reality criteria
    print("\n\n" + "="*70)
    print("REALITY CANDIDATE EVALUATION")
    print("Rules: prob >= 0.50, PF >= 1.5, WR >= 55%, trades >= 5")
    print("="*70)

    candidates = []
    for r in results:
        sym = r["symbol"]
        if r.get("error"):
            continue
        prob = r.get("prob", 0)
        hist = HISTORICAL.get(sym, {})
        pf = hist.get("pf", 0)
        wr = hist.get("wr", 0)
        trades = hist.get("trades", 0)

        if prob >= THRESHOLD_REALITY and pf >= 1.5 and wr >= 55 and trades >= 5:
            candidates.append(r)
            candidates[-1]["pf"] = pf
            candidates[-1]["wr"] = wr

    if candidates:
        candidates.sort(key=lambda x: -x["prob"])
        print(f"\n✅ TRADE CANDIDATE FOUND — {len(candidates)} pair(s) qualify\n")
        for c in candidates:
            print(f"  Symbol:         {c['symbol']}")
            print(f"  Probability:    {c['prob']:.4f} (threshold: {THRESHOLD_REALITY})")
            print(f"  Historical PF:  {c['pf']:.2f}")
            print(f"  Historical WR:  {c['wr']:.1f}%")
            print(f"  ADX:            {c['adx']:.1f}")
            print(f"  RSI:            {c['rsi']:.1f}")
            print(f"  Direction:      {c.get('direction','?')}")
            print(f"  Guardian:       REQUIRED")
            print(f"  Approval:       MANUAL")
            print(f"  Risk:           $1 max loss")
            print()
    else:
        print(f"\n❌ NO VALID CANDIDATE")
        print()
        # Show closest to qualifying
        near = []
        for r in results:
            sym = r["symbol"]
            if r.get("error"): continue
            prob = r.get("prob", 0)
            hist = HISTORICAL.get(sym, {})
            pf = hist.get("pf", 0)
            wr = hist.get("wr", 0)
            reasons = []
            if prob < THRESHOLD_REALITY: reasons.append(f"prob={prob:.3f}<0.50")
            if pf < 1.5: reasons.append(f"PF={pf:.2f}<1.5")
            if wr < 55: reasons.append(f"WR={wr:.1f}<55%")
            near.append({"symbol":sym,"prob":prob,"pf":pf,"wr":wr,"reasons":reasons})
        near.sort(key=lambda x: -x["prob"])
        print("Closest to qualifying:")
        print(f"  {'Symbol':14s} {'Prob':>6s} {'PF':>5s} {'WR':>5s} {'Misses'}")
        print(f"  {'-'*50}")
        for n in near[:5]:
            r_str = ", ".join(n["reasons"]) if n["reasons"] else "PASS"
            print(f"  {n['symbol']:14s} {n['prob']:.3f} {n['pf']:>5.2f} {n['wr']:>5.1f} {r_str}")

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Symbols scanned:    {len(results)}")
    print(f"  Research threshold: 0.55 (unchanged)")
    print(f"  Reality threshold:  0.50")
    print(f"  Candidates found:   {len(candidates)}")
    if candidates:
        print(f"  Recommend: TRADE (manual approval)")
    else:
        print(f"  Recommend: WAIT — no pair meets all criteria today")
    print()

    # Save report
    report = {
        "time": datetime.now(timezone.utc).isoformat(),
        "symbols_scanned": len(results),
        "candidates": candidates,
        "decision": "TRADE" if candidates else "WAIT",
    }
    report_path = ROOT / "docs" / "reports" / "REALITY_CANDIDATE_REPORT.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"  Report saved: {report_path}")

if __name__ == "__main__":
    asyncio.run(main())
