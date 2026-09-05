#!/usr/bin/env python3
"""
entry_replay.py — read-only статистический реплей: какой тип входа система
реально генерировала по signal_log, и как выглядело качество точки входа.

НЕ торговая логика, НЕ меняет ничего. Только анализ истории.
Для каждого направленного сигнала (prob>=0.55) по 15m-свечам ДО момента
вычисляет: фазу движения, время от импульса, расстояние от начала импульса
(в ATR), follow-through, пространство до ближайшего уровня.

Вывод: распределение фаз входа по всем сигналам + сводка.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path("/root/tradingos")
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"

# Кэш свечей по символу: {sym: [bar, ...]} — 15m, до ~6.25 дней
_CACHE: dict[str, list[dict]] = {}


def _load_klines(client: httpx.Client, symbol: str, limit: int = 600) -> list[dict]:
    if symbol in _CACHE:
        return _CACHE[symbol]
    r = client.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": "15", "limit": limit},
        timeout=15,
    )
    rows = (r.json().get("result") or {}).get("list") or []
    out = []
    for b in rows:
        ts = int(b[0])
        ts = ts / 1000 if ts > 1e12 else ts
        out.append({"ts": ts, "o": float(b[1]), "h": float(b[2]),
                    "l": float(b[3]), "c": float(b[4]), "v": float(b[5])})
    out.sort(key=lambda x: x["ts"])
    _CACHE[symbol] = out
    return out


def _atr14(bars: list[dict]) -> float:
    trs = []
    for i in range(1, len(bars)):
        tr = max(bars[i]["h"] - bars[i]["l"],
                 abs(bars[i]["h"] - bars[i - 1]["c"]),
                 abs(bars[i]["l"] - bars[i - 1]["c"]))
        trs.append(tr)
    return sum(trs[-14:]) / 14 if len(trs) >= 14 else 0.0


def classify_entry(bars: list[dict], entry_ts: float, side: str, price: float) -> dict:
    """Классифицировать точку входа по свечам до неё. Возвращает метрики."""
    pre = [b for b in bars if b["ts"] <= entry_ts - 60]
    if len(pre) < 40:
        return {"phase": "NO_DATA"}
    atr = _atr14(pre)
    if atr <= 0:
        return {"phase": "NO_DATA"}

    win = pre[-24:]  # 6 часов до входа
    vols = [b["v"] for b in win]
    v_avg = sum(vols) / len(vols) if vols else 0
    fav = "UP" if side == "BUY" else "DOWN"

    # импульс в НАПРАВЛЕНИИ входа: бар с объёмом >2.2x и движением в fav
    imp_ts = imp_open = None
    for b in win:
        if b["v"] > 2.2 * v_avg and b["c"] != b["o"]:
            up = b["c"] > b["o"]
            if (fav == "UP" and up) or (fav == "DOWN" and not up):
                imp_ts = b["ts"]
                imp_open = b["o"]
                break

    mins_since = (entry_ts - imp_ts) / 60 if imp_ts else None
    dist_from_origin = abs(price - imp_open) / atr if imp_open else None

    # follow-through: двигалась ли цена в fav с момента импульса до входа
    follow = None
    if imp_ts:
        after = [b for b in win if b["ts"] > imp_ts]
        if after:
            if fav == "UP":
                follow = after[-1]["c"] > after[0]["c"]
            else:
                follow = after[-1]["c"] < after[0]["c"]

    # ближайший уровень (экстремум последних 24 баров)
    lo = min(b["l"] for b in win)
    hi = max(b["h"] for b in win)
    if fav == "UP":
        space_to_level = (hi - price) / atr if hi > price else 0.0
        dist_from_ext = (price - lo) / atr
    else:
        space_to_level = (price - lo) / atr if price > lo else 0.0
        dist_from_ext = (hi - price) / atr

    # Фаза
    if not imp_ts:
        phase = "NO_FAV_IMPULSE"
    elif mins_since > 60 and follow is False:
        phase = "EXHAUSTION"
    elif mins_since > 60:
        phase = "LATE_MOMENTUM"
    elif mins_since <= 60 and follow is True:
        phase = "ACTIVE_MOMENTUM"
    else:
        phase = "EARLY_IMPULSE"

    return {
        "phase": phase,
        "mins_since_impulse": int(mins_since) if mins_since is not None else None,
        "dist_from_origin_atr": round(dist_from_origin, 2) if dist_from_origin is not None else None,
        "follow_through": follow,
        "dist_from_ext_atr": round(dist_from_ext, 2),
        "space_to_level_atr": round(space_to_level, 2),
        "atr": round(atr, 8),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-prob", type=float, default=0.55)
    ap.add_argument("--limit", type=int, default=0, help="0 = все")
    ap.add_argument("--symbols", type=str, default="", help="csv, пусто = все")
    args = ap.parse_args()

    rows = [json.loads(l) for l in SIGNAL_LOG.open()]
    sigs = [r for r in rows
            if r.get("direction") in ("BUY", "SELL")
            and (r.get("final_probability") or 0) >= args.min_prob]
    if args.symbols:
        allowed = set(args.symbols.split(","))
        sigs = [r for r in sigs if r.get("symbol") in allowed]
    if args.limit:
        sigs = sigs[: args.limit]

    print(f"Сигналов для реплея: {len(sigs)} (prob>={args.min_prob}, направленных)")
    from collections import Counter
    phases = Counter()
    stats = {"n": 0, "late": 0, "no_room": 0}

    with httpx.Client() as client:
        for i, s in enumerate(sigs):
            sym = s["symbol"]
            ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")).timestamp()
            bars = _load_klines(client, sym)
            res = classify_entry(bars, ts, s["direction"], s.get("close") or s.get("entry") or 0)
            if res.get("phase") == "NO_DATA":
                continue
            phases[res["phase"]] += 1
            stats["n"] += 1
            if res["phase"] in ("LATE_MOMENTUM", "EXHAUSTION"):
                stats["late"] += 1
            if (res.get("space_to_level_atr") or 99) < 1.5:
                stats["no_room"] += 1
            if (i + 1) % 200 == 0:
                print(f"  ...обработано {i+1}")

    print("\n=== РАСПРЕДЕЛЕНИЕ ФАЗ ВХОДА (реплей по history) ===")
    total = stats["n"] or 1
    for ph, cnt in phases.most_common():
        print(f"  {ph:20s} {cnt:6d} ({cnt/total*100:5.1f}%)")
    print(f"\nВсего оценено: {stats['n']}")
    print(f"  LATE_MOMENTUM+EXHAUSTION (вход после импульса): {stats['late']} ({stats['late']/total*100:.1f}%)")
    print(f"  space_to_level < 1.5 ATR (мало пространства):    {stats['no_room']} ({stats['no_room']/total*100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
