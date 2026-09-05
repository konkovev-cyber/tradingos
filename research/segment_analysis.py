#!/usr/bin/env python3
"""
segment_analysis.py — стабильность direction-edge по времени и классам.

Читает кэш H1 parquet (уже скачан direction_edge.py), строит признаки,
и разбивает выборку:
  1. По времени: 3 хронологических сегмента (walk-forward proxy)
  2. По классу: crypto vs tokenized stocks
  3. Night vs day (UTC 00-08 / 08-16 / 16-24)

Для каждого бакета — взвешенный fwd return и t-статистика по сегментам.
Если edge есть, он должен быть стабилен в нескольких сегментах.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"

CRYPTO = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "ADAUSDT", "NEARUSDT", "SUIUSDT", "LINKUSDT", "AAVEUSDT", "AVAXUSDT",
    "UNIUSDT", "LTCUSDT", "OPUSDT", "WLDUSDT", "APTUSDT", "ONDOUSDT",
}
# Остальные из списка = акции

BUCKET_DEFS = {
    "ALL": lambda d: pd.Series(True, index=d.index),
    "H1_UP_STRICT": lambda d: d["h1_up"],
    "H1_DOWN_STRICT": lambda d: d["h1_down"],
    "SCANNER_LIKE_LONG": lambda d: d["h1_up"] & d["mom_up"] & (d["dist_e20_pct"].abs() <= 0.35),
    "SCANNER_LIKE_SHORT": lambda d: d["h1_down"] & (~d["mom_up"]) & (d["dist_e20_pct"].abs() <= 0.35),
    "PULLBACK_MATURE_UP": lambda d: d["pullback_mature"] & d["h1_up"],
    "RSI<40": lambda d: d["rsi"] < 40,
    "RSI>60": lambda d: d["rsi"] > 60,
    "BELOW_EMA20": lambda d: d["dist_e20_pct"] < -0.5,
    "ABOVE_EMA20": lambda d: d["dist_e20_pct"] > 0.5,
    "VOL>1.5x": lambda d: d["vol_ratio"] > 1.5,
}


def _rsi_wilder(closes: list[float], n: int = 14) -> pd.Series:
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_g = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_g / avg_l.replace(0.0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50.0)


def build(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema20"] = d["c"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["c"].ewm(span=50, adjust=False).mean()
    d["rsi"] = _rsi_wilder(d["c"].tolist())
    d["dist_e20_pct"] = (d["c"] - d["ema20"]) / d["ema20"] * 100
    d["vol_ratio"] = d["v"] / d["v"].rolling(21).mean().shift(1)
    d["h1_up"] = (d["c"] > d["ema20"]) & (d["ema20"] > d["ema50"])
    d["h1_down"] = (d["c"] < d["ema20"]) & (d["ema20"] < d["ema50"])
    d["mom_up"] = d["rsi"] > 50
    d["up_bar"] = d["c"] > d["c"].shift(1)
    d["up_in_6"] = d["up_bar"].rolling(6).sum()
    d["straight_down"] = (d["c"] <= d["c"].shift(1)) & \
                         (d["c"].shift(1) <= d["c"].shift(2)) & \
                         (d["c"].shift(2) <= d["c"].shift(3)) & \
                         (d["c"].shift(3) <= d["c"].shift(4))
    d["pullback_mature"] = (~d["straight_down"]) & (d["up_in_6"] >= 2)
    for k in (1, 2, 5, 10):
        d[f"fwd_{k}"] = d["c"].shift(-k) / d["c"] - 1.0
    d["valid"] = d["fwd_10"].notna() & d["ema50"].notna()
    d["hour"] = pd.to_datetime(d["ts"], unit="s", utc=True).dt.hour
    return d


def tstat(vals: pd.Series) -> float:
    vals = vals.dropna()
    n = len(vals)
    if n < 30:
        return 0.0
    m = vals.mean()
    se = vals.std(ddof=1) / math.sqrt(n)
    return m / se if se > 0 else 0.0


def main() -> None:
    frames: list[pd.DataFrame] = []
    for cache in sorted(CACHE_DIR.glob("*USDT_H1.parquet")):
        sym = cache.name.replace("_H1.parquet", "")
        df = pd.read_parquet(cache)
        if len(df) < 400:
            continue
        d = build(df)
        d["symbol"] = sym
        d["cls"] = "crypto" if sym in CRYPTO else "stock"
        frames.append(d[d["valid"]])
    if not frames:
        print("Нет кэша — сначала запусти direction_edge.py")
        sys.exit(1)
    data = pd.concat(frames, ignore_index=True)
    print(f"Всего наблюдений: {len(data)} | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # Хронологические сегменты (по ts от глобального минимума)
    t_min, t_max = data["ts"].min(), data["ts"].max()
    seg_edges = [t_min + (t_max - t_min) * i / 3 for i in range(4)]
    seg_names = ["SEG1(older)", "SEG2", "SEG3(newer)"]

    per_seg = {}
    for i in range(3):
        mask = (data["ts"] >= seg_edges[i]) & (data["ts"] < seg_edges[i + 1])
        per_seg[seg_names[i]] = data[mask]

    for cls in ("crypto", "stock"):
        cls_data = data[data["cls"] == cls]
        print(f"\n{'='*92}\nАгрегированный edge по классу: {cls} (n={len(cls_data)})\n{'='*92}")
        _print_buckets(cls_data)

    print(f"\n{'='*92}\nСтабильность по времени (все классы вместе)\n{'='*92}")
    for seg_name, seg in per_seg.items():
        print(f"\n--- {seg_name} (n={len(seg)}) ---")
        _print_buckets(seg)

    # Формат для walk-forward: тренд/рейндж + ночь/день
    print(f"\n{'='*92}\nВремя суток (UTC)\n{'='*92}")
    for band, mask in (
        ("00-08", data["hour"] < 8),
        ("08-16", (data["hour"] >= 8) & (data["hour"] < 16)),
        ("16-24", data["hour"] >= 16),
    ):
        seg = data[mask]
        print(f"\n--- {band} UTC (n={len(seg)}) ---")
        _print_buckets(seg)


def _print_buckets(d: pd.DataFrame) -> None:
    print(f"{'bucket':<26}{'n':>9}{'fwd1%':>9}{'t1':>7}{'fwd5%':>9}{'t5':>7}{'fwd10%':>10}{'t10':>7}")
    for name, fn in BUCKET_DEFS.items():
        try:
            mask = fn(d)
        except Exception:
            continue
        sub = d[mask]
        n = len(sub)
        if n < 30:
            continue
        f1, f5, f10 = sub["fwd_1"].mean() * 100, sub["fwd_5"].mean() * 100, sub["fwd_10"].mean() * 100
        print(f"{name:<26}{n:>9}{f1:>9.4f}{tstat(sub['fwd_1']):>7.2f}{f5:>9.4f}{tstat(sub['fwd_5']):>7.2f}{f10:>10.4f}{tstat(sub['fwd_10']):>7.2f}")


if __name__ == "__main__":
    main()