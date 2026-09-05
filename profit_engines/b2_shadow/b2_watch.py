#!/usr/bin/env python3
"""b2_watch.py — live B2 FAILED_BREAK shadow detector + virtual maker execution ledger.

PHASE 2 (watch) + PHASE 3 (virtual PostOnly fills) + PHASE 4 (economic ledger).
NO ORDERS. Read-only market data. Separate from TradingOS production and from H2.

B2 definition (frozen, verified Phase 1 vs reference):
  up-failure:  close[i-1] > prior20_high[i-1]  AND close[i] < prior20_high[i-1] -> SHORT
  dn-failure:  close[i-1] < prior20_low[i-1]   AND close[i] > prior20_low[i-1]  -> LONG
  prior20_high = rolling(20).max of highs shifted 1 bar (excludes current bar)
  SL = 1.5 x ATR14, TP = 3R, horizon 96 bars (24h).
Entry (taker reference) = close of signal bar.
Virtual maker entries = signal-bar range fractions 0.6 / 0.75 / 0.9 (three variants),
  fill requires trade-THROUGH (next bars must trade strictly beyond limit price).

Frontier health (lesson from H2 bug): every heartbeat logs frontier ts + lag per-symbol
min/max; frontier only advances on freshly CLOSED bars (bar closed = ts + 900s <= now).
"""
import os, sys, json, time, hmac, hashlib, logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import httpx
import pandas as pd
import numpy as np

ROOT = Path("/root/tradingos/profit_engines/b2_shadow")
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
LOG = ROOT / "b2_ledger.jsonl"
STATE = ROOT / "b2_state.json"

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
        "AVAXUSDT","LINKUSDT","SUIUSDT","ARBUSDT","NEARUSDT","OPUSDT","DOTUSDT",
        "LTCUSDT","TRXUSDT","UNIUSDT","APTUSDT","FILUSDT","INJUSDT"]
POLL_SEC = 60
BAR_MS = 15 * 60 * 1000
WARMUP_BARS = 200          # >> 20-bar range + 14 ATR
HORIZON = 96               # forward window in bars
SL_MULT = 1.5
TP_R = 3.0
MAKER_FRACS = [0.6, 0.75, 0.9]
TAKER_COST_BPS = 13.0      # reference taker economics
MAKER_FEE_BPS = 4.0
AS_BPS = 3.0               # adverse selection estimate
RISK_USD = 0.50
BASE = "https://api.bybit.com"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("b2_watch")

def ledger(rec):
    rec["ts_iso"] = datetime.now(timezone.utc).isoformat()
    rec["ts"] = int(time.time() * 1000)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

