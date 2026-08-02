#!/usr/bin/env python3
"""
Historical Regime Segmentation Audit.
90d backtest → filter by ADX/RSI regimes → PF per regime → compare with forward.
"""
from __future__ import annotations

import asyncio, logging, math, sys
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
SYMBOLS = ["BTCUSDT","ETHUSDT","DOGEUSDT","SOLUSDT","XRPUSDT","ADAUSDT","BNBUSDT"]
DAYS = 90

_score_holder = {"score": None}
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched


async def fetch_all() -> dict[str, list[Candle]]:
    import httpx
    data = {}
    for sym in SYMBOLS:
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
                    all_c.append(Candle(int(row[0]), float(row[1]), float(row[2]),
                        float(row[3]), float(row[4]), float(row[5]) if row[5] else 0.0, sym, "1h"))
                end_ms = int(rows[-1][0]) - 1
                await asyncio.sleep(0.02)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > DAYS * 24: all_c = all_c[-(DAYS*24):]
        data[sym] = all_c
    return data


def run_and_segment(data: dict[str, list[Candle]]) -> list[dict]:
    """Run SignalGenerator on all symbols, return list of all trades with features."""
    all_trades = []
    for sym in SYMBOLS:
        candles = data.get(sym, [])
        if len(candles) < 200: continue
        calc = IndicatorCalculator()
        sg = SignalGenerator()
        closes = [c.close for c in candles]

        for i in range(200, len(candles)):
            w = candles[:i+1]; p = closes[:i+1]
            ema20=calc.ema(p,20); ema50=calc.ema(p,50); ema200=calc.ema(p,200)
            rsi=calc.rsi(p,14); atr=calc.atr(w,14); adx_val=calc.adx(w,14)
            macd=calc.macd(p); bb=calc.bollinger(p); vwap=calc.vwap(w)
            vr=calc.volume_ratio([c.volume for c in w],20)
            c=candles[i]
            fv=FeatureVector(timestamp_ms=c.timestamp, symbol=sym,
                open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
                ema20=ema20, ema50=ema50, ema200=ema200 or 0.0, rsi=rsi,
                macd_line=macd.get("macd",0.0), macd_signal=macd.get("signal",0.0),
                atr=atr, bb_upper=bb.get("upper",0.0), bb_lower=bb.get("lower",0.0),
                bb_middle=bb.get("middle",0.0), adx=adx_val,
                volume_ma=0.0, volume_ratio=vr, obv=0.0, vwap=vwap,
                ema_bullish=ema20>ema50 if ema20 and ema50 else False,
                price_above_ema50=c.close>ema50 if ema50 else False,
                rsi_overbought=rsi>70, rsi_oversold=rsi<30,
                htf_ema50=None, htf_ema200=None, htf_trend=None, integrity_score=1.0)

            _score_holder["score"] = None
            direction = sg.decide(sym, fv, bar_idx=i)
            if direction is None: continue

            # Accepted signal → evaluate trade
            if direction == "BUY":
                sl = c.close - atr * 2
                tp = c.close + atr * 2
            else:
                sl = c.close + atr * 2
                tp = c.close - atr * 2

            outcome = "OPEN"; exit_p = c.close
            for j in range(i+1, min(i+168, len(candles))):
                cc = candles[j]
                if direction == "BUY":
                    if cc.low <= sl: outcome="SL"; exit_p=sl; break
                    if cc.high >= tp: outcome="TP"; exit_p=tp; break
                else:
                    if cc.high >= sl: outcome="SL"; exit_p=sl; break
                    if cc.low <= tp: outcome="TP"; exit_p=tp; break
            if outcome == "OPEN":
                exit_p = candles[min(i+168, len(candles)-1)].close
                outcome = "EXP"
            risk = abs(c.close - sl)
            if direction == "BUY":
                r = (exit_p-c.close)/risk if risk>0 else 0
            else:
                r = (c.close-exit_p)/risk if risk>0 else 0

            all_trades.append({
                "symbol": sym, "direction": direction,
                "adx": adx_val, "rsi": rsi, "atr": atr,
                "prob": _score_holder.get("score", None).final_probability if _score_holder.get("score") else 0,
                "outcome": outcome, "r": round(r, 2),
                "regime": "TRENDING" if adx_val >= 25 else ("WEAK" if adx_val >= 20 else "FLAT"),
            })
    return all_trades


def compute_stats(trades: list[dict], label: str = "") -> dict:
    n = len(trades)
    wins = [t for t in trades if t["outcome"] == "TP"]
    losses = [t for t in trades if t["outcome"] == "SL"]
    nw = len(wins); nl = len(losses)
    wr = nw/n*100 if n else 0
    net_r = sum(t["r"] for t in trades)
    avg_r = net_r/n if n else 0
    gp = sum(t["r"] for t in wins)
    gl = sum(abs(t["r"]) for t in losses)
    pf = gp / max(gl, 0.01)
    dd = 0; peak = 0; cum = 0
    for t in trades: cum += t["r"]; peak = max(peak, cum); dd = min(dd, cum-peak)
    return {"label": label, "n": n, "trades": n, "wins": nw, "losses": nl,
            "wr": round(wr, 1), "pf": round(pf, 2), "net_r": round(net_r, 2),
            "avg_r": round(avg_r, 2), "max_dd": round(dd, 2)}


