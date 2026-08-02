#!/usr/bin/env python3
"""
Reality Scanner Loop v1 — continuous search for first real trade.
Runs alongside observation. Checks all liquid USDT pairs each cycle.
When candidate meets Tier conditions with direction → trade proposal.
"""
from __future__ import annotations

import asyncio, json, logging, sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("reality_scan")

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

# Historical PF/WR database
HIST = {
    "BTCUSDT":{"pf":1.50,"wr":60.0,"tr":5},"ETHUSDT":{"pf":2.00,"wr":66.7,"tr":6},
    "DOGEUSDT":{"pf":2.00,"wr":66.7,"tr":3},"SOLUSDT":{"pf":2.00,"wr":66.7,"tr":6},
    "XRPUSDT":{"pf":2.00,"wr":66.7,"tr":3},"ADAUSDT":{"pf":5.00,"wr":83.3,"tr":6},
    "BNBUSDT":{"pf":200.0,"wr":100.0,"tr":2},"BEATUSDT":{"pf":1.50,"wr":60.0,"tr":10},
    "COTIUSDT":{"pf":5.00,"wr":83.3,"tr":6},"KAITOUSDT":{"pf":7.00,"wr":87.5,"tr":8},
    "LAUSDT":{"pf":1.67,"wr":62.5,"tr":16},"AKEUSDT":{"pf":0.38,"wr":27.3,"tr":11},
    "1000PEPEUSDT":{"pf":2.00,"wr":66.7,"tr":3},
}

PROPOSAL_PATH = Path("/root/tradingos/memory/reality_trade_proposal.json")
PROCESSED_PATH = Path("/root/tradingos/memory/reality_scanned.jsonl")
AMOUNT = 10  # $10 balance


def tier(prob, pf, wr, tr, direction):
    if direction not in ("BUY", "SELL"):
        return "NO_DIRECTION"
    if prob >= 0.50 and pf >= 1.5 and wr >= 55 and tr >= 5:
        return "A"
    if prob >= 0.45 and pf >= 1.2 and wr >= 45 and tr >= 3:
        return "B"
    return "C"


async def scan_once(sg: SignalGenerator) -> list[dict]:
    """One scan cycle: fetch all liquid symbols → evaluate → return candidates."""
    import httpx
    sem = asyncio.Semaphore(5)
    # Get liquid symbols first
    resp = await httpx.AsyncClient(timeout=30).get(
        "https://api.bybit.com/v5/market/tickers?category=linear")
    tickers = resp.json().get("result", {}).get("list", [])
    liquid = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"): continue
        try:
            if float(t.get("turnover24h", 0)) >= 20_000_000:
                liquid.append(sym)
        except: continue

    results = []
    async def check(sym):
        async with sem:
            try:
                all_c = []
                end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                async with httpx.AsyncClient(timeout=15) as client:
                    params = {"category":"linear","symbol":sym,"interval":"60","limit":200}
                    if end_ms: params["end"] = str(end_ms)
                    resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                    rows = resp.json().get("result",{}).get("list",[])
                    for row in rows:
                        all_c.append(Candle(int(row[0]),float(row[1]),float(row[2]),
                            float(row[3]),float(row[4]),float(row[5]) if row[5] else 0.0,sym,"1h"))
                    all_c.sort(key=lambda x: x.timestamp)
                if len(all_c) < 200:
                    return
                calc = IndicatorCalculator()
                closes = [c.close for c in all_c]
                i = len(all_c) - 1
                w = all_c[:i+1]; p = closes[:i+1]
                adx_val=calc.adx(w,14); rsi=calc.rsi(p,14); atr=calc.atr(w,14)
                ema20=calc.ema(p,20);ema50=calc.ema(p,50);ema200=calc.ema(p,200)
                macd=calc.macd(p);bb=calc.bollinger(p);vwap=calc.vwap(w)
                vr=calc.volume_ratio([c.volume for c in w],20)
                c=all_c[i]
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
                direction=sg.decide(sym,fv,bar_idx=i)
                score=_score_holder.get("score")
                prob=score.final_probability if score else 0
                results.append({
                    "symbol":sym,"prob":round(prob,4),"direction":direction or "NONE",
                    "adx":round(adx_val,1),"rsi":round(rsi,1),
                    "atr":round(atr,4),"candles":len(all_c),
                    **HIST.get(sym,{"pf":0,"wr":0,"tr":0}),
                })
            except Exception as e:
                pass

    tasks = [check(sym) for sym in liquid]
    await asyncio.gather(*tasks)

    # Classify and sort
    for r in results:
        r["tier"] = tier(r["prob"], r.get("pf",0), r.get("wr",0), r.get("tr",0), r["direction"])

    results.sort(key=lambda x: -x["prob"])
    return results


