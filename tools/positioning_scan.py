#!/usr/bin/env python3
"""
positioning_scan.py — Positioning Effect Preliminary Scan (read-only).

Проверяет, содержит ли динамика Funding + OI + Buy/Sell информацию о будущем
направлении цены, которой не было в Signal Generator.

Метод (без look-ahead):
  - тики derivatives (5.6 мин) → для каждого t: ΔPrice, ΔOI, ΔFunding, ΔBuyRatio
    за последний интервал (цены из 15m кэша: последняя закрытая свеча до t)
  - классификация в 4 сценария: NEW_LONGS (P↑ OI↑), SHORT_COVER (P↑ OI↓),
    NEW_SHORTS (P↓ OI↑), LONG_LIQUID (P↓ OI↓)
  - forward return на 15m / 1h / 4h (из 15m кэша, будущее)
  - метрики: mean/median return, positive rate, MFE/MAE, cross-section
    концентрация (топ-1/2 символа), permutation baseline (50 shuffle)

Вердикт: PROMISING / NOTABLE / NO EVIDENCE / FAIL.
PRELIMINARY — NOT OOS VALIDATED (история ~2.9 дня).
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
DERIV = ROOT.parent / "tradingos_lab/edge_factory/data/derivatives/derivatives.jsonl"

HORIZONS = {"15m": 0.25, "1h": 1.0, "4h": 4.0}


def _load_candles(symbol: str) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{symbol}_M15.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[0] > 1e12:
        df["ts"] = df["ts"] / 1000
    return df


def _price_at(df: pd.DataFrame, ts: float) -> float | None:
    """Закрытие последней свечи ДО ts (без look-ahead)."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) == 0:
        return None
    return float(pre["c"].iloc[-1])


def _future(df: pd.DataFrame, ts: float, price: float, hours: float) -> dict:
    """Forward return / MFE / MAE от ts на горизонт hours (из будущих свечей)."""
    fut = df[df["ts"] > ts + 60]
    fut = fut[fut["ts"] <= ts + hours * 3600]
    if len(fut) == 0 or price <= 0:
        return {}
    h = float(fut["h"].max())
    l = float(fut["l"].min())
    c = float(fut["c"].iloc[-1])
    return {
        "ret": (c - price) / price * 100,
        "mfe": (h - price) / price * 100,
        "mae": (l - price) / price * 100,
    }


