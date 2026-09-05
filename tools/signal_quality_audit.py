#!/usr/bin/env python3
"""
signal_quality_audit.py — SIGNAL QUALITY AUDIT v1 (read-only, локальный кэш).

Один аналитический проход: для каждого сильного сигнала считаем признаки и
будущее движение (MFE/MAE на 4h/8h/24h), затем агрегируем по разбиениям
с IS/OOS (последние 30% по времени) раздельно.

Цель: найти признаки, связанные с последующим движением, и проверить их OOS.
НЕ трогает live. НЕ новый replay.
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

HORIZONS = [4, 8, 24]


def _load_candles(symbol: str) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{symbol}_M15.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[0] > 1e12:
        df["ts"] = df["ts"] / 1000
    return df


def _atr_before(df: pd.DataFrame, ts: float, n: int = 30) -> float:
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < n + 1:
        return 0.0
    w = pre.tail(n + 1)
    tr = pd.concat([
        (w["h"] - w["l"]),
        (w["h"] - w["c"].shift(1)).abs(),
        (w["l"] - w["c"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def _phase(df: pd.DataFrame, ts: float, side: str, price: float) -> str:
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 30:
        return "NO_DATA"
    atr = _atr_before(df, ts)
    if atr <= 0:
        return "NO_DATA"
    win = pre.tail(24)
    v_avg = float(win["v"].mean()) if len(win) else 0
    fav = "UP" if side == "BUY" else "DOWN"
    imp_ts = None
    for _, b in win.iterrows():
        if b["v"] > 2.2 * v_avg and b["c"] != b["o"]:
            up = b["c"] > b["o"]
            if (fav == "UP" and up) or (fav == "DOWN" and not up):
                imp_ts = b["ts"]
                break
    if not imp_ts:
        return "NO_FAV_IMPULSE"
    mins_since = int((ts - imp_ts) / 60)
    after = win[win["ts"] > imp_ts]
    follow = None
    if len(after) >= 2:
        follow = (after["c"].iloc[-1] > after["c"].iloc[0]) if fav == "UP" \
            else (after["c"].iloc[-1] < after["c"].iloc[0])
    if mins_since > 60 and follow is False:
        return "EXHAUSTION"
    if mins_since > 60:
        return "LATE_MOMENTUM"
    if mins_since <= 60 and follow is True:
        return "ACTIVE_MOMENTUM"
    return "EARLY_IMPULSE"


def _future_move(df: pd.DataFrame, ts: float, side: str, price: float) -> dict:
    fut = df[df["ts"] > ts + 60]
    if len(fut) == 0 or price <= 0:
        return {}
    out = {}
    base = price
    hi = lo = None
    for _, b in fut.iterrows():
        h_off = (b["ts"] - ts) / 3600
        if h_off <= HORIZONS[-1] + 1:
            hi = max(hi, b["h"]) if hi is not None else b["h"]
            lo = min(lo, b["l"]) if lo is not None else b["l"]
    if hi is None:
        return {}
    for h in HORIZONS:
        # экстремумы только в пределах h часов
        hh = hi_lim = lo_lim = None
        for _, b in fut.iterrows():
            h_off = (b["ts"] - ts) / 3600
            if h_off <= h:
                hh = max(hh, b["h"]) if hh is not None else b["h"]
                lo_lim = min(lo_lim, b["l"]) if lo_lim is not None else b["l"]
        if hh is None:
            continue
        if side == "BUY":
            out[f"mfe_{h}h"] = (hh - base) / base * 100
            out[f"mae_{h}h"] = (lo_lim - base) / base * 100
        else:
            out[f"mfe_{h}h"] = (base - lo_lim) / base * 100
            out[f"mae_{h}h"] = (base - hh) / base * 100
    return out


def main() -> int:
    rows = [json.loads(l) for l in SIGNAL_LOG.open()]
    sigs = [r for r in rows if r.get("direction") in ("BUY", "SELL")
            and (r.get("final_probability") or 0) >= 0.55]
    sigs.sort(key=lambda r: r["timestamp"])
    split = int(len(sigs) * 0.7)
    oos_ts = {s["timestamp"] for s in sigs[split:]}

    records = []  # каждая: {теги..., mfe_4h, mae_4h, ...}
    cache = {}
    n_invalid = 0
    for s in sigs:
        sym = s["symbol"]
        if sym not in cache:
            cache[sym] = _load_candles(sym)
        df = cache[sym]
        if df is None or len(df) < 60:
            n_invalid += 1
            continue
        ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")).timestamp()
        side = s["direction"]
        price = s.get("close") or s.get("entry") or 0
        atr = _atr_before(df, ts)
        if atr <= 0:
            n_invalid += 1
            continue
        fm = _future_move(df, ts, side, price)
        if not fm:
            n_invalid += 1
            continue
        pre = df[df["ts"] <= ts - 60].tail(30)
        vol_ratio = float(pre["v"].mean()) / (float(df["v"].median()) or 1)
        dist_ema = (price - s.get("ema20", price)) / (s.get("ema20", price) or 1e-12) * 100
        liq = "HIGH" if float(df["v"].median()) > 5e6 else ("MED" if float(df["v"].median()) > 8e5 else "LOW")
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        phase = _phase(df, ts, side, price)
        rec = {
            "set": "OOS" if s["timestamp"] in oos_ts else "IS",
            "side": side,
            "regime": "TREND" if (s.get("adx") or 0) >= 25 else "RANGE",
            "phase": phase,
            "liq": liq,
            "hour": f"{hour:02d}",
            "score": int(s["score"]),
            "prob": round(s["final_probability"], 3),
            "vol": "HIGH_VOL" if vol_ratio > 1.5 else "NORM_VOL",
            "dist_ema": "NEAR" if abs(dist_ema) < 0.3 else "FAR",
            "adx": round(s.get("adx") or 0, 1),
        }
        rec.update(fm)
        records.append(rec)

    print(f"Signals: {len(sigs)} | valid: {len(records)} | invalid: {n_invalid}")
    print(f"OOS split: IS до {sigs[split]['timestamp'][:19]} | OOS после")

    def stat(recs):
        if not recs:
            return {"n": 0}
        mfe4 = [r.get("mfe_4h", 0) for r in recs]
        mae4 = [r.get("mae_4h", 0) for r in recs]
        mfe24 = [r.get("mfe_24h", 0) for r in recs]
        mae24 = [r.get("mae_24h", 0) for r in recs]
        # pos% = MFE4h > |MAE4h| (в пользу входа)
        pos4 = sum(1 for r in recs if r.get("mfe_4h", 0) > abs(r.get("mae_4h", 0)))
        pos24 = sum(1 for r in recs if r.get("mfe_24h", 0) > abs(r.get("mae_24h", 0)))
        return {
            "n": len(recs),
            "mfe4": round(statistics.mean(mfe4), 2),
            "mae4": round(statistics.mean(mae4), 2),
            "mfe24": round(statistics.mean(mfe24), 2),
            "mae24": round(statistics.mean(mae24), 2),
            "pos4%": round(pos4 / len(recs) * 100, 1),
            "pos24%": round(pos24 / len(recs) * 100, 1),
        }

    groups = [
        ("side", ["BUY", "SELL"]),
        ("regime", ["TREND", "RANGE"]),
        ("phase", ["EARLY_IMPULSE", "ACTIVE_MOMENTUM", "LATE_MOMENTUM",
                    "EXHAUSTION", "NO_FAV_IMPULSE"]),
        ("liq", ["HIGH", "MED", "LOW"]),
        ("score", None),  # бакеты ниже
        ("prob", None),
        ("vol", ["HIGH_VOL", "NORM_VOL"]),
        ("dist_ema", ["NEAR", "FAR"]),
        ("hour", None),
    ]

    def show(recs_filter, title):
        st = stat(recs_filter)
        if st["n"] == 0:
            print(f"{title:28s} N=0 (нет данных)")
            return
        print(f"{title:28s} N={st['n']:>6d} MFE4={st['mfe4']:>+6.2f} MAE4={st['mae4']:>+6.2f} "
              f"MFE24={st['mfe24']:>+6.2f} MAE24={st['mae24']:>+6.2f} "
              f"pos4%={st['pos4%']:>5.1f} pos24%={st['pos24%']:>5.1f}")

    print("\n" + "=" * 110)
    print("БАЗОВАЯ СТАТИСТИКА (все сигналы):")
    show(records, "ALL")
    show([r for r in records if r["set"] == "IS"], "IS")
    show([r for r in records if r["set"] == "OOS"], "OOS")

    for gname, fixed in groups:
        print(f"\n### {gname.upper()}")
        if fixed:
            buckets = fixed
        else:
            buckets = sorted({r[gname] for r in records})
        for b in buckets:
            sub = [r for r in records if r[gname] == b]
            show(sub, f"{gname}={b}")
            # OOS внутри бакета
            sub_oos = [r for r in sub if r["set"] == "OOS"]
            if len(sub_oos) >= 30:
                show(sub_oos, f"  [OOS] {gname}={b}")
            else:
                print(f"  [OOS] {gname}={b} N={len(sub_oos)} (<30, недостаточно)")

    # Score бакеты 55-80
    print("\n### SCORE ДЕТАЛЬНО (по 5-пунктам)")
    for lo in range(55, 85, 5):
        sub = [r for r in records if lo <= r["score"] < lo + 5]
        show(sub, f"score {lo}-{lo+4}")
        sub_oos = [r for r in sub if r["set"] == "OOS"]
        if len(sub_oos) >= 20:
            show(sub_oos, f"  [OOS] score {lo}-{lo+4}")

    # Prob бакеты
    print("\n### PROBABILITY ДЕТАЛЬНО")
    for lo, hi in [(0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75), (0.75, 1.0)]:
        sub = [r for r in records if lo <= r["prob"] < hi]
        show(sub, f"prob {lo:.2f}-{hi:.2f}")
        sub_oos = [r for r in sub if r["set"] == "OOS"]
        if len(sub_oos) >= 20:
            show(sub_oos, f"  [OOS] prob {lo:.2f}-{hi:.2f}")

    # Часы: группы сессий
    print("\n### СЕССИИ (UTC)")
    sess = {"ASIA": set(range(0, 8)), "EUROPE": set(range(8, 16)), "US": set(range(16, 24))}
    for name, hours in sess.items():
        sub = [r for r in records if int(r["hour"]) in hours]
        show(sub, f"{name}")
        sub_oos = [r for r in sub if r["set"] == "OOS"]
        if len(sub_oos) >= 20:
            show(sub_oos, f"  [OOS] {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