def make_proposal(candidate: dict) -> dict:
    """Create trade proposal JSON."""
    has_hist = candidate.get("pf", 0) > 0
    atr = candidate.get("atr", 0)
    entry = candidate.get("entry", 0) or 0
    direction = candidate["direction"]

    # SL/TP based on ATR (same logic as backtest: 2 ATR)
    if direction == "BUY":
        sl = entry - atr * 2 if atr > 0 else entry * 0.99
        tp = entry + atr * 2 if atr > 0 else entry * 1.01
    else:
        sl = entry + atr * 2 if atr > 0 else entry * 1.01
        tp = entry - atr * 2 if atr > 0 else entry * 0.99

    risk_pct = abs(entry - sl) / entry * 100 if entry else 0
    risk_usdt = AMOUNT * risk_pct / 100

    proposal = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": candidate["symbol"],
        "direction": direction,
        "probability": candidate["prob"],
        "historical_pf": candidate.get("pf", 0),
        "historical_wr": candidate.get("wr", 0),
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp": round(tp, 6),
        "risk_pct": round(risk_pct, 2),
        "risk_usdt": round(risk_usdt, 2),
        "tier": candidate["tier"],
        "guardian_status": "REQUIRED",
        "approval": "MANUAL",
        "adx": candidate.get("adx", 0),
        "rsi": candidate.get("rsi", 0),
    }
    return proposal


async def main_loop(interval: int = 300):
    """Main loop: scan → evaluate → propose."""
    sg = SignalGenerator()
    log.info("Reality Scanner Loop started (interval=%ds)", interval)

    while True:
        try:
            log.info("Scanning market...")
            candidates = await scan_once(sg)

            # Find best actionable candidate
            actionable = [c for c in candidates if c["tier"] in ("A", "B")]
            if actionable:
                best = actionable[0]
                log.info("CANDIDATE FOUND: %s %s prob=%.3f tier=%s",
                         best["symbol"], best["direction"], best["prob"], best["tier"])

                proposal = make_proposal(best)
                PROPOSAL_PATH.parent.mkdir(parents=True, exist_ok=True)
                PROPOSAL_PATH.write_text(json.dumps(proposal, indent=2))

                # Log to processed
                with open(PROCESSED_PATH, "a") as f:
                    f.write(json.dumps({"time": proposal["timestamp"],
                                        "symbol": best["symbol"],
                                        "direction": best["direction"],
                                        "prob": best["prob"],
                                        "tier": best["tier"]}) + "\n")

                print("\n" + "=" * 60)
                print("REALITY TRADE PROPOSAL")
                print("=" * 60)
                print(f"  Symbol:       {best['symbol']}")
                print(f"  Direction:    {best['direction']}")
                print(f"  Probability:  {best['prob']:.4f}")
                print(f"  Tier:         {best['tier']}")
                print(f"  Historical:   PF={best.get('pf',0):.2f} WR={best.get('wr',0):.1f}%")
                print(f"  Entry:        {proposal['entry']:.6f}")
                print(f"  SL:           {proposal['sl']:.6f}")
                print(f"  TP:           {proposal['tp']:.6f}")
                print(f"  Risk:         ${proposal['risk_usdt']:.2f}")
                print(f"  Guardian:     {proposal['guardian_status']}")
                print(f"  Approval:     {proposal['approval']}")
                print(f"\n  Manual approval required.")
                print()
            else:
                # Show top-3 non-actionable
                top = candidates[:3]
                log.info("No candidates. Top: %s (%.3f, %s), %s (%.3f, %s), %s (%.3f, %s)",
                         top[0]["symbol"], top[0]["prob"], top[0]["direction"],
                         top[1]["symbol"] if len(top)>1 else "","","",
                         top[2]["symbol"] if len(top)>2 else "","","")

        except Exception as e:
            log.error("Scan error: %s", e)

        await asyncio.sleep(interval)


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=300, help="Scan interval in seconds")
    args = parser.parse_args()
    await main_loop(interval=args.interval)


if __name__ == "__main__":
    asyncio.run(main())
