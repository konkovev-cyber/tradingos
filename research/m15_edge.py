#!/usr/bin/env python3
"""
m15_edge.py — Direction-edge на M15-масштабе (горизонт 0.25–4ч).

Продолжение direction_edge.py: те же признаки, но на M15 барах.
Forward return на +1/+4/+8/+16 M15 баров (15м/1ч/2ч/4ч).

READ-ONLY, кэш в replay_cache/{sym}_M15.parquet.
"""
from __future__ import annotations

import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
OUT = ROOT / "memory" / "research" / "direction_edge"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "ADAUSDT", "NEARUSDT", "SUIUSDT", "LINKUSDT", "AAVEUSDT", "AVAXUSDT",
    "UNIUSDT", "LTCUSDT", "OPUSDT", "WLDUSDT", "APTUSDT", "ONDOUSDT",
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "AMZNUSDT", "MSFTUSDT", "METAUSDT",
    "GOOGLUSDT", "NFLXUSDT", "INTCUSDT",
]
LIMIT_PER_REQ = 1000
TARGET = 6000  # ~62 дня M15


def _rsi_wilder(closes, n=14):
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_g = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_g / avg_l.replace(0.0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50.0)


def fetch(sym: str, client: httpx.Client) -> pd.DataFrame:
    cache = CACHE_DIR / f"{sym}_M15full.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        if len(df) >= TARGET:
            return df
    rows = []
    end = int(time.time() * 1000)
    while len(rows) < TARGET:
        r = client.get("https://api.bybit.com/v5/market/kline",
                       params={"category": "linear", "symbol": sym,
                               "interval": "15", "limit": LIMIT_PER_REQ, "end": end},
                       timeout=20)
        data = r.json()
        if data.get("retCode") != 0:
            break
        lst = (data.get("result") or {}).get("list") or []
        if not lst:
            break
        rows.extend(lst)
        oldest = min(int(x[0]) for x in lst)
        if len(lst) < LIMIT_PER_REQ:
            break
        end = oldest - 1
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v", "turnover"])
    for col in ("o", "h", "l", "c", "v"):
        df[col] = df[col].astype(float)
    df["ts"] = df["ts"].astype("int64") // 1000
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True).iloc[-TARGET:]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def build(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema20"] = d["c"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["c"].ewm(span=50, adjust=False).mean()
    d["rsi"] = _rsi_wilder(d["c"].tolist())
    d["dist_e20_pct"] = (d["c"] - d["ema20"]) / d["ema20"] * 100
    d["vol_ratio"] = d["v"] / d["v"].rolling(21).mean().shift(1)
    d["up"] = (d["c"] > d["ema20"]) & (d["ema20"] > d["ema50"])
    d["down"] = (d["c"] < d["ema20"]) & (d["ema20"] < d["ema50"])
    d["mom_up"] = d["rsi"] > 50
    d["up_bar"] = d["c"] > d["c"].shift(1)
    d["up_in_6"] = d["up_bar"].rolling(6).sum()
    d["straight_down"] = (d["c"] <= d["c"].shift(1)) & (d["c"].shift(1) <= d["c"].shift(2)) & \
                         (d["c"].shift(2) <= d["c"].shift(3)) & (d["c"].shift(3) <= d["c"].shift(4))
    d["pullback_mature"] = (~d["straight_down"]) & (d["up_in_6"] >= 2)
    for k in (1, 4, 8, 16):
        d[f"fwd_{k}"] = d["c"].shift(-k) / d["c"] - 1.0
    d["valid"] = d["fwd_16"].notna() & d["ema50"].notna()
    return d


def tstat(vals: pd.Series) -> float:
    vals = vals.dropna()
    n = len(vals)
    if n < 30:
        return 0.0
    m = vals.mean()
    se = vals.std(ddof=1) / math.sqrt(n)
    return m / se if se > 0 else 0.0


BUCKETS = {
    "ALL": lambda d: pd.Series(True, index=d.index),
    "UP_STRICT": lambda d: d["up"],
    "DOWN_STRICT": lambda d: d["down"],
    "SCANNER_LIKE_LONG": lambda d: d["up"] & d["mom_up"] & (d["dist_e20_pct"].abs() <= 0.35),
    "SCANNER_LIKE_SHORT": lambda d: d["down"] & (~d["mom_up"]) & (d["dist_e20_pct"].abs() <= 0.35),
    "PULLBACK_MATURE_UP": lambda d: d["pullback_mature"] & d["up"],
    "RSI<40": lambda d: d["rsi"] < 40,
    "RSI>60": lambda d: d["rsi"] > 60,
    "VOL>1.5x": lambda d: d["vol_ratio"] > 1.5,
}


def main() -> None:
    frames = []
    with httpx.Client() as client:
        for i, sym in enumerate(SYMBOLS, 1):
            print(f"[{i}/{len(SYMBOLS)}] {sym}...", flush=True)
            try:
                df = fetch(sym, client)
                if df.empty or len(df) < 2000:
                    print(f"  insufficient ({len(df)})")
                    continue
                d = build(df)
                d["symbol"] = sym
                frames.append(d[d["valid"]])
            except Exception as e:
                print(f"  ERROR: {e}")
    if not frames:
        print("no data")
        return
    data = pd.concat(frames, ignore_index=True)
    print(f"\nВсего наблюдений M15: {len(data)}")

    print(f"\n{'='*92}\nM15 DIRECTION EDGE (aggregated, all classes)\n{'='*92}")
    print(f"{'bucket':<26}{'n':>10}{'fwd+1(15m)%':>13}{'t':>7}{'fwd+4(1h)%':>12}{'t':>7}{'fwd+16(4h)%':>12}{'t':>7}")
    for name, fn in BUCKETS.items():
        sub = data[fn(data)]
        n = len(sub)
        if n < 30:
            continue
        f1 = sub["fwd_1"].mean() * 100
        f4 = sub["fwd_4"].mean() * 100
        f16 = sub["fwd_16"].mean() * 100
        print(f"{name:<26}{n:>10}{f1:>13.4f}{tstat(sub['fwd_1']):>7.2f}{f4:>12.4f}{tstat(sub['fwd_4']):>7.2f}{f16:>12.4f}{tstat(sub['fwd_16']):>7.2f}")

    # Сегменты по времени
    t_min, t_max = data["ts"].min(), data["ts"].max()
    edges = [t_min + (t_max - t_min) * i / 3 for i in range(4)]
    print(f"\n{'='*92}\nСтабильность по времени (SCANNER_LIKE)\n{'='*92}")
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        seg = data[(data["ts"] >= a) & (data["ts"] < b)]
        sl = seg[BUCKETS["SCANNER_LIKE_LONG"](seg)]
        ss = seg[BUCKETS["SCANNER_LIKE_SHORT"](seg)]
        f1l, f4l = sl["fwd_1"].mean() * 100 if len(sl) else 0, sl["fwd_4"].mean() * 100 if len(sl) else 0
        f1s, f4s = ss["fwd_1"].mean() * 100 if len(ss) else 0, ss["fwd_4"].mean() * 100 if len(ss) else 0
        print(f"SEG{i+1} (n={len(seg)}): LONG n={len(sl)} fwd1={f1l:+.4f} t={tstat(sl['fwd_1']) if len(sl) else 0:.2f} fwd4={f4l:+.4f} | "
              f"SHORT n={len(ss)} fwd1={f1s:+.4f} t={tstat(ss['fwd_1']) if len(ss) else 0:.2f} fwd4={f4s:+.4f}")

    OUT.mkdir(parents=True, exist_ok=True)
    data.to_parquet(OUT / "m15_all.parquet")
    print(f"\nRAW: {OUT / 'm15_all.parquet'}")


if __name__ == "__main__":
    main()