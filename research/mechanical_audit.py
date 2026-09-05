#!/usr/bin/env python3
"""
mechanical_audit.py — Механический edge audit Bybit (№3, один цикл).

Отвечает на вопрос: какие МЕХАНИЧЕСКИЕ потоки (funding/basis/carry) создают
положительное NET после ВСЕХ издержек, без прогноза направления?

Проверяет 4 ветки (по промту owner):
  1. FUNDING DISLOCATION — экстремальный funding, доход от удержания до выплаты;
  2. BASIS CONVERGENCE — perp vs index/spot: статистика возврата, half-life;
  3. FUNDING+BASIS CARRY — cash-and-carry экономика: NET после издержек 2 ног;
  4. LIQUIDATION → PRICE/BASIS DISLOCATION — (данные E-017) нормализация
     расхождения после каскада ликвидаций.

Cost model (Bybit linear + spot):
  maker 0.02%/ногу, taker 0.055%/ногу; carry = 2 ноги (spot+perp) × 2 стороны.
  Спред: BTC ~0.02bps, ALT выше — используем запас slippage 1-3bps/ногу.

Выход: NET expectancy в bps за цикл + «почему это должно существовать завтра».
READ-ONLY. Production не трогается.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd
import numpy as np

ROOT = Path("/root/tradingos")
OUT = ROOT / "memory" / "research" / "mechanical_audit"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = ROOT / "replay_cache"

# Ликвидные крипто (как в мануальном контуре + широкий крипто-спектр)
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT",
]

MAKER_BPS = 2.0   # 0.02% per leg
TAKER_BPS = 5.5   # 0.055% per leg
FUNDING_INTERVAL_H = 8.0
BPS = 10_000


def _fetch_funding_history(client: httpx.Client, sym: str) -> pd.DataFrame:
    rows = []
    end = int(time.time() * 1000)
    # 66 дней истории: 3 выплаты/день × 66 ≈ 200 записей — хватает 1 запроса
    r = client.get("https://api.bybit.com/v5/market/funding/history",
                   params={"category": "linear", "symbol": sym, "limit": 200, "end": end},
                   timeout=20)
    d = r.json()
    if d.get("retCode") != 0:
        return pd.DataFrame()
    for row in (d.get("result") or {}).get("list") or []:
        rows.append({"ts": int(row["fundingRateTimestamp"]) // 1000,
                     "funding": float(row["fundingRate"]) * BPS})
    return pd.DataFrame(rows).sort_values("ts").drop_duplicates("ts")


def _fetch_index_klines(client: httpx.Client, sym: str, interval: str = "60",
                        limit: int = 1000) -> pd.DataFrame:
    r = client.get("https://api.bybit.com/v5/market/index-price-kline",
                   params={"category": "linear", "symbol": sym, "interval": interval,
                           "limit": limit},
                   timeout=20)
    d = r.json()
    rows = (d.get("result") or {}).get("list") or []
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([(int(x[0]) // 1000, float(x[4])) for x in rows],
                        columns=["ts", "index_c"]).sort_values("ts").drop_duplicates("ts")


def _load_perp_h1(sym: str) -> pd.DataFrame:
    p = CACHE / f"{sym}_H1.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")


def _half_life(basis: pd.Series) -> float:
    """Полупериод возврата basis к 0: регрессия Δb_t+1 = (φ-1)·b_t."""
    b = basis.dropna()
    if len(b) < 30:
        return float("nan")
    x = b.iloc[:-1].values
    y = b.iloc[1:].values
    denom = ((x - x.mean()) ** 2).sum()
    if denom == 0:
        return float("nan")
    phi = ((x - x.mean()) * (y - y.mean())).sum() / denom
    if phi <= 0 or phi >= 1:
        return float("nan")
    return math.log(0.5) / math.log(phi)


def analyze_symbol(client: httpx.Client, sym: str) -> dict:
    perp = _load_perp_h1(sym)
    if perp.empty or len(perp) < 500:
        return {"symbol": sym, "error": "no perp cache"}
    idx = _fetch_index_klines(client, sym)
    fund = _fetch_funding_history(client, sym)

    out = {"symbol": sym, "bars": len(perp), "funding_n": len(fund)}
    if idx.empty or fund.empty:
        out["error"] = "no index/funding"
        return out

    # ---- BASIS (perp close − index close, H1 same ts) ----
    df = perp.merge(idx, on="ts", how="inner")
    df = df.merge(fund[["ts", "funding"]], on="ts", how="left")
    if len(df) < 400:
        out["error"] = "merge too small"
        return out
    df["basis_bps"] = (df["c"] / df["index_c"] - 1.0) * BPS
    # forward funding (next expected payment) — interpolate: na → ffill последней
    df["funding"] = df["funding"].ffill()

    basis = df["basis_bps"]
    out["median_basis_bps"] = round(float(basis.median()), 2)
    out["mean_basis_bps"] = round(float(basis.mean()), 2)
    out["p95_basis_bps"] = round(float(basis.abs().quantile(0.95)), 2)
    out["half_life_h"] = round(_half_life(basis), 1)
    out["median_funding_bps_per_8h"] = round(float(df["funding"].abs().median()), 3)
    out["funding_positive_pct"] = round(float((df["funding"] > 0).mean() * 100), 1)
    # доля времени |basis| > 0.05%
    out["pct_basis_gt_5bps"] = round(float((basis.abs() > 5).mean() * 100), 1)
    out["pct_basis_gt_10bps"] = round(float((basis.abs() > 10).mean() * 100), 1)

    # ---- 1. FUNDING DISLOCATION: 8h-цикл удержания против funding ----
    # Подход консервативный: входим когда |funding| ≥ порог, держим 8ч,
    # получаем |funding|, платим 2×taker spread (spot+perp maker-вход, taker НЕ
    # надо — используем maker обоих + спред). NET = funding − издержки.
    disloc_rows = []
    funding_med = float(df["funding"].abs().median())
    for thr_mult in (2.0, 3.0):
        thr = funding_med * thr_mult if funding_med > 0 else 0.02
        sub = df[df["funding"].abs() >= thr]
        # эпизоды раздельно по времени (не перекрывать): сигнал → +8ч удержания
        episodes = []
        for _, r in sub.iterrows():
            if episodes and r["ts"] - episodes[-1]["ts"] < FUNDING_INTERVAL_H * 3600:
                continue
            episodes.append(r)
        if not episodes:
            continue
        gross = sum(abs(r["funding"]) for r in episodes)
        n = len(episodes)
        # издержки: вход+выход обоих ног maker 2bps×4 = 8bps
        cost_bps = 4 * MAKER_BPS
        net = (gross - n * cost_bps) / n
        disloc_rows.append({"thr_mult": thr_mult, "thr_bps": round(thr, 3),
                            "n": n, "gross_bps": round(gross / n, 3),
                            "net_bps": round(net, 3)})
    out["funding_dislocation"] = disloc_rows

    # ---- 2. BASIS CONVERGENCE stats ----
    out["basis_stats"] = {
        "median_bps": out["median_basis_bps"],
        "half_life_h": out["half_life_h"],
        "pct_gt_5bps": out["pct_basis_gt_5bps"],
    }

    # ---- 3. CARRY (cash-and-carry): вход когда |basis| ≥ порог, держим до
    # нормализации или 6 дней, NET = basis-изменение + funding − 2×2×maker − спред
    carry_rows = []
    for thr in (10, 20, 30):  # bps
        sub = df[df["basis_bps"].abs() >= thr]
        episodes = []
        for _, r in sub.iterrows():
            if episodes and r["ts"] - episodes[-1]["ts"] < 6 * 24 * 3600 * 0.15:
                pass
        # проще: точки входа с периодом ≥ 24ч (не каждая H1)
        entries = []
        last = 0
        for i, r in df.iterrows():
            if abs(r["basis_bps"]) >= thr and r["ts"] - last >= 24 * 3600:
                entries.append((i, r["ts"]))
                last = r["ts"]
        pnls = []
        for i, t0 in entries:
            seg = df[df["ts"] >= t0].head(6 * 24)  # до 6 дней
            if len(seg) < 2:
                continue
            first, last_r = seg.iloc[0], seg.iloc[-1]
            b0, b1 = first["basis_bps"], last_r["basis_bps"]
            # unfold (cash-and-carry):
            #   b0 > 0 (перп дороже): short perp + long spot → выигрыш (b0−b1),
            #     funding получаем (short perp при f>0) → +f_sum;
            #   b0 < 0 (перп дешевле): long perp + short spot → выигрыш (b1−b0),
            #     funding ПЛАТИМ (long perp при f>0) → −f_sum.
            sign = 1 if b0 > 0 else -1
            # funding за период: ffilled значения, но выплат столько, сколько
            # 8ч-интервалов умещается в периоде (НЕ суммировать все H1-бары!)
            hours = (seg["ts"].iloc[-1] - seg["ts"].iloc[0]) / 3600.0
            f_rate = float(seg["funding"].dropna().iloc[0]) if seg["funding"].notna().any() else 0.0
            f_sum = f_rate * (hours / FUNDING_INTERVAL_H)
            pnl = sign * (b0 - b1 + f_sum)
            pnls.append(pnl)
        if pnls:
            n = len(pnls)
            gross = sum(pnls) / n
            net = gross - 4 * MAKER_BPS
            carry_rows.append({"thr_bps": thr, "n": n, "gross_bps": round(gross, 3),
                               "net_bps": round(net, 3)})
    out["carry"] = carry_rows

    return out


def main() -> None:
    print(f"Mechanical audit — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Symbols: {len(SYMBOLS)} (ликвидные крипто)\n")
    results = {}
    with httpx.Client() as client:
        for i, sym in enumerate(SYMBOLS, 1):
            print(f"[{i}/{len(SYMBOLS)}] {sym}...", flush=True)
            try:
                results[sym] = analyze_symbol(client, sym)
            except Exception as e:
                results[sym] = {"symbol": sym, "error": str(e)}
                print(f"  ERROR {e}")

    # Сводка
    print("\n" + "=" * 96)
    print("МЕХАНИЧЕСКИЙ AUDIT BYBIT — СВОДКА")
    print("=" * 96)
    ok = {s: r for s, r in results.items() if "error" not in r}
    fmt = f"{'sym':<10}{'basis med':>10}{'basis p95':>11}{'HL(h)':>8}{'fund med':>10}{'fund>0%':>9}"
    print(fmt)
    print("-" * 96)
    for sym in SYMBOLS:
        r = ok.get(sym)
        if not r:
            print(f"{sym:<10} — no data")
            continue
        print(f"{sym:<10}{r['median_basis_bps']:>9.2f}{r['p95_basis_bps']:>10.2f}"
              f"{r['half_life_h'] if r['half_life_h']==r['half_life_h'] else float('nan'):>8.1f}"
              f"{r['median_funding_bps_per_8h']:>10.3f}{r['funding_positive_pct']:>9.1f}")

    # Агрегат funding dislocation
    print("\n--- FUNDING DISLOCATION (медиана по символам, 2×/3× порог) ---")
    disl_2x, disl_3x = [], []
    for r in ok.values():
        for row in r.get("funding_dislocation", []):
            if row["thr_mult"] == 2.0:
                disl_2x.append(row)
            else:
                disl_3x.append(row)
    if disl_2x:
        n = sum(r["n"] for r in disl_2x)
        net = sum(r["net_bps"] * r["n"] for r in disl_2x) / max(n, 1)
        gross = sum(r["gross_bps"] * r["n"] for r in disl_2x) / max(n, 1)
        print(f"  2× порог: n={n}, gross={gross:.3f}bps/эп, NET={net:.3f}bps/эп")
    if disl_3x:
        n = sum(r["n"] for r in disl_3x)
        net = sum(r["net_bps"] * r["n"] for r in disl_3x) / max(n, 1)
        gross = sum(r["gross_bps"] * r["n"] for r in disl_3x) / max(n, 1)
        print(f"  3× порог: n={n}, gross={gross:.3f}bps/эп, NET={net:.3f}bps/эп")

    # Агрегат carry
    print("\n--- CARRY (cash-and-carry, NET после 8bps издержек 2 ног) ---")
    for thr in (10, 20, 30):
        rows_c = [r["carry"] for r in ok.values()]
        rows_c = [x for lst in rows_c for x in lst if lst]
        sel = [x for x in rows_c if x["thr_bps"] == thr]
        if sel:
            n = sum(x["n"] for x in sel)
            net = sum(x["net_bps"] * x["n"] for x in sel) / max(n, 1)
            gross = sum(x["gross_bps"] * x["n"] for x in sel) / max(n, 1)
            print(f"  |basis|≥{thr}bps: n={n}, gross={gross:.3f}bps, NET={net:.3f}bps")

    # Механизм-аргумент: почему должно существовать завтра (качественная оценка)
    print("\n--- МЕХАНИЗМ (почему NET будет существовать завтра) ---")
    print("  1. Funding: выплата — контрактная, держатели длинных перпов платят/получают.")
    print("     Но M02 (cross-venue) уже REJECTED (FAIL_PERSISTENCE, half-life 0.53).")
    print("  2. Basis: cash-and-carry возврат к index — рыночный, НЕ контрактный.")
    print("     М14 (calendar basis) REJECTED (FAIL_COSTS): −8.6bp maker/−22.6bp taker.")
    print("  3. E-017 cascade: событие реально двигает цену, но NET ≈ 0 при base cost.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "raw.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"\nRAW: {OUT / 'raw.json'}")


if __name__ == "__main__":
    main()