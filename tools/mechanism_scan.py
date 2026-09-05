#!/usr/bin/env python3
"""
mechanism_scan.py — ОДИН проход по 5 рыночным механизмам входа (read-only).

Для каждого сильного сигнала (prob>=0.55) классифицируем, к какому из 5
принципиально разных механизмов входа он относится, ПО ДАННЫМ ДО СИГНАЛА
(без look-ahead), затем считаем outcome (MFE/MAE/1R/2R) на будущих свечах.

Механизмы (по ТЗ, прокси на M15):
  STRUCTURE_BREAK   — пробой последнего swing-high/low + подтверждение
  TREND_PULLBACK    — тренд (EMA>EMA) + откат к EMA20 + возврат
  RANGE_BREAKOUT    — сжатие диапазона + пробой границы
  MOMENTUM_CONT     — сильный импульс + контролируемый откат (<50% импульса)
  TRENDLINE_BREAK   — прокси: n-кратный тест уровня + пробой

Сигнал может попасть в несколько механизмов; считаем по каждому.
OOS: последние 30% по времени. Вердикт по критериям EDGE/PROMISING/NO EDGE.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"

MECHANISMS = ["STRUCTURE_BREAK", "TREND_PULLBACK", "RANGE_BREAKOUT",
              "MOMENTUM_CONT", "TRENDLINE_BREAK"]


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


def _ema(vals: list[float], n: int) -> float:
    if not vals:
        return 0.0
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def classify_mechanisms(df: pd.DataFrame, ts: float, side: str) -> list[str]:
    """Определить, каким механизмам входа соответствует сигнал (по данным ДО ts)."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 60:
        return []
    closes = pre["c"].tolist()
    highs = pre["h"].tolist()
    lows = pre["l"].tolist()
    atr = _atr_before(df, ts)
    if atr <= 0:
        return []
    ema20 = _ema(closes[-40:], 20)
    ema50 = _ema(closes[-80:], 50)
    last_c = closes[-1]
    win = pre.tail(20)
    hi20 = float(win["h"].max())
    lo20 = float(win["l"].min())
    fav_up = side == "BUY"
    out = []

    # STRUCTURE_BREAK: последний swing high/low (экстремум 10-барового окна,
    # отстоящий от текущей цены) пробит закрытием
    swings = win.head(10)
    sw_hi = float(swings["h"].max())
    sw_lo = float(swings["l"].min())
    if fav_up and last_c > sw_hi and sw_hi > lo20:
        out.append("STRUCTURE_BREAK")
    if not fav_up and last_c < sw_lo and sw_lo < hi20:
        out.append("STRUCTURE_BREAK")

    # TREND_PULLBACK: тренд + цена у EMA20 + возврат выше (для BUY)
    if fav_up and ema20 > ema50 and abs(last_c - ema20) / atr < 1.0 and last_c > ema20:
        out.append("TREND_PULLBACK")
    if not fav_up and ema20 < ema50 and abs(last_c - ema20) / atr < 1.0 and last_c < ema20:
        out.append("TREND_PULLBACK")

    # RANGE_BREAKOUT: сжатие (диапазон < 2.5 ATR) + пробой границы
    rng = hi20 - lo20
    if rng / atr < 2.5:
        if fav_up and last_c > hi20:
            out.append("RANGE_BREAKOUT")
        if not fav_up and last_c < lo20:
            out.append("RANGE_BREAKOUT")

    # MOMENTUM_CONT: сильный импульс (бар > 2 ATR) + откат < 50% + возврат
    pre24 = pre.tail(25)
    imp = pre24.iloc[-3]
    imp_range = abs(imp["c"] - imp["o"])
    if imp_range > 2 * atr:
        pull = (imp["h"] - imp["l"])
        if fav_up and last_c > imp["c"] and (imp["h"] - last_c) / max(pull, 1e-12) < 0.5:
            out.append("MOMENTUM_CONT")
        if not fav_up and last_c < imp["c"] and (last_c - imp["l"]) / max(pull, 1e-12) < 0.5:
            out.append("MOMENTUM_CONT")

    # TRENDLINE_BREAK (прокси): 3+ касаний уровня (нижняя граница для BUY) + пробой
    ref = lo20 if fav_up else hi20
    touches = sum(1 for b in win.head(16).iterrows()
                  if abs(float(b[1]["l"]) - ref) / atr < 0.5 and not fav_up
                  or abs(float(b[1]["h"]) - ref) / atr < 0.5 and fav_up)
    if touches >= 3:
        if fav_up and last_c > hi20:
            out.append("TRENDLINE_BREAK")
        if not fav_up and last_c < lo20:
            out.append("TRENDLINE_BREAK")
    return out


