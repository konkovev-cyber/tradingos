#!/usr/bin/env python3
"""
recent_move_audit.py — READ-ONLY аудит: классификация RECENT MOVE для сигналов.

Для каждого сильного кандидата (prob>=0.55) определяем, что рынок сделал
НЕПОСРЕДСТВЕННО перед входом, по данным ДО сигнала (без look-ahead):

Классы:
  OPPOSITE_IMPULSE — сильный импульс ПРОТИВ направления входа, нет reversal
  LATE_MOMENTUM    — импульс В НАПРАВЛЕНИИ входа, но старый/без follow-through
  REVERSAL         — подтверждённый разворот против импульса
  CONTINUATION     — импульс в сторону входа, ещё активен
  NORMAL           — нет значимого движения

Метрики по классам: N, forward 1h/4h return (directional), MFE/MAE, win%.
Никакого кода в live. Только статистика по существующим сигналам.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"


def load_candles(symbol: str) -> pd.DataFrame | None:
    p = CACHE_DIR / f"{symbol}_M15.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[0] > 1e12:
        df["ts"] = df["ts"] / 1000
    tr = pd.concat([
        (df["h"] - df["l"]),
        (df["h"] - df["c"].shift(1)).abs(),
        (df["l"] - df["c"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(30).mean()
    df["vm"] = df["v"].rolling(60).median().shift(1)
    return df


def recent_move(df: pd.DataFrame, ts: float, side: str) -> dict:
    """Классифицировать движение рынка непосредственно перед ts."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 60:
        return {"class": "NO_DATA", "age": None, "strength": None}
    b = pre.iloc[-1]
    price = float(b["c"])
    atr = float(b["atr"]) if pd.notna(b["atr"]) else 0.0
    vm = float(b["vm"]) if pd.notna(b["vm"]) else float(b["v"])
    if atr <= 0:
        return {"class": "NO_DATA", "age": None, "strength": None}

    # Окно свежести: до 4 баров
    win = pre.tail(4)
    # Находим самый сильный направленный бар в окне
    best = None
    for _, wb in win.iterrows():
        if wb["v"] <= 1.0 * vm:  # без объёма — не импульс
            continue
        body = abs(wb["c"] - wb["o"])
        rng = wb["h"] - wb["l"]
        if rng <= 0:
            continue
        disp = abs(wb["c"] - wb["o"]) / atr
        rel_v = wb["v"] / vm if vm > 0 else 1
        # сила: комбинация displacement + body/range + объёма
        strength = disp * rel_v
        if best is None or strength > best["strength"]:
            direction = "UP" if wb["c"] > wb["o"] else "DOWN"
            best = {"age": (ts - wb["ts"]) / 60, "direction": direction,
                    "strength": strength, "disp": disp, "rel_v": rel_v,
                    "body_ratio": body / rng}

    if best is None:
        return {"class": "NORMAL", "age": None, "strength": None}

    age = int(best["age"])
    direction = best["direction"]
    # Значимый импульс: displacement > 0.8 ATR + rel_v > 1.5 + body>0.5 range
    strong = best["disp"] > 0.8 and best["rel_v"] > 1.5 and best["body_ratio"] > 0.5
    fav = "UP" if side == "BUY" else "DOWN"

    if strong:
        if direction != fav:
            # импульс ПРОТИВ входа: проверяем reversal (следующие бары после импульса)
            imp_ts = best_age_ts(pre, best["age"], ts)
            rev = check_reversal(df, imp_ts, ts, side)
            cls = "REVERSAL" if rev else "OPPOSITE_IMPULSE"
            return {"class": cls, "age": age, "strength": round(best["strength"], 2),
                    "disp": round(best["disp"], 2), "rel_v": round(best["rel_v"], 1)}
        else:
            # импульс в сторону входа
            if age <= 60:
                return {"class": "CONTINUATION", "age": age, "strength": round(best["strength"], 2),
                        "disp": round(best["disp"], 2), "rel_v": round(best["rel_v"], 1)}
            return {"class": "LATE_MOMENTUM", "age": age, "strength": round(best["strength"], 2),
                    "disp": round(best["disp"], 2), "rel_v": round(best["rel_v"], 1)}
    return {"class": "NORMAL", "age": age, "strength": round(best["strength"], 2),
            "disp": round(best["disp"], 2), "rel_v": round(best["rel_v"], 1)}