async def main():
    print("=" * 70)
    print("HISTORICAL REGIME SEGMENTATION AUDIT")
    print("=" * 70)

    print("\nFetching 90 days data...")
    data = await fetch_all()
    print(f"Loaded {sum(len(v) for v in data.values())} candles across {len(data)} symbols")

    print("\nRunning SignalGenerator...")
    all_trades = run_and_segment(data)
    print(f"Total accepted signals: {len(all_trades)}")

    # Task 1+2: Regime filters
    print("\n" + "=" * 70)
    print("TASK 1+2 — REGIME FILTERS & PERFORMANCE")
    print("=" * 70)

    regimes = [
        ("ALL (no filter)", lambda t: True),
        ("ADX >= 35", lambda t: t["adx"] >= 35),
        ("ADX >= 25 (TRENDING)", lambda t: t["adx"] >= 25),
        ("ADX < 20 (FLAT)", lambda t: t["adx"] < 20),
        ("RSI <= 40", lambda t: t["rsi"] <= 40),
        ("RSI > 40", lambda t: t["rsi"] > 40),
        ("ADX>=35 & RSI<=40", lambda t: t["adx"] >= 35 and t["rsi"] <= 40),
        ("ADX>=25 & RSI<=40", lambda t: t["adx"] >= 25 and t["rsi"] <= 40),
        ("ADX>=35 & RSI>40", lambda t: t["adx"] >= 35 and t["rsi"] > 40),
    ]

    hdr = f"{'Regime':25s} {'Trades':>7s} {'W':>4s} {'L':>4s} {'WR%':>5s} {'PF':>6s} {'NetR':>6s} {'AvgR':>6s}"
    print(hdr)
    print("-" * 75)

    regime_results = []
    for label, filt in regimes:
        filtered = [t for t in all_trades if filt(t)]
        s = compute_stats(filtered, label)
        regime_results.append(s)
        print(f"{s['label']:25s} {s['trades']:>7d} {s['wins']:>4d} {s['losses']:>4d} "
              f"{s['wr']:>5.1f} {s['pf']:>6.2f} {s['net_r']:>+5.2f} {s['avg_r']:>+5.2f}")

    # Task 3: All observations → probability distribution in each regime
    print("\n" + "=" * 70)
    print("TASK 3 — REGIME COVERAGE (all observations, not just accepted)")
    print("=" * 70)

    # Count observations by regime
    total_obs = 0
    regime_obs = {}
    for sym in SYMBOLS:
        candles = data.get(sym, [])
        if len(candles) < 200: continue
        calc = IndicatorCalculator()
        closes = [c.close for c in candles]
        for i in range(200, len(candles)):
            w = candles[:i+1]; p = closes[:i+1]
            adx_val = calc.adx(w, 14) if len(w) >= 14 else 0
            rsi = calc.rsi(p, 14) if len(p) >= 14 else 50
            total_obs += 1
            for label, filt in regimes:
                if label == "ALL (no filter)": continue  # skip, it's all
                if label not in regime_obs: regime_obs[label] = 0
                try:
                    if filt({"adx": adx_val, "rsi": rsi}):
                        regime_obs[label] = regime_obs.get(label, 0) + 1
                except: pass

    print(f"\nTotal observations: {total_obs}")
    for label, _ in regimes:
        if label == "ALL (no filter)": continue
        cnt = regime_obs.get(label, 0)
        print(f"  {label:25s}: {cnt:>7d} ({cnt/total_obs*100:>5.1f}%)")

    # Forward regime: what % of historical trades would qualify
    print(f"\nForward regime: ADX~35, RSI~35")
    fwd_regime_trades = [t for t in all_trades if t["adx"] >= 35 and t["rsi"] <= 40]
    print(f"Historical trades in same regime: {len(fwd_regime_trades)}")
    if fwd_regime_trades:
        fs = compute_stats(fwd_regime_trades, "Forward-like regime")
        print(f"  PF={fs['pf']} WR={fs['wr']}% NetR={fs['net_r']} on {fs['trades']} trades")

    # Find periods where probability stayed below 0.55
    print("\nProbability context:")
    all_trades_sorted = sorted(all_trades, key=lambda t: f"{t['symbol']}_{t.get('adx',0)}")
    probs = [t["prob"] for t in all_trades]
    print(f"  Min probability among accepted: {min(probs):.3f}" if probs else "  No accepted trades")
    print(f"  Max probability among accepted: {max(probs):.3f}" if probs else "")
    print(f"  Avg probability among accepted: {sum(probs)/len(probs):.3f}" if probs else "")

    # Longest gap between signals
    if len(all_trades) >= 2:
        gaps = []
        for idx in range(1, len(all_trades)):
            gap = all_trades[idx].get("adx", 0) - all_trades[idx-1].get("adx", 0)
            gaps.append(abs(gap))
        print(f"  Avg gap between trades: {sum(gaps)/len(gaps):.1f} ADX units")

    # Final analysis
    print("\n" + "=" * 70)
    print("TASK 4 — FORWARD COMPARISON")
    print("=" * 70)

    # Forward regime matches
    fwd_adx = 35.2
    fwd_rsi = 35.4
    near_fwd = [t for t in all_trades if abs(t["adx"] - fwd_adx) < 10 and abs(t["rsi"] - fwd_rsi) < 10]
    print(f"\nForward regime (ADX~35, RSI~35) in historical:")
    print(f"  Signals in similar conditions: {len(near_fwd)}")
    if near_fwd:
        ns = compute_stats(near_fwd, "Near-forward")
        print(f"  PF={ns['pf']} WR={ns['wr']}% NetR={ns['net_r']} on {ns['trades']} trades")
        if ns['trades'] > 0:
            print(f"\n  → Historical DID produce signals in similar regime.")
            print(f"  → Current 0 accepted may be normal variance — need more observations.")
        else:
            print(f"\n  → Historical did NOT produce signals in this exact regime.")
            print(f"  → Current regime may be genuinely different.")
    else:
        print(f"  → No historical trades near forward regime.")
        print(f"  → Continuing forward observation is the only way.")

    print()


if __name__ == "__main__":
    asyncio.run(main())
