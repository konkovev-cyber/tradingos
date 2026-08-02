#!/usr/bin/env python3
"""
Historical Signal Frequency Audit.
Average interval between accepted signals, by regime and symbol.
Compare expected rate vs current forward age.
"""
from __future__ import annotations

import logging, math, sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

# Reuse the same analysis function — just compute intervals
# We'll load the existing trade data from the previous regime segmentation

# Actually, let's compute fresh from the 90d data
import asyncio
from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator
import tradingos.signals.signal_scoring as ss

THRESHOLD = 0.55
SYMBOLS = ["BTCUSDT","ETHUSDT","DOGEUSDT","SOLUSDT","XRPUSDT","ADAUSDT","BNBUSDT"]
DAYS = 90

_score_holder = {"score": None}
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched


async def main():
    # Use previously fetched data or fetch fresh
    # Try to use cached data from the previous run
    import json
    cache_path = ROOT / "memory" / "historical_trades_cache.json"
    
    if cache_path.exists():
        print("Loading cached historical trades...")
        all_trades = json.loads(cache_path.read_text())
    else:
        print("Fetching 90 days data (no cache found)...")
        import httpx
        data = {}
        for sym in SYMBOLS:
            all_c = []
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            async with httpx.AsyncClient(timeout=15) as client:
                while len(all_c) < DAYS * 24:
                    params = {"category":"linear","symbol":sym,"interval":"60","limit":200}
                    if end_ms: params["end"]=str(end_ms)
                    resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
                    rows = resp.json().get("result",{}).get("list",[])
                    if not rows: break
                    for row in rows:
                        all_c.append(Candle(int(row[0]),float(row[1]),float(row[2]),
                            float(row[3]),float(row[4]),float(row[5]) if row[5] else 0.0,sym,"1h"))
                    end_ms = int(rows[-1][0])-1
                    await asyncio.sleep(0.02)
            all_c.sort(key=lambda x: x.timestamp)
            if len(all_c) > DAYS*24: all_c = all_c[-(DAYS*24):]
            data[sym] = all_c

        # Run signal generator
        all_trades = []
        for sym in SYMBOLS:
            candles = data.get(sym,[])
            if len(candles) < 200: continue
            calc = IndicatorCalculator()
            sg = SignalGenerator()
            closes = [c.close for c in candles]

            for i in range(200, len(candles)):
                w=candles[:i+1]; p=closes[:i+1]
                ema20=calc.ema(p,20); ema50=calc.ema(p,50); ema200=calc.ema(p,200)
                rsi=calc.rsi(p,14); atr=calc.atr(w,14); adx_val=calc.adx(w,14)
                macd=calc.macd(p); bb=calc.bollinger(p); vwap=calc.vwap(w)
                vr=calc.volume_ratio([c.volume for c in w],20)
                c=candles[i]
                fv=FeatureVector(timestamp_ms=c.timestamp, symbol=sym,
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
                if direction is None: continue

                # Evaluate trade
                if direction=="BUY":
                    sl=c.close-atr*2; tp=c.close+atr*2
                else:
                    sl=c.close+atr*2; tp=c.close-atr*2
                outcome="OPEN"; exit_p=c.close
                for j in range(i+1,min(i+168,len(candles))):
                    cc=candles[j]
                    if direction=="BUY":
                        if cc.low<=sl: outcome="SL"; exit_p=sl; break
                        if cc.high>=tp: outcome="TP"; exit_p=tp; break
                    else:
                        if cc.high>=sl: outcome="SL"; exit_p=sl; break
                        if cc.low<=tp: outcome="TP"; exit_p=tp; break
                if outcome=="OPEN":
                    exit_p=candles[min(i+168,len(candles)-1)].close; outcome="EXP"
                risk=abs(c.close-sl)
                r=(exit_p-c.close)/risk if risk>0 else 0 if direction=="BUY" else (c.close-exit_p)/risk if risk>0 else 0

                all_trades.append({
                    "symbol":sym,"direction":direction,"adx":adx_val,"rsi":rsi,
                    "outcome":outcome,"r":round(r,2),
                    "ts":c.timestamp,
                })

        # Cache
        cache_path.write_text(json.dumps(all_trades))

    print(f"Total trades: {len(all_trades)}")
    
    # Sort all trades by timestamp to compute intervals
    all_trades.sort(key=lambda t: t["ts"])
    
    # 1. Global interval
    n = len(all_trades)
    if n >= 2:
        total_hours = (all_trades[-1]["ts"] - all_trades[0]["ts"]) / (3600 * 1000)
        avg_interval_hours = total_hours / (n - 1)
        avg_interval_days = avg_interval_hours / 24
    else:
        total_hours = 0
        avg_interval_hours = 0
        avg_interval_days = 0
    
    print(f"\n{'='*60}")
    print(f"SIGNAL FREQUENCY AUDIT")
    print(f"{'='*60}")
    print(f"\nPeriod: {DAYS} days")
    print(f"Total accepted signals: {n}")
    print(f"Span: {total_hours:.0f}h ({total_hours/24:.1f}d)")
    if n >= 2:
        print(f"Average interval between signals: {avg_interval_hours:.1f}h ({avg_interval_days:.2f}d)")
    
    # 2. Per symbol
    print(f"\n{'='*60}")
    print(f"PER SYMBOL")
    print(f"{'='*60}")
    print(f"\n{'Symbol':9s} {'Trades':>7s} {'Interval':>12s} {'ADX avg':>8s} {'RSI avg':>8s}")
    print(f"{'-'*48}")
    for sym in SYMBOLS:
        sym_trades = [t for t in all_trades if t["symbol"] == sym]
        sym_trades.sort(key=lambda t: t["ts"])
        sn = len(sym_trades)
        if sn >= 2:
            sym_hours = (sym_trades[-1]["ts"] - sym_trades[0]["ts"]) / (3600 * 1000)
            avg_int = sym_hours / (sn - 1)
        else:
            avg_int = float('inf')
        avg_adx = sum(t["adx"] for t in sym_trades) / sn if sn else 0
        avg_rsi = sum(t["rsi"] for t in sym_trades) / sn if sn else 0
        int_str = f"{avg_int:.0f}h" if avg_int != float('inf') else "N/A"
        print(f"{sym:9s} {sn:>7d} {int_str:>12s} {avg_adx:>8.1f} {avg_rsi:>8.1f}")
    
    # 3. Current regime (ADX>=35 & RSI<=40)
    print(f"\n{'='*60}")
    print(f"CURRENT REGIME: ADX>=35 & RSI<=40")
    print(f"{'='*60}")
    regime_trades = [t for t in all_trades if t["adx"] >= 35 and t["rsi"] <= 40]
    regime_trades.sort(key=lambda t: t["ts"])
    rn = len(regime_trades)
    print(f"\nTrades in this regime: {rn}")
    if rn >= 2:
        r_hours = (regime_trades[-1]["ts"] - regime_trades[0]["ts"]) / (3600 * 1000)
        r_days = r_hours / 24
        avg_int_r = r_hours / (rn - 1)
        print(f"Period span: {r_hours:.0f}h ({r_days:.1f}d)")
        print(f"Average interval: {avg_int_r:.0f}h ({avg_int_r/24:.2f}d)")
        print(f"Trades per month (est): {30 / (avg_int_r/24):.1f}")
    elif rn == 1:
        print(f"Single trade — cannot compute interval.")
        print(f"Estimated rate: << 1 per 90 days")
    else:
        print(f"No trades in this regime in historical data.")
        print(f"Forward is exploring unprecedented conditions.")
    
    # 4. How many hours of observation before first signal
    print(f"\n{'='*60}")
    print(f"EXPECTED TIME TO FIRST SIGNAL")
    print(f"{'='*60}")
    
    # Time to first trade overall
    if n >= 1 and total_hours > 0:
        hours_to_first = (all_trades[0]["ts"] - (all_trades[0]["ts"] - (n-1)*avg_interval_hours*3600*1000)) / (3600*1000)
        # Actually, compute directly: time from first possible evaluation to first trade
        # Let's use: 90 days / 31 trades ≈ 1 trade per 3 days ≈ 70 hours
        print(f"\nOverall historical: 1 trade per ~{avg_interval_hours:.0f}h ({avg_interval_days:.1f}d)")
        
    # For current regime specifically
    if rn >= 1:
        # We have data only for 9% of observations
        # Expected: 0.09 * 31 = 2.79 trades from this regime in 90 days
        expected_per_90d = rn  # actual count
        if expected_per_90d > 0:
            hours_per_trade = (DAYS * 24) / expected_per_90d
            print(f"Current regime: 1 trade per ~{hours_per_trade:.0f}h ({hours_per_trade/24:.1f}d)")
            print(f"Expected trades in 90 days: {expected_per_90d}")
    
    print(f"\nForward age: ~3 hours")
    
    # How long until first signal given regime frequency?
    if rn >= 1:
        days_between = (DAYS) / max(rn, 1)
        print(f"\nAt historical rate for this regime: ~{days_between:.0f} days between trades.")
        print(f"Expected first signal: within ~{days_between:.0f} days from start.")
    else:
        print(f"\nNo historical data for this exact regime combination.")
        print(f"Expected first signal: unknown — regime is rare.")
    
    # Normality check
    print(f"\n{'='*60}")
    print(f"NORMALITY CHECK")
    print(f"{'='*60}")
    fwd_hours = 3
    if rn >= 1:
        expected_interval_days = days_between
        expected_interval_hours = expected_interval_days * 24
        if fwd_hours < expected_interval_hours:
            print(f"\nForward age ({fwd_hours}h) < expected interval ({expected_interval_hours:.0f}h)")
            print(f"→ 0 accepted signals is NORMAL for this duration.")
            print(f"→ Expected first signal around ~{expected_interval_hours:.0f}h of forward observation.")
        else:
            print(f"\nForward age ({fwd_hours}h) exceeds expected interval ({expected_interval_hours:.0f}h)")
            print(f"→ 0 accepted signals is SLIGHTLY UNUSUAL.")
            print(f"→ Continue observation — variance expected with small samples.")
    else:
        print(f"\nCannot compute expected interval — no historical trades in exact regime.")
        print("→ 0 accepted signals is NEITHER normal nor abnormal — unknown territory.")

if __name__ == "__main__":
    asyncio.run(main())