def best_age_ts(pre: pd.DataFrame, age_min: float, ts: float) -> float:
    """Временная метка бара-импульса."""
    return ts - age_min * 60


def check_reversal(df: pd.DataFrame, imp_ts: float, entry_ts: float, side: str) -> bool:
    """Проверить, есть ли подтверждённый разворот ПОСЛЕ импульса и ДО входа."""
    between = df[(df["ts"] > imp_ts + 60) & (df["ts"] <= entry_ts - 60)]
    if len(between) < 2:
        return False
    # Разворот: после UP-импульса цена сделала lower-high (для SELL входа)
    # или после DOWN-импульса — higher-low (для BUY)
    if side == "SELL":
        # после UP импульса ждём lower high или пробой вниз
        first_hi = between["h"].iloc[0]
        last = between.iloc[-1]
        return last["h"] < first_hi or last["c"] < last["o"]
    else:
        first_lo = between["l"].iloc[0]
        last = between.iloc[-1]
        return last["l"] > first_lo or last["c"] > last["o"]


def future(df: pd.DataFrame, ts: float, price: float, hours: float) -> dict | None:
    f = df[(df["ts"] > ts + 60) & (df["ts"] <= ts + hours * 3600)]
    if len(f) == 0 or price <= 0:
        return None
    return {"r": (f["c"].iloc[-1] - price) / price * 100,
            "m": (f["h"].max() - price) / price * 100,
            "a": (f["l"].min() - price) / price * 100}


def main() -> int:
    rows = [json.loads(l) for l in SIGNAL_LOG.open()]
    sigs = [r for r in rows if r.get("direction") in ("BUY", "SELL")
            and (r.get("final_probability") or 0) >= 0.55]
    sigs.sort(key=lambda r: r["timestamp"])

    cache: dict = {}
    stats = defaultdict(list)
    class_n = defaultdict(int)
    for s in sigs:
        sym = s["symbol"]
        if sym not in cache:
            cache[sym] = load_candles(sym)
        df = cache[sym]
        if df is None:
            continue
        ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")).timestamp()
        rm = recent_move(df, ts, s["direction"])
        cls = rm["class"]
        class_n[cls] += 1
        price = s.get("close") or s.get("entry") or 0
        f1 = future(df, ts, price, 1)
        f4 = future(df, ts, price, 4)
        if f1 is None or f4 is None:
            continue
        sign = 1 if s["direction"] == "BUY" else -1
        stats[cls].append({"r1": f1["r"] * sign, "r4": f4["r"] * sign,
                           "m": f1["m"] * sign, "a": f1["a"] * sign})

    print(f"Сигналов: {len(sigs)}")
    print("\n=== RECENT MOVE КЛАССЫ ===")
    for cls in ["OPPOSITE_IMPULSE", "LATE_MOMENTUM", "REVERSAL", "CONTINUATION", "NORMAL", "NO_DATA"]:
        print(f"  {cls:18s} {class_n.get(cls, 0):>5d}")
    print("\n=== OUTCOMES ПО КЛАССАМ (directional 1h) ===")
    hdr = (f"{'CLASS':18s} {'N':>5s} {'r1h%':>7s} {'r4h%':>7s} {'MFE1h':>6s} {'MAE1h':>6s} {'win%':>6s}")
    print(hdr)
    print("-" * 60)
    for cls in ["OPPOSITE_IMPULSE", "LATE_MOMENTUM", "REVERSAL", "CONTINUATION", "NORMAL"]:
        lst = stats.get(cls, [])
        if not lst:
            continue
        n = len(lst)
        r1 = statistics.mean(x["r1"] for x in lst)
        r4 = statistics.mean(x["r4"] for x in lst)
        m = statistics.mean(x["m"] for x in lst)
        a = statistics.mean(x["a"] for x in lst)
        w = sum(1 for x in lst if x["r1"] > 0) / n * 100
        print(f"{cls:18s} {n:>5d} {r1:>+7.3f} {r4:>+7.3f} {m:>+6.2f} {a:>+6.2f} {w:>5.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
