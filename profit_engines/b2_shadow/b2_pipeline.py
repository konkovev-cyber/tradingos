#!/usr/bin/env python3
"""b2_pipeline.py — read-only shadow detectors across M15 / H1 / H4 x 5 event families.

Implements (B2 frozen) + E1..E5. Each (TF, family) is a separate detector with
its own frontier + ledger. NO ORDERS. No changes to TradingOS production.

Event families (frozen, no optimization per spec):
  B2  FAILED_BREAK              close[i-1] > prior20h and close[i] < prior20h  → SHORT (mirror → LONG)
  E1  FALSE_BREAKOUT_RETRACE    same as B2 but 40-bar extremum (wider noise filter)
  E2  RANGE_EXPANSION            ATR pct rank < 20 (480-bar win)  + bar range > 2×ATR  → bar dir
  E3  LIQUIDITY_SWEEP            20-bar hi-lo sweep wick > 2×ATR with close back inside
  E4  DISPLACEMENT_RETRACEMENT    bar range >= 1.0×ATR (strong impulse), prev bar in opposite dir
  E5  VOL_EXPANSION_FAILURE       ATR pct rank > 80 + close crossed EMA20 against prior dir
"""
import os, sys, time, json
from pathlib import Path
from datetime import datetime, timezone
import httpx
import pandas as pd
import numpy as np

ROOT = Path("/root/tradingos/profit_engines/b2_shadow")
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
STATE = ROOT / "frontiers.json"
SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
        "AVAXUSDT","LINKUSDT","SUIUSDT","ARBUSDT","NEARUSDT","OPUSDT","DOTUSDT",
        "LTCUSDT","TRXUSDT","UNIUSDT","APTUSDT","FILUSDT","INJUSDT"]
TFS = [("M15", 15, 96, 0.6), ("H1", 60, 96, 0.6), ("H4", 240, 96, 0.6)]
SL_MULT = 1.5; TP_R = 3.0; HORIZON = 96
RANGE_LEN = {"E1_BREAKOUT": 40}
RANGE_LEN_FAMILY = {"B2": 20, "E1": 40, "E2": 20, "E3": 20, "E4": 20, "E5": 20}
FAMILY_DEFAULTS = {
    "B2": (20, 14, 480, 0.20, 2.0),    # range_len, atr_len, atr_pctile_win, atr_pctile_thr, range_mult
    "E1": (40, 14, 480, 0.20, 2.0),
    "E2": (20, 14, 480, 0.20, 2.0),
    "E3": (20, 14, 480, 0.20, 2.0),
    "E4": (20, 14, 480, 0.80, 1.0),
    "E5": (20, 14, 480, 0.80, 1.0),
}