def kline(sym, limit=200):
    r = httpx.get(f"{BASE}/v5/market/kline",
        params={"category":"linear","symbol":sym,"interval":"15","limit":limit},
        timeout=12)
    d = r.json()
    if d.get("retCode") != 0:
        raise RuntimeError(f"retCode={d.get('retCode')} {d.get('retMsg')}")
    rows = [(int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
            for b in d["result"]["list"]]
    df = pd.DataFrame(rows, columns=["ts","o","h","l","c","v"]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df

def tickers(syms):
    out = {}
    r = httpx.get(f"{BASE}/v5/market/tickers", params={"category":"linear"}, timeout=12).json()
    if r.get("retCode") == 0:
        for t in r["result"]["list"]:
            if t["symbol"] in syms:
                out[t["symbol"]] = {"bid": float(t["bid1Price"]), "ask": float(t["ask1Price"]),
                                    "last": float(t["lastPrice"]),
                                    "spread_bps": (float(t["ask1Price"])-float(t["bid1Price"]))/max(float(t["lastPrice"]),1e-12)*10000}
    return out

def prep(df):
    tr = np.maximum(df["h"]-df["l"], np.maximum((df["h"]-df["c"].shift()).abs(), (df["l"]-df["c"].shift()).abs()))
    df = df.copy()
    df["atr"] = tr.rolling(14).mean()
    df["hi20"] = df["h"].shift(1).rolling(20).max()
    df["lo20"] = df["l"].shift(1).rolling(20).min()
    return df

def detect_new(df, frontier_ts):
    """Return B2 events on CLOSED bars with ts > frontier_ts. No lookahead:
    detection at bar i uses only bars <= i; the event becomes actionable only
    when bar i is closed (guaranteed by caller cutting to closed bars)."""
    evs = []
    cs, ts = df["c"].values, df["ts"].values
    ph, pl = df["hi20"].values, df["lo20"].values
    atr = df["atr"].values
    o, h, l = df["o"].values, df["h"].values, df["l"].values
    n = len(df)
    for i in range(1, n):
        t = int(ts[i])
        if t <= frontier_ts: continue
        if pd.isna(ph[i-1]) or pd.isna(pl[i-1]) or pd.isna(atr[i]): continue
        d = 0
        lvl = None
        if cs[i-1] > ph[i-1] and cs[i] < ph[i-1]:
            d, lvl = -1, float(ph[i-1])
        elif cs[i-1] < pl[i-1] and cs[i] > pl[i-1]:
            d, lvl = 1, float(pl[i-1])
        if d == 0: continue
        rng = float(h[i] - l[i])
        ev = dict(sym=None, event_ts=t, bar_i=i, dir=d,
                  breakout_level=lvl, failure_level=lvl,
                  entry_taker=float(cs[i]), sl_dist=SL_MULT*float(atr[i]),
                  atr14=float(atr[i]),
                  sig_o=float(o[i]), sig_h=float(h[i]), sig_l=float(l[i]), sig_c=float(cs[i]),
                  sig_range=rng)
        evs.append(ev)
    return evs

def virtual_outcomes(df, ev, i0):
    """Simulate forward window from the event bar (already closed at detection):
    - taker reference: entry at signal close, SL/TP LE-conservative
    - maker variants: PostOnly limit at entry_taker -/+ frac*range (with the trend side),
      fill only on strict trade-through; after fill, same SL/TP logic; timeout at horizon.
    Returns list of variant dicts."""
    n = len(df)
    h, l, c = df["h"].values, df["l"].values, df["c"].values
    ts = df["ts"].values
    d = ev["dir"]
    entry0 = ev["entry_taker"]
    sd = ev["sl_dist"]
    out = []
    end = min(i0 + 1 + HORIZON, n)
    # taker reference
    sl_px = entry0 - d * sd
    tp_px = entry0 + d * TP_R * sd
    stopped = hit = False; mfe = mae = 0.0
    for j in range(i0+1, end):
        fav = (h[j]-entry0)*d; adv = (entry0-l[j])*d
        mfe = max(mfe, fav); mae = max(mae, adv)
        st = (l[j]-sl_px)*d <= 0; tg = (h[j]-tp_px)*d >= 0
        if tg and not st: hit=True; break
        if st: stopped=True; break
    if hit: pnl = TP_R
    elif stopped: pnl = -1.0
    else: pnl = (c[end-1]-entry0)*d/sd
    out.append(dict(variant="taker", entry=entry0, filled=True,
                    pnl_R=pnl, cost_R=TAKER_COST_BPS/(sd/entry0*10000) if sd>0 else 0,
                    mfe_R=mfe/sd, mae_R=mae/sd, exit_ts=int(ts[min(i0+HORIZON, n-1)])))
    # maker variants: limit placed on the pullback side (BUY below / SELL above)
    for frac in MAKER_FRACS:
        lim = entry0 - d * frac * ev["sig_range"]
        filled = False; fill_j = None
        # fill requires trade-through: strictly beyond limit
        for j in range(i0+1, end):
            if (l[j] - lim) * d < 0:   # price traded through our limit
                filled = True; fill_j = j; break
        if not filled:
            out.append(dict(variant=f"maker{int(frac*100)}", entry=lim, filled=False,
                            pnl_R=0.0, cost_R=0.0, mfe_R=0.0, mae_R=0.0, exit_ts=None))
            continue
        sl_px = lim - d * sd
        tp_px = lim + d * TP_R * sd
        stopped = hit = False; mfe = mae = 0.0
        pnl = None
        for j in range(fill_j, end):
            fav = (h[j]-lim)*d; adv = (lim-l[j])*d
            mfe = max(mfe, fav); mae = max(mae, adv)
            st = (l[j]-sl_px)*d <= 0; tg = (h[j]-tp_px)*d >= 0
            if tg and not st: hit=True; break
            if st: stopped=True; break
        if hit: pnl = TP_R
        elif stopped: pnl = -1.0
        else: pnl = (c[end-1]-lim)*d/sd
        out.append(dict(variant=f"maker{int(frac*100)}", entry=lim, filled=True,
                        pnl_R=pnl, cost_R=(MAKER_FEE_BPS+AS_BPS)/(sd/lim*10000) if sd>0 else 0,
                        mfe_R=mfe/sd, mae_R=mae/sd, exit_ts=int(ts[end-1])))
    return out

def main():
    ledger({"evt":"service_start","mode":"b2_watch","symbols":len(SYMS)})
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
    ledger({"evt":"warmup_done","n_syms":len(series),
            "frontier_min":min(frontier.values()) if frontier else None,
            "frontier_max":max(frontier.values()) if frontier else None})
    # persist state
    STATE.write_text(json.dumps({"frontier": frontier}, indent=1))

    while True:
        t0 = time.time()
        now_ms = int(time.time()*1000)
        closed_cut = now_ms - BAR_MS
        try:
            ticks = tickers(SYMS)
        except Exception as e:
            ticks = {}
            ledger({"evt":"tickers_error","err":str(e)})
        new_events = 0
        frontier_lags = []
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
            for ev in evs:
                ev["sym"] = sym
                tk = ticks.get(sym, {})
                sl_bps = ev["sl_dist"]/ev["entry_taker"]*10000 if ev["entry_taker"] else None
                rec = {**{k:v for k,v in ev.items() if k!="bar_i"},
                       "bid": tk.get("bid"), "ask": tk.get("ask"),
                       "spread_bps": tk.get("spread_bps"),
                       "tp_px": ev["entry_taker"] + ev["dir"]*TP_R*ev["sl_dist"],
                       "sl_px": ev["entry_taker"] - ev["dir"]*ev["sl_dist"],
                       "sl_bps": sl_bps,
                       "exp_net_taker_R": (TP_R if True else 0),  # filled later by outcome
                       "risk_usd": RISK_USD}
                ledger({"evt":"b2_signal", **rec})
                # forward outcome can only be computed when future bars exist;
                # schedule via pending list evaluated on later polls
                pending.append((sym, ev["event_ts"], ev))
                new_events += 1
            if len(closed):
                frontier[sym] = max(frontier.get(sym,0), int(closed["ts"].iloc[-1]))
            series[sym] = merged
            lag_ms = now_ms - frontier.get(sym, now_ms)
            frontier_lags.append(lag_ms)
            time.sleep(0.08)
        # evaluate pending forward outcomes when horizon data available
        still = []
        for (sym, ev_ts, ev) in pending:
            df = series.get(sym)
            if df is None: still.append((sym, ev_ts, ev)); continue
            idx = df.index[df["ts"]==ev_ts]
            if not len(idx): still.append((sym, ev_ts, ev)); continue
            i0 = int(idx[0])
            if i0 + HORIZON >= len(df) - 0 and (int(df['ts'].iloc[-1]) < ev_ts + HORIZON*BAR_MS):
                still.append((sym, ev_ts, ev)); continue  # not enough forward bars yet
            outs = virtual_outcomes(df, ev, i0)
            for o in outs:
                net_R = (o["pnl_R"] - o["cost_R"]) if o["filled"] else 0.0
                ledger({"evt":"b2_outcome","sym":sym,"event_ts":ev_ts,
                        "variant":o["variant"],"filled":o["filled"],
                        "entry":o["entry"],"pnl_R":o["pnl_R"],"cost_R":o["cost_R"],
                        "net_R":net_R,"net_usd":net_R*RISK_USD,
                        "mfe_R":o["mfe_R"],"mae_R":o["mae_R"],
                        "exit_ts":o["exit_ts"]})
        pending.clear(); pending.extend(still)
        # persist frontier
        STATE.write_text(json.dumps({"frontier": frontier}, indent=1))
        ledger({"evt":"poll_heartbeat","cycle_ms":int((time.time()-t0)*1000),
                "n_syms":len(series),"new_events":new_events,
                "pending_outcomes":len(pending),
                "frontier_lag_min_ms":int(min(frontier_lags)) if frontier_lags else None,
                "frontier_lag_max_ms":int(max(frontier_lags)) if frontier_lags else None})
        time.sleep(max(5.0, POLL_SEC - (time.time()-t0)))

pending = []

if __name__ == "__main__":
    main()
