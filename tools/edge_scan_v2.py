#!/usr/bin/env python3
"""edge_scan_v2.py — быстрый scan 10 механизмов, forward-return предрасчитан векторизованно."""
import statistics, sys, json
from collections import defaultdict
from pathlib import Path
import pandas as pd
import numpy as np

CACHE = Path("/root/tradingos/replay_cache")

def load(sym):
    p = CACHE/f"{sym}_M15.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[0]>1e12: df["ts"]=df["ts"]/1000
    tr=pd.concat([(df["h"]-df["l"]),(df["h"]-df["c"].shift(1)).abs(),(df["l"]-df["c"].shift(1)).abs()],axis=1).max(axis=1)
    df["atr"]=tr.rolling(30).mean()
    df["hi40"]=df["h"].rolling(40).max().shift(1)
    df["lo40"]=df["l"].rolling(40).min().shift(1)
    df["vm"]=df["v"].rolling(60).median().shift(1)
    df["e20"]=df["c"].ewm(span=20,adjust=False).mean()
    df["e50"]=df["c"].ewm(span=50,adjust=False).mean()
    # forward returns векторизованно: shift(-k) для 1bar(15m), 4bar(1h), 16bar(4h)
    for k,h in [(1,"15m"),(4,"1h"),(16,"4h")]:
        df[f"ret_{h}"]=(df["c"].shift(-k)/df["c"]-1)*100
        df[f"maxh_{h}"]=(df["h"].rolling(k).max().shift(-k)/df["c"]-1)*100
        df[f"minl_{h}"]=(df["l"].rolling(k).min().shift(-k)/df["c"]-1)*100
    return df

syms=sorted(p.name.replace("_M15.parquet","") for p in CACHE.glob("*_M15.parquet"))
ev=defaultdict(list)
for sym in syms:
    df=load(sym)
    if df is None or len(df)<80: continue
    d=df.iloc[60:-16]  # только бары с полными future
    for _,b in d.iterrows():
        ts=float(b["ts"]); pr=float(b["c"])
        atr=float(b["atr"]) if pd.notna(b["atr"]) else 0
        if atr<=0: continue
        hi=float(b["hi40"]) if pd.notna(b["hi40"]) else pr
        lo=float(b["lo40"]) if pd.notna(b["lo40"]) else pr
        vm=float(b["vm"]) if pd.notna(b["vm"]) else float(b["v"])
        e20=float(b["e20"]); e50=float(b["e50"])
        f={h:{"r":float(b[f"ret_{h}"]),"m":float(b[f"maxh_{h}"]),"a":float(b[f"minl_{h}"])}
           for h in ("15m","1h","4h")}
        if any(pd.isna(f[h]["r"]) for h in f): continue
        base={"s":sym,"f":f}; dev=(pr-e20)/atr

        if pr>hi: ev["A_TREND_CONT"].append(dict(base,d="LONG"))
        if pr<lo: ev["A_TREND_CONT"].append(dict(base,d="SHORT"))
        if b["h"]>hi and pr<hi: ev["B_LIQ_SWEEP"].append(dict(base,d="LONG_REV"))
        if b["l"]<lo and pr>lo: ev["B_LIQ_SWEEP"].append(dict(base,d="SHORT_REV"))
        if dev>1.5: ev["C_MEAN_REV"].append(dict(base,d="SHORT_REV"))
        if dev<-1.5: ev["C_MEAN_REV"].append(dict(base,d="LONG_REV"))
        if b["v"]>2.5*vm: ev["I_VOL_IMBAL"].append(dict(base,d="LONG" if b["c"]>b["o"] else "SHORT"))
        if (b["h"]-b["l"])>3*atr and b["v"]>2*vm: ev["H_LIQ_STRESS"].append(dict(base,d="LONG" if b["c"]>b["o"] else "SHORT"))
        rg="TREND" if e20>e50 else "RANGE"
        if rg=="TREND" and pr>hi: ev["J_TREND_BRK"].append(dict(base,d="LONG"))
        if rg=="RANGE" and dev<-1.2: ev["J_RANGE_REV"].append(dict(base,d="LONG_REV"))

print(f"Symbols={len(syms)} events={sum(len(v) for v in ev.values())}",flush=True)
print(f"{'MECH':16s} {'N':>6s} {'dir1h%':>8s} {'dir4h%':>8s} {'MFE1h':>7s} {'MAE1h':>7s} {'pos4h%':>7s} {'top1%':>7s} {'noTop10%':>9s}",flush=True)
print("-"*90,flush=True)
for mech in sorted(ev):
    lst=ev[mech]
    if len(lst)<20: continue
    d1=[x["f"]["1h"]["r"]*(1 if x["d"].startswith("LONG") else -1) for x in lst]
    d4=[x["f"]["4h"]["r"]*(1 if x["d"].startswith("LONG") else -1) for x in lst]
    mfe=[x["f"]["1h"]["m"]*(1 if x["d"].startswith("LONG") else -1) for x in lst]
    mae=[x["f"]["1h"]["a"]*(1 if x["d"].startswith("LONG") else -1) for x in lst]
    pos4=sum(1 for v in d4 if v>0)/len(d4)*100
    bys=defaultdict(list)
    for x,v in zip(lst,d4): bys[x["s"]].append(v)
    sm={s:statistics.mean(v) for s,v in bys.items()}
    tot=sum(sm.values()); top1=max(sm.values())/tot*100 if tot else 0
    cut=sorted(d1)[:max(1,int(len(d1)*0.9))]; rob=statistics.mean(cut)
    print(f"{mech:16s} {len(lst):>6d} {statistics.mean(d1):>+8.3f} {statistics.mean(d4):>+8.3f} "
          f"{statistics.mean(mfe):>+7.2f} {statistics.mean(mae):>+7.2f} {pos4:>6.1f}% {top1:>6.0f}% {rob:>+9.3f}",flush=True)
print("\nDONE",flush=True)
