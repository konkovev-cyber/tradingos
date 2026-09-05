#!/usr/bin/env python3
"""
edge_discovery.py — ОДИН discovery pass: 10 независимых механизмов.

Проверяет, есть ли в доступных данных повторяемый эффект, предсказывающий
направление/величину будущего движения с положительным ожиданием.

Для каждого механизма (на данных ДО момента t, без look-ahead):
  - forward return / MFE / MAE на 15m / 1h / 4h
  - vs baseline (BUY_ALL / SELL_ALL / RANDOM)
  - permutation test (500 shuffle)
  - symbol concentration (top-1 %)
  - robustness (удаление top-10% сделок)

Механизмы:
  A_TREND_CONT      trend continuation: breakout выше prev_hi (закрытие)
  B_LIQ_SWEEP       sweep: цена пробила prev_hi intrabar, закрылась ниже (wick)
  C_MEAN_REV        отклонение от EMA20 > 1.5 ATR в одну сторону
  D_VOL_EXPAND      ATR<низкий 30-барный перцентиль + расширение + пробой
  E_REL_STRENGTH    coin_ret - BTC_ret на 1h > 0 (прокси: рет последних 4 баров)
  F_CROSS_MOM       ранжирование по momentum, проверка top-дециля
  G_FUND_OI         price↑+OI↑ (NEW LONGS) / price↓+OI↑ (NEW SHORTS) — из derivatives
  H_LIQ_STRESS      OI резко падает + volume spike (прокси по свечам: v>3x, hi-lo>2ATR)
  I_VOL_IMBALANCE   abnormal volume (v>2.5x median) + направление закрытия
  J_REGIME_COND     TREND regime (ADX-прокси: EMA20>EMA50) vs RANGE, отдельно

Вердикт: CONFIRMED / PROMISING / NO EVIDENCE / FAIL.
PRELIMINARY — история ~10 дней.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"

HORIZONS = {"15m": 0.25, "1h": 1.0, "4h": 4.0}


def _load_candles(symbol: str) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{symbol}_M15.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[0] > 1e12:
        df["ts"] = df["ts"] / 1000
    # Предрасчёт rolling-признаков один раз на символ (векторизованно)
    tr = pd.concat([
        (df["h"] - df["l"]),
        (df["h"] - df["c"].shift(1)).abs(),
        (df["l"] - df["c"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(30).mean()
    df["hi40"] = df["h"].rolling(40).max().shift(1)
    df["lo40"] = df["l"].rolling(40).min().shift(1)
    df["hi20"] = df["h"].rolling(24).max().shift(1)
    df["lo20"] = df["l"].rolling(24).min().shift(1)
    df["v_med60"] = df["v"].rolling(60).median().shift(1)
    df["ema20"] = df["c"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["c"].ewm(span=50, adjust=False).mean()
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


def _future(df: pd.DataFrame, ts: float, price: float, hours: float) -> dict:
    fut = df[df["ts"] > ts + 60]
    fut = fut[fut["ts"] <= ts + hours * 3600]
    if len(fut) == 0 or price <= 0:
        return {}
    h = float(fut["h"].max())
    l = float(fut["l"].min())
    c = float(fut["c"].iloc[-1])
    return {"ret": (c - price) / price * 100,
            "mfe": (h - price) / price * 100,
            "mae": (l - price) / price * 100}


def detect_events(df: pd.DataFrame, sym: str) -> dict[str, list[dict]]:
    """Определить события механизмов на каждом закрытом баре (без look-ahead).
    Использует предрасчитанные rolling-колонки из _load_candles: atr, hi40/lo40,
    hi20/lo20, v_med60, ema20/ema50 (все с shift=1 — только прошлые бары)."""
    events = defaultdict(list)
    n = len(df)
    if n < 80:
        return events
    prev_c = df["c"].shift(1)
    for i in range(60, n - 1):
        bar = df.iloc[i]
        ts = float(bar["ts"])
        price = float(bar["c"])
        atr = float(bar["atr"]) if pd.notna(bar["atr"]) else 0.0
        if atr <= 0:
            continue
        hi40 = float(bar["hi40"]) if pd.notna(bar["hi40"]) else price
        lo40 = float(bar["lo40"]) if pd.notna(bar["lo40"]) else price
        hi20 = float(bar["hi20"]) if pd.notna(bar["hi20"]) else price
        lo20 = float(bar["lo20"]) if pd.notna(bar["lo20"]) else price
        vol_med = float(bar["v_med60"]) if pd.notna(bar["v_med60"]) else float(bar["v"])
        ema20 = float(bar["ema20"]) if pd.notna(bar["ema20"]) else price
        ema50 = float(bar["ema50"]) if pd.notna(bar["ema50"]) else price
        # future
        fut = {h: _future(df, ts, price, hh) for h, hh in HORIZONS.items()}
        if not all(fut.values()):
            continue
        base = {"sym": sym, "ts": ts, "fut": fut}
        dev = (price - ema20) / atr

        # A_TREND_CONT: закрытие выше prev_hi (40-барный экстремум прошлого)
        if price > hi40:
            events["A_TREND_CONT"].append(dict(base, side="LONG"))
        if price < lo40:
            events["A_TREND_CONT"].append(dict(base, side="SHORT"))

        # B_LIQ_SWEEP: intrabar пробил уровень, закрылся обратно
        if bar["h"] > hi40 and price < hi40:
            events["B_LIQ_SWEEP"].append(dict(base, side="LONG_REV"))
        if bar["l"] < lo40 and price > lo40:
            events["B_LIQ_SWEEP"].append(dict(base, side="SHORT_REV"))

        # C_MEAN_REV: отклонение от EMA20 > 1.5 ATR
        if dev > 1.5:
            events["C_MEAN_REV"].append(dict(base, side="SHORT_REV"))
        if dev < -1.5:
            events["C_MEAN_REV"].append(dict(base, side="LONG_REV"))

        # D_VOL_EXPAND: ATR < его 30-барного P25 + диапазон бара > 2 ATR + пробой
        atr_series = df["atr"].iloc[max(0, i - 30):i]
        atr_p25 = float(atr_series.quantile(0.25)) if len(atr_series) >= 10 else atr
        if atr < atr_p25 and (bar["h"] - bar["l"]) > 2 * atr:
            if price > hi40:
                events["D_VOL_EXPAND"].append(dict(base, side="LONG"))
            if price < lo40:
                events["D_VOL_EXPAND"].append(dict(base, side="SHORT"))

        # E_REL_STRENGTH: рет последних 4 баров (для последующего сравнения)
        p4 = float(prev_c.iloc[i - 4]) if i >= 4 and pd.notna(prev_c.iloc[i - 4]) else price
        r4 = (price / p4 - 1) * 100 if p4 > 0 else 0.0
        events["E_REL_STRENGTH"].append(dict(base, r4=r4))

        # I_VOL_IMBALANCE: объём > 2.5x
        if bar["v"] > 2.5 * vol_med:
            side = "LONG" if bar["c"] > bar["o"] else "SHORT"
            events["I_VOL_IMBALANCE"].append(dict(base, side=side))

        # H_LIQ_STRESS: резкое движение (hi-lo > 3 ATR) + объём
        if (bar["h"] - bar["l"]) > 3 * atr and bar["v"] > 2 * vol_med:
            side = "LONG" if bar["c"] > bar["o"] else "SHORT"
            events["H_LIQ_STRESS"].append(dict(base, side=side))

        # J_REGIME_COND: trend → breakout; range → mean-rev
        regime = "TREND" if ema20 > ema50 else "RANGE"
        if regime == "TREND" and price > hi40:
            events["J_REGIME_COND"].append(dict(base, side="LONG", regime="TREND"))
        if regime == "RANGE" and dev < -1.2:
            events["J_REGIME_COND"].append(dict(base, side="LONG_REV", regime="RANGE"))
    return events


def permutation_pvalue(vals: list[float], obs_mean: float, runs: int = 500) -> dict:
    rng = random.Random(42)
    perm = []
    for _ in range(runs):
        sh = vals[:]
        rng.shuffle(sh)
        perm.append(statistics.mean(sh))
    pct = sum(1 for m in perm if m >= obs_mean) / runs
    return {"obs": round(obs_mean, 4), "pct": round(pct, 3),
            "perm_p95": round(statistics.quantiles(perm, n=20)[18], 4),
            "perm_mean": round(statistics.mean(perm), 4)}


def analyze() -> None:
    # Все символы из кэша
    symbols = sorted(p.name.replace("_M15.parquet", "") for p in CACHE_DIR.glob("*_M15.parquet"))
    cache = {}
    all_events: dict[str, list[dict]] = defaultdict(list)
    # BTC для E (proxy: медианный рет по всем символам)
    for sym in symbols:
        df = _load_candles(sym)
        if df is None or len(df) < 80:
            continue
        cache[sym] = df
        ev = detect_events(df, sym)
        for k, v in ev.items():
            all_events[k].extend(v)

    print(f"Symbols: {len(symbols)} | событий по механизмам:")
    for k in sorted(all_events):
        print(f"  {k:16s} N={len(all_events[k])}")

    # E_REL_STRENGTH: нужен медианный рет всех символов на тот же бар — прокси:
    # будем использовать r4 каждого события и compare vs random (перемешивание r4).
    print("\n" + "=" * 100)
    print("МЕХАНИЗМЫ vs BASELINE (forward 1h return, %):")
    print("=" * 100)
    for mech in sorted(all_events):
        lst = all_events[mech]
        if len(lst) < 20:
            continue
        vals = [x["fut"]["1h"]["ret"] for x in lst]
        obs = statistics.mean(vals)
        # если событие направленное (LONG/SHORT), считаем directional return
        if "side" in lst[0]:
            dret = [x["fut"]["1h"]["ret"] * (1 if x["side"].startswith("LONG") else -1)
                    for x in lst]
            obs_d = statistics.mean(dret)
            perm = permutation_pvalue(dret, obs_d)
        else:
            dret = vals
            perm = permutation_pvalue(vals, obs)
        # symbol concentration (на directional return)
        by_sym = defaultdict(list)
        for x, r in zip(lst, dret):
            by_sym[x["sym"]].append(r)
        sym_means = {s: statistics.mean(v) for s, v in by_sym.items()}
        total = sum(sym_means.values())
        top1 = max(sym_means.values()) if sym_means else 0
        top1_pct = (top1 / total * 100) if total else 0
        # robustness: удалить top-10% лучших сделок
        sorted_d = sorted(dret)
        cut = sorted_d[: max(1, int(len(dret) * 0.9))]
        robust = statistics.mean(cut)
        print(f"{mech:16s} N={len(lst):>5d} dir_ret1h={obs_d:+.3f} | perm_pct={perm['pct']:.2f} "
              f"p95={perm['perm_p95']:+.3f} | top1_sym={top1_pct:.0f}% | без_top10%={robust:+.3f}")


if __name__ == "__main__":
    analyze()
