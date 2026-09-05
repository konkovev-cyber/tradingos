#!/usr/bin/env python3
"""S4_NEARHI live shadow detector.
FROZEN: close[bar] in top 15% of 20-bar high/low range → SHORT
SL = 1.5 × ATR14, TP = 3R, horizon = 96 bars
NO ORDERS. Read-only + virtual maker ledger.
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime, timezone
import httpx
import pandas as pd
import numpy as np

ROOT = Path("/root/tradingos/profit_engines/nearhi_shadow")
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
LOG = ROOT / "nearhi_ledger.jsonl"
STATE = ROOT / "nearhi_state.json"

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
        "AVAXUSDT","LINKUSDT","SUIUSDT","ARBUSDT","NEARUSDT","OPUSDT","DOTUSDT",
        "LTCUSDT","TRXUSDT","UNIUSDT","APTUSDT","FILUSDT","INJUSDT"]
POLL_SEC = 60
BAR_MS = 15 * 60 * 1000
WARMUP_BARS = 200
HORIZON = 96
SL_MULT = 1.5
TP_R = 3.0
TAKER_COST_R = 0.04
BASE = "https://api.bybit.com"

import logging
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("nearhi_watch")

def ledger(rec):
    rec["ts_iso"] = datetime.now(timezone.utc).isoformat()
    rec["ts"] = int(time.time() * 1000)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

def kline(sym, limit=200):
    r = httpx.get(f"{BASE}/v5/market/kline",
        params={"category":"linear","symbol":sym,"interval":"15","limit":limit}, timeout=12)
    d = r.json()
    if d.get("retCode") != 0:
        raise RuntimeError(f"retCode={d.get('retCode')}")
    rows = [(int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
            for b in d["result"]["list"]]
    df = pd.DataFrame(rows, columns=["ts","o","h","l","c","v"]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df

def prep(df):
    tr = np.maximum(df["h"]-df["l"], np.maximum((df["h"]-df["c"].shift()).abs(), (df["l"]-df["c"].shift()).abs()))
    df = df.copy()
    df["atr14"] = tr.rolling(14).mean()
    df["hi20"] = df["h"].shift(1).rolling(20).max()
    df["lo20"] = df["l"].shift(1).rolling(20).min()
    df["rng_pos"] = (df["c"]-df["lo20"]) / (df["hi20"]-df["lo20"]).replace(0, 1)
    return df

def detect_new(df, frontier_ts):
    """Return (idx, dir, level) events where close is in top 15% of 20-bar range.
    -1 = SHORT (close near 20-bar high)"""
    events = []
    cs = df["c"].values; ts = df["ts"].values
    h = df["h"].values; l = df["l"].values
    rp = df["rng_pos"].values; atr = df["atr14"].values
    n = len(df)
    for i in range(1, n):
        t = int(ts[i])
        if t <= frontier_ts: continue
        if pd.isna(rp[i]) or pd.isna(atr[i]) or atr[i]<=0: continue
        if rp[i] > 0.85:
            events.append((i, -1, float(h[i])))
    return events

def main():
    ledger({"evt":"service_start","mode":"s4_nearhi_watch","symbols":len(SYMS),
             "frozen":"rng_pos>0.85 → SHORT, SL=1.5×ATR, TP=3R"})
    series = {}
    frontier = {}
    for sym in SYMS:
        try:
            df = kline(sym, WARMUP_BARS)
            series[sym] = prep(df)
            closed_cut = int(time.time()*1000) - BAR_MS
            closed = df[df["ts"] <= closed_cut]
            frontier[sym] = int(closed["ts"].iloc[-1]) if len(closed) else int(df["ts"].iloc[-1])
            time.sleep(0.1)
        except Exception as e:
            ledger({"evt":"warmup_error","sym":sym,"err":str(e)})
    ledger({"evt":"warmup_done","n_syms":len(series)})
    STATE.write_text(json.dumps({"frontier": frontier}, indent=1))

    while True:
        t0 = time.time()
        now_ms = int(time.time()*1000)
        closed_cut = now_ms - BAR_MS
        new_events = 0
        for sym in SYMS:
            if sym not in series: continue
            try:
                df_new = kline(sym, 60)
            except Exception as e:
                ledger({"evt":"poll_error","sym":sym,"err":str(e)})
                continue
            old = series[sym]
            merged = pd.concat([old, df_new]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
            if len(merged) > 700: merged = merged.tail(700).reset_index(drop=True)
            merged = prep(merged)
            closed = merged[merged["ts"] <= closed_cut]
            evs = detect_new(closed, frontier.get(sym, 0))
            for (i, dir, lvl) in evs:
                rec = dict(sym=sym, evt="nearhi_signal", bar_i=int(i),
                           dir=dir, signal_close=float(closed["c"].iloc[i]),
                           breakout_level=lvl, atr14=float(closed["atr14"].iloc[i]),
                           sl_dist=SL_MULT*float(closed["atr14"].iloc[i]),
                           sl_bps=SL_MULT*float(closed["atr14"].iloc[i])/float(closed["c"].iloc[i])*10000,
                           entry_taker=float(closed["c"].iloc[i]),
                           tp_px=float(closed["c"].iloc[i]) + dir*TP_R*SL_MULT*float(closed["atr14"].iloc[i]),
                           sl_px=float(closed["c"].iloc[i]) - dir*SL_MULT*float(closed["atr14"].iloc[i]),
                           )
                ledger(rec)
                new_events += 1
            if len(closed):
                frontier[sym] = max(frontier.get(sym,0), int(closed["ts"].iloc[-1]))
            series[sym] = merged
            time.sleep(0.08)
        STATE.write_text(json.dumps({"frontier": frontier}, indent=1))
        ledger({"evt":"poll_heartbeat","cycle_ms":int((time.time()-t0)*1000),
                "n_syms":len(series),"new_signals":new_events})
        time.sleep(max(5.0, POLL_SEC - (time.time()-t0)))

if __name__ == "__main__":
    main()
