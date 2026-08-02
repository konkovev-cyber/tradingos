#!/usr/bin/env python3
"""
Market Regime Similarity Audit.
Compares forward observation features (ADX, RSI, ATR)
against historical 90d backtest.
"""
from __future__ import annotations

import asyncio, json, logging, math, sys
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
SYMBOLS = ["BTCUSDT","ETHUSDT","DOGEUSDT","SOLUSDT","XRPUSDT","ADAUSDT","BNBUSDT"]
DAYS = 90

_score_holder = {"score": None}
_orig_calc = ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
ss.SignalScoringEngine.calculate_with_vectors = _patched


def stats(vals):
    """Compute statistics for a list of floats."""
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": 0, "median": 0, "std": 0, "min": 0, "max": 0,
                "p25": 0, "p50": 0, "p75": 0, "p90": 0, "p95": 0}
    s = sorted(vals)
    mean = sum(vals) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in vals) / n) if n > 1 else 0
    return {
        "n": n, "mean": round(mean, 3), "median": round(s[n // 2], 3),
        "std": round(std, 3), "min": round(s[0], 3), "max": round(s[-1], 3),
        "p25": round(s[n // 4], 3), "p50": round(s[n // 2], 3),
        "p75": round(s[3 * n // 4], 3), "p90": round(s[9 * n // 10], 3),
        "p95": round(s[95 * n // 100], 3),
    }


def ks_test(sample1, sample2):
    """Two-sample KS test (simplified)."""
    from scipy.stats import ks_2samp, wasserstein_distance
    s1, s2 = list(sample1), list(sample2)
    if len(s1) < 5 or len(s2) < 5:
        return {"statistic": 0, "pvalue": 1.0, "wasserstein": 0}
    ks = ks_2samp(s1, s2)
    ws = wasserstein_distance(s1, s2)
    return {"statistic": round(ks.statistic, 4), "pvalue": round(ks.pvalue, 4),
            "wasserstein": round(ws, 4)}


async def fetch_historical() -> dict[str, dict]:
    """Fetch 90d 1h candles, run SignalGenerator, return feature stats."""
    import httpx
    hist = {"adx": [], "rsi": [], "atr": [], "prob_rejected": []}
    per_sym_adx = {s: [] for s in SYMBOLS}
    per_sym_rsi = {s: [] for s in SYMBOLS}

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
                await asyncio.sleep(0.03)
        all_c.sort(key=lambda x: x.timestamp)
        if len(all_c) > DAYS * 24: all_c = all_c[-(DAYS*24):]

        # Run SignalGenerator, collect features
        if len(all_c) < 200: continue
        calc = IndicatorCalculator()
        sg = SignalGenerator()
        closes = [c.close for c in all_c]

        for i in range(200, len(all_c)):
            w = all_c[:i+1]; p = closes[:i+1]
            ema20=calc.ema(p,20); ema50=calc.ema(p,50); ema200=calc.ema(p,200)
            rsi=calc.rsi(p,14); atr=calc.atr(w,14); adx_val=calc.adx(w,14)
            macd=calc.macd(p); bb=calc.bollinger(p); vwap=calc.vwap(w)
            vr=calc.volume_ratio([c.volume for c in w],20)
            c=all_c[i]
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

            hist["adx"].append(adx_val)
            hist["rsi"].append(rsi)
            hist["atr"].append(atr)
            per_sym_adx[sym].append(adx_val)
            per_sym_rsi[sym].append(rsi)

            score = _score_holder.get("score")
            if score is not None and direction is None:
                hist["prob_rejected"].append(score.final_probability)

    return hist, per_sym_adx, per_sym_rsi


def load_forward() -> dict:
    """Load forward telemetry from signal_log."""
    fwd = {"adx": [], "rsi": [], "atr": [], "prob": [], "prob_rejected": [],
           "per_sym": {s: {"adx": [], "rsi": []} for s in SYMBOLS}}

    log_path = ROOT / "memory" / "signal_log.jsonl"
    if not log_path.exists():
        return fwd

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            sym = d.get("symbol", "")
            if sym not in SYMBOLS: continue
            fwd["adx"].append(d.get("adx", 0))
            fwd["rsi"].append(d.get("rsi", 0))
            fwd["atr"].append(d.get("atr", 0))
            fwd["prob"].append(d.get("final_probability", 0))
            if d.get("direction") == "NONE":
                fwd["prob_rejected"].append(d.get("final_probability", 0))
            if sym in fwd["per_sym"]:
                fwd["per_sym"][sym]["adx"].append(d.get("adx", 0))
                fwd["per_sym"][sym]["rsi"].append(d.get("rsi", 0))

    return fwd


async def main():
    print("=" * 70)
    print("MARKET REGIME SIMILARITY AUDIT")
    print("=" * 70)

    # Load forward data
    print("\nLoading forward telemetry...")
    fwd = load_forward()
    print(f"  Forward observations: {len(fwd['adx'])}")
    print(f"  Forward regected probs: {len(fwd['prob_rejected'])}")

    # Load historical data
    print(f"\nFetching 90 days historical data...")
    hist, per_sym_adx, per_sym_rsi = await fetch_historical()
    print(f"  Historical observations: {len(hist['adx'])}")

    # Task 1: Feature Distribution
    print("\n" + "=" * 70)
    print("TASK 1 — FEATURE DISTRIBUTION")
    print("=" * 70)

    for feat_name, feat_key in [("ADX", "adx"), ("RSI", "rsi"), ("ATR", "atr")]:
        h_vals = hist[feat_key]
        f_vals = fwd[feat_key]
        h_s = stats(h_vals)
        f_s = stats(f_vals)
        print(f"\n{feat_name}:")
        print(f"  {'Metric':8s} {'Historical':>12s} {'Forward':>12s} {'Delta':>12s}")
        print(f"  {'-'*46}")
        for m in ["n", "mean", "median", "std", "min", "max", "p25", "p75", "p90", "p95"]:
            h_v = h_s[m]
            f_v = f_s[m]
            d = f_v - h_v if isinstance(h_v, (int, float)) and isinstance(f_v, (int, float)) else 0
            print(f"  {m:8s} {h_v:>12} {f_v:>12} {d:>+12.3f}" if isinstance(d, float) else
                  f"  {m:8s} {h_v:>12} {f_v:>12} {'--':>12}")

    # Task 2: Statistical Tests
    print("\n" + "=" * 70)
    print("TASK 2 — STATISTICAL SIMILARITY")
    print("=" * 70)

    print(f"\n{'Feature':8s} {'KS stat':>9s} {'p-value':>9s} {'Wasserst':>9s} {'Verdict':>12s}")
    print(f"{'-'*52}")
    veredicts = []
    for feat_key in ["adx", "rsi", "atr"]:
        h = hist[feat_key]
        f = fwd[feat_key]
        if len(h) < 5 or len(f) < 5:
            print(f"{feat_key:8s} {'--':>9s} {'--':>9s} {'--':>9s} {'INSUFFICIENT':>12s}")
            veredicts.append("INSUFFICIENT")
            continue
        t = ks_test(h, f)
        v = "MATCH" if t["pvalue"] > 0.05 else "DIFFERENT"
        print(f"{feat_key:8s} {t['statistic']:>9.4f} {t['pvalue']:>9.4f} {t['wasserstein']:>9.4f} {v:>12s}")
        veredicts.append(v)

    # Task 3: Regime Distribution
    print("\n" + "=" * 70)
    print("TASK 3 — MARKET REGIME")
    print("=" * 70)

    for label, vals in [("Historical", hist["adx"]), ("Forward", fwd["adx"])]:
        n = len(vals)
        if n == 0:
            print(f"\n  {label}: no data")
            continue
        trending = sum(1 for v in vals if v >= 25)
        weak = sum(1 for v in vals if 20 <= v < 25)
        flat = sum(1 for v in vals if v < 20)
        print(f"\n  {label} (n={n}):")
        print(f"    TRENDING (ADX≥25): {trending/n*100:.0f}%")
        print(f"    WEAK (20-25):      {weak/n*100:.0f}%")
        print(f"    FLAT (ADX<20):     {flat/n*100:.0f}%")

    # Task 4: Symbol Drift
    print("\n" + "=" * 70)
    print("TASK 4 — SYMBOL DRIFT")
    print("=" * 70)
    print(f"\n{'Symbol':9s} {'Hist ADX':>9s} {'Fwd ADX':>9s} {'Δ ADX':>7s} {'Match':>8s}")
    print(f"{'-'*46}")
    for sym in SYMBOLS:
        h_adx = per_sym_adx.get(sym, [])
        f_adx = fwd.get("per_sym", {}).get(sym, {}).get("adx", [])
        h_avg = sum(h_adx)/len(h_adx) if h_adx else 0
        f_avg = sum(f_adx)/len(f_adx) if f_adx else 0
        delta = f_avg - h_avg
        match = "OK" if abs(delta) < 5 else "DRIFT"
        print(f"{sym:9s} {h_avg:>9.1f} {f_avg:>9.1f} {delta:>+7.1f} {match:>8s}")

    # Task 5: Probability Context
    print("\n" + "=" * 70)
    print("TASK 5 — PROBABILITY CONTEXT")
    print("=" * 70)

    h_rej = hist.get("prob_rejected", [])
    f_prob = fwd.get("prob_rejected", [])

    print(f"\n{'Metric':12s} {'Hist Rej':>12s} {'Forward':>12s}")
    print(f"{'-'*40}")
    if h_rej and f_prob:
        h_s = stats(h_rej)
        f_s = stats(f_prob)
        for m in ["mean", "median", "p90", "p95", "min", "max"]:
            print(f"{m:12s} {h_s[m]:>12.4f} {f_s[m]:>12.4f}")
        # Distance to threshold
        h_dist = [0.55 - p for p in h_rej]
        f_dist = [0.55 - p for p in f_prob]
        print(f"\n  Distance to threshold (0.55):")
        print(f"  {'Metric':12s} {'Hist':>12s} {'Forward':>12s}")
        print(f"  {'mean':12s} {sum(h_dist)/len(h_dist):>12.4f} {sum(f_dist)/len(f_dist):>12.4f}")
        print(f"  {'min':12s} {min(h_dist):>12.4f} {min(f_dist):>12.4f}")

        f_min = min(f_prob)
        h_max_prob = max(h_rej)
        print(f"\n  Forward prob range: {min(f_prob):.4f}–{max(f_prob):.4f}")
        print(f"  Historical rejected range: {min(h_rej):.4f}–{max(h_rej):.4f}")
        if max(f_prob) <= max(h_rej):
            print(f"  → Forward probabilities INSIDE historical rejected range")
        else:
            print(f"  → Forward probabilities partially ABOVE historical rejected range")
    else:
        print("  Insufficient data for probability comparison")
        print(f"  Historical rejected probs: {len(h_rej)}")
        print(f"  Forward probs: {len(f_prob)}")

    # Verdict
    print("\n" + "=" * 70)
    print("FINAL VERDICT")
    print("=" * 70)

    matches = sum(1 for v in veredicts if v == "MATCH")
    different = sum(1 for v in veredicts if v == "DIFFERENT")
    insufficient = sum(1 for v in veredicts if v == "INSUFFICIENT")

    n_forward = len(fwd["adx"])

    print(f"\n  Statistical tests: {matches} MATCH, {different} DIFFERENT, {insufficient} INSUFFICIENT")
    print(f"  Forward sample size: {n_forward}")

    if n_forward < 50:
        print(f"\n  → C — INSUFFICIENT DATA")
        print(f"    Forward sample too small ({n_forward} observations).")
        print(f"    Need minimum ~50-100 for reliable comparison.")
        print(f"    Continue observation.")
    elif different >= 2:
        print(f"\n  → B — REGIME SHIFT")
        print(f"    {different}/3 features show statistically significant difference.")
        print(f"    Forward period differs from historical.")
        print(f"    Continue observation — account for shift in signal frequency interpretation.")
    else:
        print(f"\n  → A — REGIME MATCH")
        print(f"    Forward market conditions statistically similar to historical period.")
        print(f"    Historical PF=2.14 should be reproducible.")
        print(f"    Continue observation.")

    print()

    # Restart observation
    import subprocess
    subprocess.Popen(["setsid", "python3", "-m", "tradingos.data.run_observation"],
                     cwd="/root", stdout=open("/dev/null", "w"), stderr=open("/dev/null", "w"))
    print("  Observation restarted.")


if __name__ == "__main__":
    asyncio.run(main())
