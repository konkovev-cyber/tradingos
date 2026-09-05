"""
Per-(TF, fam) B2 pipeline economic analysis using only signal events collected.
We use a 1-bar forward window for the basic OOS check (since we don't have full forward-window bars recorded).
For each signal:
  - If event_ts in market data, run live-bars replay from index = (event bar + 1) for 96 bars.
  - If event is from warmup (>= 7 days old), use 7-day window only.
For each (TF, fam) compute: taker NET, maker NET, PF, WR, anti-cherry.
"""
import os, json, glob
from collections import defaultdict
import pandas as pd
import numpy as np
import httpx, time

ROOT = "/root/tradingos/profit_engines/b2_shadow"
TF_INTERVAL = {"M15": 15, "H1": 60, "H4": 240}
SL_MULT = 1.5
TP_R = 3.0
HORIZON = {"M15": 96, "H1": 48, "H4": 12}   # 96 bars per TF (covers 1 day M15, 2 days H1, 2 days H4)
TAKER_FEE_BPS = {"M15": 13, "H1": 11, "H4": 11}  # base slippage
MAKER_FEE_BPS = 4
AS_BPS = {"M15": 3, "H1": 4, "H4": 5}

def get_klines(sym, interval, end_ms, limit=200):
    start_ms = end_ms - limit * TF_INTERVAL.get(interval, 60) * 60 * 1000
    r = httpx.get("https://api.bybit.com/v5/market/kline",
        params={"category":"linear","symbol":sym,"interval":interval,
                "start":start_ms,"end":end_ms,"limit":limit}, timeout=10).json()
    if r.get("retCode") != 0: return None
    rows = [(int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
            for b in r["result"]["list"]]
    if not rows: return None
    df = pd.DataFrame(rows, columns=["ts","o","h","l","c","v"]).sort_values("ts").reset_index(drop=True)
    return df

def atr14(series, idx):
    # ATR14 computed on the bars up to idx (no lookahead)
    if idx < 14: return None
    sub = series.iloc[max(0, idx-13):idx+1]
    tr = pd.Series(np.maximum(sub["h"]-sub["l"],
        np.maximum((sub["h"]-sub["c"].shift()).abs(), (sub["l"]-sub["c"].shift()).abs())))
    if len(tr) < 2: return None
    return tr.rolling(14, min_periods=2).mean().iloc[-1]

def run_horizon(sym, interval, event_ts, dir, src):
    """Run a small horizon for the signal at event_ts."""
    interval_ms = TF_INTERVAL.get(interval, 60) * 60 * 1000
    bar_ms = interval_ms
    horizon_bars = HORIZON.get(interval, 96)
    end_ms = event_ts + horizon_bars * bar_ms + bar_ms
    df = get_klines(sym, interval, end_ms)
    if df is None or len(df) < 20: return None
    # find event bar index
    idxs = df.index[df["ts"] >= event_ts]
    if len(idxs) == 0: return None
    i0 = idxs[0]
    atr = atr14(df, i0)
    if atr is None or atr <= 0: return None
    h, l, c = df["h"].values, df["l"].values, df["c"].values
    entry = c[i0]
    sl = SL_MULT * atr
    side = 1 if dir == "LONG" else -1
    # taker reference: close-to-close entry; SL below (LONG) or above (SHORT) entry by sl
    sl_px = entry - side * sl
    tp_px = entry + side * TP_R * sl
    stopped = hit = False
    mfe = mae = 0.0
    end = min(i0 + 1 + horizon_bars, len(df))
    for j in range(i0+1, end):
        fav = (h[j] - entry) * side
        adv = (entry - l[j]) * side
        mfe = max(mfe, fav); mae = max(mae, adv)
        st = (l[j] - sl_px) * side <= 0
        tg = (h[j] - tp_px) * side >= 0
        if tg and not st: hit = True; break
        if st: stopped = True; break
    if hit: pnl_R = TP_R
    elif stopped: pnl_R = -1.0
    else: pnl_R = (c[end-1] - entry) * side / sl
    return dict(pnl_R=pnl_R, mfe_R=mfe/sl, mae_R=mae/sl, sl_bps=sl/entry*10000, fill=True, hit=hit, stopped=stopped)

def load_events(tf, fam):
    rows = []
    for f in glob.glob(f"{ROOT}/led_{tf}_{fam}_*.jsonl"):
        for line in open(f):
            try: rows.append(json.loads(line)) 
            except: pass
    return rows

# Cache klines to avoid re-fetching
KLINE_CACHE = {}

def fetch_klines_cached(sym, interval, end_ms, limit=200):
    key = (sym, interval, limit)
    if key in KLINE_CACHE:
        df = KLINE_CACHE[key]
        # ensure we have enough bars; re-fetch if needed
        if df["ts"].max() < end_ms - 1000:
            df2 = get_klines(sym, interval, end_ms, limit)
            if df2 is not None:
                df = df2
                KLINE_CACHE[key] = df
    else:
        df = get_klines(sym, interval, end_ms, limit)
        if df is not None:
            KLINE_CACHE[key] = df
    return KLINE_CACHE.get(key)

def analyze_combo(tf, fam, max_signals=20):
    events = load_events(tf, fam)
    if len(events) < 5: return None
    # get unique events (sometimes frontier re-fires, we have a frontier file but stored events include multiple)
    seen = set()
    unique = []
    for e in events:
        k = (e.get("ts"), e.get("dir"))
        if k in seen: continue
        seen.add(k)
        unique.append(e)
    # limit to most recent max_signals
    unique = sorted(unique, key=lambda x: x["ts"])[-max_signals:]
    rows = []
    for e in unique:
        sym = e.get("sym")
        ts = e.get("ts")
        d = e.get("dir")
        interval = TF_INTERVAL[tf]
        bar_ms = interval * 60 * 1000
        end_ms = ts + HORIZON[tf] * bar_ms + bar_ms
        df = fetch_klines_cached(sym, tf, end_ms, 200)
        if df is None: continue
        if df["ts"].max() < end_ms - 1000:
            continue  # no recent data
        out = run_horizon(sym, tf, ts, d, fam)
        if out is None: continue
        out["sym"] = sym; out["tf"] = tf; out["fam"] = fam
        out["ts"] = ts
        rows.append(out)
        time.sleep(0.05)  # rate-limit friendly
    if len(rows) < 5: return None
    return rows

# COSTS
def taker_cost(tf, sl_bps): return (TAKER_FEE_BPS[tf] / sl_bps) if sl_bps > 0 else 0.5
def maker_cost(tf, sl_bps): return ((MAKER_FEE_BPS + AS_BPS[tf]) / sl_bps) if sl_bps > 0 else 0.5

# REPORT
results = []
for tf in TF_INTERVAL:
    for fam in ["B2", "E1", "E2", "E3", "E4", "E5"]:
        rows = analyze_combo(tf, fam, max_signals=12)
        if rows is None: continue
        pnls = [r["pnl_R"] for r in rows]
        mfe = [r["mfe_R"] for r in rows]
        mae = [r["mae_R"] for r in rows]
        sl_bps_arr = [r["sl_bps"] for r in rows]
        avg_sl_bps = np.mean(sl_bps_arr)
        # taker NET per R
        taker_cost_R = taker_cost(tf, avg_sl_bps)
        taker_net = np.array(pnls) - taker_cost_R
        # maker NET per R
        maker_cost_R = maker_cost(tf, avg_sl_bps)
        maker_net = np.array(pnls) - maker_cost_R
        wr = float(np.mean([1 if p > 0 else 0 for p in pnls]))
        avg_win = float(np.mean([p for p in pnls if p > 0]) or 0)
        avg_loss = float(np.mean([p for p in pnls if p < 0]) or 0)
        # noTop3
        sorted_pnls = sorted(taker_net, reverse=True)
        noTop3 = float(np.mean(sorted_pnls[3:]) if len(sorted_pnls) > 3 else 0)
        # per-symbol
        syms_seen = set(r["sym"] for r in rows)
        sym_pnls = {}
        for r in rows:
            sym_pnls.setdefault(r["sym"], []).append(r["pnl_R"] - taker_cost_R)
        positive_syms = sum(1 for s, p in sym_pnls.items() if np.mean(p) > 0)
        results.append({
            "TF": tf, "fam": fam, "n": len(rows),
            "wr": wr, "avg_win_R": avg_win, "avg_loss_R": avg_loss,
            "median_pnl_R": float(np.median(pnls)),
            "median_taker_net_R": float(np.median(taker_net)),
            "median_maker_net_R": float(np.median(maker_net)),
            "mean_taker_net_R": float(np.mean(taker_net)),
            "mean_maker_net_R": float(np.mean(maker_net)),
            "noTop3": noTop3,
            "positive_syms": positive_syms, "total_syms": len(syms_seen),
            "avg_sl_bps": avg_sl_bps,
            "taker_cost_R": taker_cost_R,
            "maker_cost_R": maker_cost_R,
            "MFE_med_R": float(np.median(mfe)),
            "MAE_med_R": float(np.median(mae)),
        })

# Output report
print("=" * 100)
print("B2 MONEY VERDICT: ECONOMIC ANALYSIS OF (TF, FAM) COMBINATIONS")
print("=" * 100)
print()
print(f"{'TF':4s}{'fam':5s}{'n':>4s}{'wr%':>6s}{'winR':>7s}{'lossR':>8s}{'medPNL':>9s}{'tNETmed':>9s}{'mNETmed':>9s}{'noTop3':>9s}{'+syms':>7s}{'tot':>5s}{'tNET':>9s}{'mNET':>9s}{'sl':>5s}")
for r in results:
    print(f"{r['TF']:4s}{r['fam']:5s}{r['n']:>4d}{r['wr']*100:>5.0f}%{r['avg_win_R']:>+7.2f}{r['avg_loss_R']:>+7.2f}"
          f"{r['median_pnl_R']:>+8.3f}{r['median_taker_net_R']:>+8.3f}{r['median_maker_net_R']:>+8.3f}"
          f"{r['noTop3']:>+8.3f}{r['positive_syms']:>7d}/{r['total_syms']:<2d}{r['mean_taker_net_R']:>+8.3f}{r['mean_maker_net_R']:>+8.3f}{r['avg_sl_bps']:>5.0f}")
print()
print("=" * 100)
print("TOP-10 BY median_maker_net_R (maker assumption only, no cherry-picking)")
print("=" * 100)
top = sorted(results, key=lambda r: -r['median_maker_net_R'])[:10]
print(f"{'rank':4s}{'TF':4s}{'fam':5s}{'n':>4s}{'med_maker_R':>13s}{'mean_maker_R':>13s}{'noTop3':>9s}{'+syms/tot':>10s}")
for i, r in enumerate(top, 1):
    print(f"{i:>3d}  {r['TF']:4s}{r['fam']:5s}{r['n']:>4d}{r['median_maker_net_R']:>+13.3f}{r['mean_maker_net_R']:>+13.3f}{r['noTop3']:>+9.3f}{r['positive_syms']:>5d}/{r['total_syms']:<3d}")

print()
print("=" * 100)
print("$80 MONEY TRANSLATION (top candidate maker assumption only)")
print("=" * 100)
if top:
    r = top[0]
    risk = 0.50
    net_R_med = r['median_maker_net_R']
    net_R_mean = r['mean_maker_net_R']
    print(f"  Top candidate: {r['TF']} {r['fam']} (n={r['n']})")
    print(f"  Median maker NET_R per trade: {net_R_med:+.3f}R  →  ${risk*net_R_med:+.4f} per trade at $0.50 risk")
    print(f"  Mean   maker NET_R per trade: {net_R_mean:+.3f}R  →  ${risk*net_R_mean:+.4f}")
    print(f"  noTop3 maker NET_R:           {r['noTop3']:+.3f}R  →  ${risk*r['noTop3']:+.4f}")
    # freq is UNKNOWN (b2-pipeline just started collecting; we used historical warmup)
    print(f"  Frequency: UNKNOWN (b2-pipeline started recently; only historical warmup data used in this analysis)")
    print(f"  SL distance:                  {r['avg_sl_bps']:.1f} bps")
    print(f"  Maker cost_R:                  {r['maker_cost_R']:.3f}R (= (4+{4 if r['TF']=='M15' else 5}bps) / {r['avg_sl_bps']:.0f}bps)")
    print(f"  WR:                            {r['wr']*100:.0f}%")
    print(f"  Positive symbols:              {r['positive_syms']}/{r['total_syms']}")

print()
print("=" * 100)
print("MONEY VERDICT")
print("=" * 100)
best = top[0] if top else None
if best is None or best['n'] < 5:
    print("MONEY VERDICT: REJECT (insufficient sample)")
elif best['median_maker_net_R'] <= 0:
    print(f"MONEY VERDICT: REJECT (median maker NET_R = {best['median_maker_net_R']:+.3f}R <= 0)")
elif best['mean_maker_net_R'] <= 0:
    print(f"MONEY VERDICT: REJECT (mean maker NET_R = {best['mean_maker_net_R']:+.3f}R <= 0)")
elif best['positive_syms'] < best['total_syms'] / 2:
    print(f"MONEY VERDICT: REJECT (only {best['positive_syms']}/{best['total_syms']} symbols positive)")
elif best['noTop3'] <= 0:
    print(f"MONEY VERDICT: REJECT (noTop3 = {best['noTop3']:+.3f}R <= 0)")
else:
    print(f"MONEY VERDICT: INCONCLUSIVE (small sample n={best['n']}, possibly PROMISING but more data needed)")
    print()
    print(f"  Top candidate: {best['TF']} {best['fam']}")
    print(f"  Median maker NET_R: {best['median_maker_net_R']:+.3f}R  (mean: {best['mean_maker_net_R']:+.3f}R, noTop3: {best['noTop3']:+.3f}R)")
    print(f"  WR: {best['wr']*100:.0f}%  positive_syms: {best['positive_syms']}/{best['total_syms']}  n={best['n']}")
    print(f"  Requires MANUAL APPROVAL before any live testing.")

# Save full report
with open("/root/tradingos/profit_engines/b2_shadow/econ_report.json", "w") as f:
    json.dump(results, f, indent=1, default=str)