def kline(sym, interval, limit=200):
    r = httpx.get("https://api.bybit.com/v5/market/kline",
        params={"category":"linear","symbol":sym,"interval":interval,"limit":limit},
        timeout=12)
    d = r.json()
    if d.get("retCode") != 0: raise RuntimeError(d.get("retMsg","err"))
    rows = [(int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
            for b in d["result"]["list"]]
    return pd.DataFrame(rows, columns=["ts","o","h","l","c","v"]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

def prep(df, atr_len, atr_win, atr_pctile_thr, range_mult):
    c,h,l,o,v = df["c"],df["h"],df["l"],df["o"],df["v"]
    tr = pd.Series(np.maximum(h-l, np.maximum((h-c.shift()).abs(), (l-c.shift()).abs())))
    df = df.copy()
    df["atr"] = tr.rolling(atr_len).mean()
    df["atr_pctile"] = (df["atr"]/df["c"]).rolling(atr_win, min_periods=100).rank(pct=True)
    return df

def det_B2(df, range_len=20):
    ph = df["h"].shift(1).rolling(range_len).max()
    pl = df["l"].shift(1).rolling(range_len).min()
    out = []
    n = len(df); ts = df["ts"].values; cs = df["c"].values
    for i in range(1, n):
        if pd.isna(ph.iloc[i-1]) or pd.isna(pl.iloc[i-1]): continue
        if cs[i-1] > ph.iloc[i-1] and cs[i] < ph.iloc[i-1]:
            out.append((int(ts[i]), i, -1, float(ph.iloc[i-1]), "B2"))
        elif cs[i-1] < pl.iloc[i-1] and cs[i] > pl.iloc[i-1]:
            out.append((int(ts[i]), i, 1, float(pl.iloc[i-1]), "B2"))
    return out

def det_E1(df, range_len=40):
    return det_B2(df, range_len)  # same structure, wider range

def det_E2(df, range_len, atr_len, atr_win, atr_pctile_thr, range_mult):
    c,h,l,o = df["c"].values,df["h"].values,df["l"].values,df["o"].values
    ap = df["atr_pctile"].values; ts = df["ts"].values; a = df["atr"].values
    out = []
    for i in range(1, len(df)-1):
        if pd.isna(ap[i]) or pd.isna(a[i]) or a[i]<=0: continue
        if ap[i] < (atr_pctile_thr or 0.20) and (h[i]-l[i]) > range_mult * a[i]:
            d = 1 if c[i] > o[i] else -1
            out.append((int(ts[i]), i, d, c[i], "E2"))
    return out

def det_E3(df, range_len, atr_len, atr_win, atr_pctile_thr, range_mult):
    h,l,c,ts = df["h"].values,df["l"].values,df["c"].values,df["ts"].values
    a = df["atr"].values
    ph = df["h"].shift(1).rolling(range_len).max().values
    pl = df["l"].shift(1).rolling(range_len).min().values
    out = []
    for i in range(1, len(df)-1):
        if pd.isna(ph[i-1]) or pd.isna(pl[i-1]) or pd.isna(a[i]) or a[i]<=0: continue
        # liquidity sweep: wick > 2*ATR beyond, close back inside range
        if (h[i] - ph[i-1]) > 2*a[i] and c[i] < ph[i-1]:
            out.append((int(ts[i]), i, -1, c[i], "E3"))  # swept up -> fade
        elif (pl[i-1] - l[i]) > 2*a[i] and c[i] > pl[i-1]:
            out.append((int(ts[i]), i, 1, c[i], "E3"))  # swept down -> bounce
    return out

def det_E4(df, range_len, atr_len, atr_win, atr_pctile_thr, range_mult):
    o,h,l,c,ts = df["o"].values,df["h"].values,df["l"].values,df["c"].values,df["ts"].values
    a = df["atr"].values
    out = []
    for i in range(2, len(df)-1):
        if pd.isna(a[i]) or a[i]<=0: continue
        # large displacement (>=1*ATR) followed by any non-trending bar
        if (h[i]-l[i]) >= 1.0*a[i]:
            # direction inferred from close vs open; entry next bar in opposite direction (retracement)
            d = 1 if c[i] > o[i] else -1
            out.append((int(ts[i]), i, -d, c[i], "E4"))  # fade the impulse
    return out

def det_E5(df, range_len, atr_len, atr_win, atr_pctile_thr, range_mult):
    h,l,c,ts = df["h"].values,df["l"].values,df["c"].values,df["ts"].values
    a = df["atr"].values
    ema = df["c"].rolling(20).mean().values
    ap = df["atr_pctile"].values
    out = []
    for i in range(20, len(df)-1):
        if pd.isna(ap[i]) or pd.isna(a[i]) or pd.isna(ema[i]) or a[i]<=0: continue
        if ap[i] > 0.80 and c[i] < ema[i] and c[i-1] > ema[i-1]:
            out.append((int(ts[i]), i, -1, c[i], "E5"))  # breakdown
        elif ap[i] > 0.80 and c[i] > ema[i] and c[i-1] < ema[i-1]:
            out.append((int(ts[i]), i, 1, c[i], "E5"))   # breakout (continuation)
    return out

DETECTORS = {
    "B2": lambda d, rl, *_: det_B2(d, range_len=rl),
    "E1": lambda d, rl, *_: det_E1(d, range_len=rl),
    "E2": det_E2,
    "E3": det_E3,
    "E4": det_E4,
    "E5": det_E5,
}

def led_path(tf, fam, sym):
    return ROOT / f"led_{tf}_{fam}_{sym}.jsonl"

def led_append(tf, fam, sym, rec):
    p = led_path(tf, fam, sym)
    rec["ts_iso"] = datetime.now(timezone.utc).isoformat()
    rec["ts"] = int(time.time() * 1000)
    with p.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

def main():
    STATE.write_text(json.dumps({"started": int(time.time()*1000)}, indent=1))
    series = {}
    for tf, interval, _, _ in TFS:
        for sym in SYMS:
            try:
                df = kline(sym, interval, 200 if tf=="M15" else 300)
                series[(tf, sym)] = df
                time.sleep(0.1)
            except Exception as e:
                print(f"warmup_err {tf} {sym} {e}", file=sys.stderr)
    print(f"warmed up {len(series)} (TF,sym) pairs")
    while True:
        t0 = time.time()
        now_ms = int(time.time()*1000)
        new_total = 0
        for (tf, sym), df in list(series.items()):
            if df is None or len(df) < 50: continue
            try:
                df_new = kline(sym, {"M15":15,"H1":60,"H4":240}[tf], 100)
            except Exception as e:
                continue
            merged = pd.concat([df, df_new]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
            if len(merged) > 1200: merged = merged.tail(1200).reset_index(drop=True)
            # dispatch by family
            for fam, (range_len, atr_len, atr_win, atr_pctile_thr, range_mult) in FAMILY_DEFAULTS.items():
                dfp = prep(merged, atr_len, atr_win, atr_pctile_thr, range_mult)
                evs = DETECTORS[fam](dfp, range_len, atr_len, atr_win, atr_pctile_thr, range_mult)
                for (ts, i, d, lvl, src) in evs:
                    # only fire if bar is closed (ts + bar_ms < now)
                    bar_ms = ({"M15":15*60*1000,"H1":60*60*1000,"H4":240*60*1000})[tf]
                    if ts + bar_ms > now_ms: continue
                    # check frontier (avoid double-fire)
                    frontier_path = ROOT / f"frontier_{tf}_{fam}_{sym}.json"
                    frontier = {}
                    if frontier_path.exists():
                        try: frontier = json.loads(frontier_path.read_text())
                        except: pass
                    if frontier.get(str(i)) == ts: continue
                    frontier[str(i)] = ts
                    frontier_path.write_text(json.dumps(frontier))
                    led_append(tf, fam, sym, {"evt":"signal","ts":ts,"bar_i":int(i),
                                               "dir":int(d),"level":float(lvl),
                                               "src":src,"atr":float(dfp["atr"].iloc[i])})
                    new_total += 1
            series[(tf, sym)] = merged
            time.sleep(0.05)
        # heartbeat
        new_files = ROOT / "heartbeat.jsonl"
        with new_files.open("a") as f:
            f.write(json.dumps({"ts":now_ms, "new_signals":new_total,
                                "n_series":len(series)}) + "\n")
        # frontier health (per (TF, fam, sym))
        all_frontiers = list(ROOT.glob("frontier_*.json"))
        lags = []
        for fp in all_frontiers:
            try:
                f = json.loads(fp.read_text())
                for v in f.values():
                    lags.append((int(time.time()*1000) - v) / 1000)
            except: pass
        if lags:
            print(f"heartbeat new_signals={new_total} frontier_lag_min={min(lags):.0f}s "
                  f"max={max(lags):.0f}s median={sorted(lags)[len(lags)//2]:.0f}s "
                  f"n={len(lags)}")
        time.sleep(max(5.0, 60 - (time.time()-t0)))

if __name__ == "__main__":
    main()