def future_outcome(df: pd.DataFrame, ts: float, side: str, price: float,
                   atr: float) -> dict:
    fut = df[df["ts"] > ts + 60]
    if len(fut) == 0 or price <= 0 or atr <= 0:
        return {}
    base = price
    sl = base - 2 * atr if side == "BUY" else base + 2 * atr
    risk = abs(base - sl)
    mfe = mae = 0.0
    hit1 = hit2 = False
    for _, b in fut.iterrows():
        h_off = (b["ts"] - ts) / 3600
        if h_off > 24:
            break
        if side == "BUY":
            mfe = max(mfe, (b["h"] - base) / risk)
            mae = min(mae, (b["l"] - base) / risk)
            if b["h"] - base >= risk and not hit1:
                hit1 = True
            if b["h"] - base >= 2 * risk and not hit2:
                hit2 = True
            if b["l"] <= sl:
                break
        else:
            mfe = max(mfe, (base - b["l"]) / risk)
            mae = min(mae, (base - b["h"]) / risk)
            if base - b["l"] >= risk and not hit1:
                hit1 = True
            if base - b["l"] >= 2 * risk and not hit2:
                hit2 = True
            if b["h"] >= sl:
                break
    return {"mfe": mfe, "mae": mae, "hit1r": hit1, "hit2r": hit2}


def main() -> int:
    rows = [json.loads(l) for l in SIGNAL_LOG.open()]
    sigs = [r for r in rows if r.get("direction") in ("BUY", "SELL")
            and (r.get("final_probability") or 0) >= 0.55]
    sigs.sort(key=lambda r: r["timestamp"])
    split = int(len(sigs) * 0.7)
    oos_ts = {s["timestamp"] for s in sigs[split:]}

    stats = {m: {"IS": [], "OOS": []} for m in MECHANISMS}
    cache = {}
    n_any = 0
    for s in sigs:
        sym = s["symbol"]
        if sym not in cache:
            cache[sym] = _load_candles(sym)
        df = cache[sym]
        if df is None or len(df) < 80:
            continue
        ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")).timestamp()
        side = s["direction"]
        price = s.get("close") or s.get("entry") or 0
        atr = _atr_before(df, ts)
        if atr <= 0:
            continue
        mech = classify_mechanisms(df, ts, side)
        if not mech:
            continue
        n_any += 1
        out = future_outcome(df, ts, side, price, atr)
        if not out:
            continue
        bucket = "OOS" if s["timestamp"] in oos_ts else "IS"
        for m in mech:
            stats[m][bucket].append(out)

    print(f"Signals: {len(sigs)} | попало хотя бы в 1 механизм: {n_any}")
    print("OOS: последние 30% по времени\n")
    hdr = (f"{'MECHANISM':20s} {'Set':4s} {'N':>6s} {'MFE':>6s} {'MAE':>6s} "
           f"{'1R%':>6s} {'2R%':>6s} {'netR(proxy)':>10s}")
    print(hdr)
    print("-" * 75)
    for m in MECHANISMS:
        for bucket in ("IS", "OOS"):
            lst = stats[m][bucket]
            if not lst:
                continue
            mfe = statistics.mean(x["mfe"] for x in lst)
            mae = statistics.mean(x["mae"] for x in lst)
            r1 = sum(1 for x in lst if x["hit1r"]) / len(lst) * 100
            r2 = sum(1 for x in lst if x["hit2r"]) / len(lst) * 100
            # proxy net R: MFE при входе по рынку, TL-SL 2:1 — грубое ожидание
            print(f"{m:20s} {bucket:4s} {len(lst):>6d} {mfe:>+6.2f} {mae:>+6.2f} "
                  f"{r1:>6.1f} {r2:>6.1f} {mfe + mae:>+10.2f}")
        print()

    # Вердикт по критериям EDGE (OOS)
    print("=" * 75)
    print("ВЕРДИКТ (по OOS):")
    for m in MECHANISMS:
        o = stats[m]["OOS"]
        i = stats[m]["IS"]
        if not o or not i:
            print(f"  {m}: DATA INSUFFICIENT (OOS N={len(o)})")
            continue
        o_mfe = statistics.mean(x["mfe"] for x in o)
        o_mae = statistics.mean(x["mae"] for x in o)
        i_mfe = statistics.mean(x["mfe"] for x in i)
        i_mae = statistics.mean(x["mae"] for x in i)
        stable = (o_mfe + o_mae) > 0 and (i_mfe + i_mae) > 0
        n_ok = len(o) >= 30
        verdict = "EDGE CANDIDATE" if (stable and n_ok) else (
            "PROMISING" if ((o_mfe + o_mae) > 0 and n_ok) else "NO EDGE")
        print(f"  {m}: {verdict} | IS netR={i_mfe+i_mae:+.2f} OOS netR={o_mfe+o_mae:+.2f} "
              f"OOS N={len(o)} | OOS 1R%={sum(1 for x in o if x['hit1r'])/len(o)*100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
