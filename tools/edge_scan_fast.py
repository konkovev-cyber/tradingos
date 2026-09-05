#!/usr/bin/env python3
"""edge_scan_fast.py — быстрый scan 10 механизмов, без permutation."""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path
import pandas as pd

CACHE = Path("/root/tradingos/replay_cache")
H = {"15m":0.25,"1h":1.0,"4h":4.0}

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
    return df

def fut(df,ts,price,hrs):
    f=df[(df["ts"]>ts+60)&(df["ts"]<=ts+hrs*3600)]
    if len(f)==0 or price<=0: return {}
    return {"r":(f["c"].iloc[-1]-price)/price*100,"m":(f["h"].max()-price)/price*100,"a":(f["l"].min()-price)/price*100}

syms=sorted(p.name.replace("_M15.parquet","") for p in CACHE.glob("*_M15.parquet"))
ev=defaultdict(list)
for sym in syms:
    df=load(sym)
    if df is None or len(df)<80: continue
    for i in range(60,len(df)-1):
        b=df.iloc[i]; ts=float(b["ts"]); pr=float(b["c"]); atr=float(b["atr"]) if pd.notna(b["atr"]) else 0
        if atr<=0: continue
        hi=float(b["hi40"]) if pd.notna(b["hi40"]) else pr
        lo=float(b["lo40"]) if pd.notna(b["lo40"]) else pr
        vm=float(b["vm"]) if pd.notna(b["vm"]) else float(b["v"])
        e20=float(b["e20"]); e50=float(b["e50"])
        f={h:fut(df,ts,pr,hh) for h,hh in H.items()}
        if not all(f.values()): continue
        base={"s":sym,"f":f}; dev=(pr-e20)/atr

        if pr>hi: ev["A_TREND_CONT"].append(dict(base,d="LONG"))
        if pr<lo: ev["A_TREND_CONT"].append(dict(base,d="SHORT"))
        if b["h"]>hi and pr<hi: ev["B_LIQ_SWEEP"].append(dict(base,d="LONG_REV"))
        if b["l"]<lo and pr>lo: ev["B_LIQ_SWEEP"].append(dict(base,d="SHORT_REV"))
        if dev>1.5: ev["C_MEAN_REV"].append(dict(base,d="SHORT_REV"))
        if dev<-1.5: ev["C_MEAN_REV"].append(dict(base,d="LONG_REV"))
        if b["v"]>2.5*vm:
            ev["I_VOL_IMBAL"].append(dict(base,d="LONG" if b["c"]>b["o"] else "SHORT"))
        if (b["h"]-b["l"])>3*atr and b["v"]>2*vm:
            ev["H_LIQ_STRESS"].append(dict(base,d="LONG" if b["c"]>b["o"] else "SHORT"))
        rg="TREND" if e20>e50 else "RANGE"
        if rg=="TREND" and pr>hi: ev["J_TREND_BREAK"].append(dict(base,d="LONG"))
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