def main() -> int:
    deriv_rows = [json.loads(l) for l in DERIV.open()]
    # группируем тики по символу, сортируем
    by_sym: dict[str, list] = defaultdict(list)
    for r in deriv_rows:
        by_sym[r["symbol"]].append(r)
    for s in by_sym:
        by_sym[s].sort(key=lambda x: x["ts"])

    # кэш свечей
    cache: dict[str, pd.DataFrame | None] = {}
    events = defaultdict(list)  # scenario -> list of {sym, horizon: {ret,mfe,mae}}
    n_deriv_usable = 0
    n_no_price = 0

    for sym, ticks in by_sym.items():
        if sym not in cache:
            cache[sym] = _load_candles(sym)
        df = cache[sym]
        if df is None or len(df) < 50:
            continue
        prev = None
        for t in ticks:
            ts = t["ts"] / 1000
            price = _price_at(df, ts)
            if price is None:
                n_no_price += 1
                prev = None
                continue
            if prev is None:
                prev = {"ts": ts, "price": price, "oi": float(t["open_interest"]),
                        "fund": float(t["funding_rate"]), "buy": float(t["buy_ratio"])}
                continue
            def _f(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            fund_now = _f(t["funding_rate"])
            buy_now = _f(t["buy_ratio"])
            oi_now = _f(t["open_interest"])
            if fund_now is None or buy_now is None or oi_now is None:
                prev = None
                continue
            d_p = price - prev["price"]
            d_oi = oi_now - prev["oi"]
            d_fund = fund_now - prev["fund"]
            d_buy = buy_now - prev["buy"]
            # сценарии позиционирования
            scen = None
            if d_p > 0 and d_oi > 0:
                scen = "A_NEW_LONGS"
            elif d_p > 0 and d_oi < 0:
                scen = "B_SHORT_COVER"
            elif d_p < 0 and d_oi > 0:
                scen = "C_NEW_SHORTS"
            elif d_p < 0 and d_oi < 0:
                scen = "D_LONG_LIQUID"
            # funding alignment/divergence под-сценарии
            if scen in ("A_NEW_LONGS", "C_NEW_SHORTS"):
                sub = "_FUND_ALIGN" if d_fund > 0 else "_FUND_DIVERG"
                scen += sub
            # buy-ratio divergence
            if d_p > 0 and d_buy < 0:
                scen_buy = "X_PRICE_UP_BUY_DOWN"
            elif d_p < 0 and d_buy > 0:
                scen_buy = "X_PRICE_DOWN_BUY_UP"
            else:
                scen_buy = None
            if scen:
                fut = {}
                for hname, hh in HORIZONS.items():
                    f = _future(df, ts, price, hh)
                    if f:
                        fut[hname] = f
                if fut:
                    rec = {"sym": sym, "horizons": fut}
                    events[scen].append(rec)
                    n_deriv_usable += 1
            prev = {"ts": ts, "price": price, "oi": oi_now,
                    "fund": fund_now, "buy": buy_now}

    print(f"Deriv-тиков обработано: {n_deriv_usable} | без цены: {n_no_price}")
    print("Сценарии (PRELIMINARY, ~2.9 дня, NOT OOS VALIDATED):")
    print()

    # агрегация
    scenarios = ["A_NEW_LONGS_FUND_ALIGN", "A_NEW_LONGS_FUND_DIVERG",
                 "B_SHORT_COVER", "C_NEW_SHORTS_FUND_ALIGN", "C_NEW_SHORTS_FUND_DIVERG",
                 "D_LONG_LIQUID", "X_PRICE_UP_BUY_DOWN", "X_PRICE_DOWN_BUY_UP"]
    print(f"{'SCENARIO':26s} {'N':>6s} {'Sym':>4s} | {'15m ret':>8s} {'1h ret':>8s} "
          f"{'4h ret':>8s} | {'1h MFE':>7s} {'1h MAE':>7s} {'pos%4h':>7s} {'top1%':>7s}")
    print("-" * 105)

    per_scen_stats = {}
    for scen in scenarios:
        lst = events.get(scen, [])
        if not lst:
            continue
        n = len(lst)
        syms = set(x["sym"] for x in lst)
        rets = {h: [x["horizons"][h]["ret"] for x in lst if h in x["horizons"]]
                for h in HORIZONS}
        # mean по горизонтам
        line = f"{scen:26s} {n:>6d} {len(syms):>4d} | "
        for h in HORIZONS:
            rr = rets[h]
            line += f"{statistics.mean(rr):>+8.3f}" if rr else f"{'—':>8s}"
        line += " | "
        mfe1 = [x["horizons"]["1h"]["mfe"] for x in lst if "1h" in x["horizons"]]
        mae1 = [x["horizons"]["1h"]["mae"] for x in lst if "1h" in x["horizons"]]
        pos4 = [x for x in lst if x["horizons"].get("4h", {}).get("ret", 0) > 0]
        line += f"{statistics.mean(mfe1):>+7.2f} {statistics.mean(mae1):>+7.2f} " \
                f"{len(pos4)/n*100:>6.1f}%"
        # концентрация: вклад топ-1 символа в сумму 4h ret
        by_sym_sum = defaultdict(float)
        for x in lst:
            r4 = x["horizons"].get("4h", {}).get("ret", 0)
            by_sym_sum[x["sym"]] += r4
        total = sum(by_sym_sum.values())
        top1 = max(by_sym_sum.values()) if by_sym_sum else 0
        top1_pct = (top1 / total * 100) if total != 0 else 0
        line += f" | {top1_pct:>6.1f}%"
        print(line)
        per_scen_stats[scen] = {"n": n, "syms": len(syms), "rets": rets,
                                "top1_pct": top1_pct, "mfe1": statistics.mean(mfe1) if mfe1 else 0,
                                "mae1": statistics.mean(mae1) if mae1 else 0}

    # Permutation baseline: shuffle 4h returns внутри сценария
    print("\n" + "=" * 105)
    print("PERMUTATION BASELINE (4h ret, 100 shuffle):")
    rng = random.Random(42)
    for scen, st in per_scen_stats.items():
        lst = events.get(scen, [])
        vals = [x["horizons"].get("4h", {}).get("ret", 0) for x in lst]
        if len(vals) < 20:
            continue
        obs_mean = statistics.mean(vals)
        perm_means = []
        for _ in range(100):
            sh = vals[:]
            rng.shuffle(sh)
            perm_means.append(statistics.mean(sh))
        pct = sum(1 for m in perm_means if m >= obs_mean) / 100
        print(f"  {scen:26s} obs_mean={obs_mean:+.3f} | perm p95={statistics.quantiles(perm_means,n=20)[18]:+.3f} "
              f"pct>=obs: {pct*100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
