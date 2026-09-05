#!/usr/bin/env python3
"""
direction_edge.py — Direction-edge research (T3).

Гипотеза (owner 2026-08-29):
    Если свечная структура даёт статистически предсказуемое краткосрочное
    направление P(up|state), то лимитный вход + динамическое управление
    имеют смысл. Если нет — никакой execution engine не поможет.

Что измеряем (READ-ONLY, публичные данные, ничего не торгуем):
    Для каждого ЗАКРЫТОГО H1-бара считаем признаки как в manual_scanner:
      - h1_up / h1_down (price vs EMA20 vs EMA50)
      - RSI14 (Wilder)
      - dist_to_ema20 (%)
      - vol_ratio (последний закрытый vs средние 20)
      - momentum-класс (RSI направление)
      - pullback_mature proxy (up-бар в последних 6 H1, not straight-down)
    и forward return на +1 / +2 / +5 / +10 H1 баров.

    Агрегируем по бакетам признаков и сторонам (LONG/SHORT СИММЕТРИЧНО):
      mean forward return, win rate, n, и t-статистику.

Отчёт покажет, ГДЕ есть статистический edge, а где его нет.
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

ROOT = Path("/root/tradingos")
OUT = ROOT / "memory" / "research" / "direction_edge"
OUT.mkdir(parents=True, exist_ok=True)

# Символы: крипто с реальной ликвидностью + акции из мануального контура
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "ADAUSDT", "NEARUSDT", "SUIUSDT", "LINKUSDT", "AAVEUSDT", "AVAXUSDT",
    "UNIUSDT", "LTCUSDT", "OPUSDT", "WLDUSDT", "APTUSDT", "ONDOUSDT",
    "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "AMZNUSDT", "MSFTUSDT", "METAUSDT",
    "GOOGLUSDT", "NFLXUSDT", "AMDUSDT", "INTCUSDT",
]

H1_LIMIT_PER_REQ = 1000
TARGET_BARS = 3000          # ~125 дней H1 на символ
CACHE_DIR = ROOT / "replay_cache"


def _rsi_wilder(closes: list[float], n: int = 14) -> pd.Series:
    """RSI14 Wilder — вектор по всему массиву закрытий."""
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_g = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_g / avg_l.replace(0.0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50.0)


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def fetch_klines(client: httpx.Client, symbol: str, interval: str = "60",
                 target: int = TARGET_BARS) -> pd.DataFrame:
    """Качать klines с пагинацией назад от 'сейчас', кэш в parquet."""
    cache = CACHE_DIR / f"{symbol}_H1.parquet"
    if cache.exists():
        df = pd.read_parquet(cache).sort_values("ts").drop_duplicates("ts")
        if len(df) >= target:
            return df
    rows: list[tuple] = []
    end = int(time.time() * 1000)
    while len(rows) < target:
        r = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": interval,
                    "limit": H1_LIMIT_PER_REQ, "end": end},
            timeout=20,
        )
        data = r.json()
        if data.get("retCode") != 0:
            print(f"  {symbol}: retCode={data.get('retCode')} {data.get('retMsg')}")
            break
        lst = (data.get("result") or {}).get("list") or []
        if not lst:
            break
        rows.extend(lst)
        oldest = min(int(x[0]) for x in lst)
        if len(lst) < H1_LIMIT_PER_REQ:
            break
        end = oldest - 1
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v", "turnover"])
    for col in ("o", "h", "l", "c", "v"):
        df[col] = df[col].astype(float)
    df["ts"] = df["ts"].astype("int64") // 1000  # миллисекунды → секунды
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    df = df.iloc[-target:]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Признаки как в manual_scanner + forward returns."""
    d = df.copy()
    d["ema20"] = _ema(d["c"], 20)
    d["ema50"] = _ema(d["c"], 50)
    d["rsi"] = _rsi_wilder(d["c"].tolist())
    d["dist_e20_pct"] = (d["c"] - d["ema20"]) / d["ema20"] * 100
    d["vol_ratio"] = d["v"] / d["v"].rolling(21).mean().shift(1)
    d["h1_up"] = (d["c"] > d["ema20"]) & (d["ema20"] > d["ema50"])
    d["h1_down"] = (d["c"] < d["ema20"]) & (d["ema20"] < d["ema50"])
    d["mom_up"] = d["rsi"] > 50
    # pullback_mature proxy на H1: ≥1 up-бар в последних 6, не 4+ подряд вниз
    d["up_bar"] = d["c"] > d["c"].shift(1)
    d["up_in_6"] = d["up_bar"].rolling(6).sum()
    d["straight_down"] = (d["c"] <= d["c"].shift(1)) & \
                         (d["c"].shift(1) <= d["c"].shift(2)) & \
                         (d["c"].shift(2) <= d["c"].shift(3)) & \
                         (d["c"].shift(3) <= d["c"].shift(4))
    d["pullback_mature"] = (~d["straight_down"]) & (d["up_in_6"] >= 2)
    # Forward returns на закрытых барах
    for k in (1, 2, 5, 10):
        d[f"fwd_{k}"] = d["c"].shift(-k) / d["c"] - 1.0
    # Бар должен быть закрыт и иметь forward данные до k=10
    d["valid"] = d["fwd_10"].notna() & d["ema50"].notna() & d["rsi"].notna()
    return d


def bucket_stats(sub: pd.DataFrame, label: str, side: str) -> dict | None:
    """Статистика по подвыборке: mean fwd return, WR, n, t-stat."""
    if len(sub) < 30:
        return None
    out = {"label": label, "side": side, "n": len(sub)}
    for k in (1, 2, 5, 10):
        col = f"fwd_{k}"
        vals = sub[col].dropna()
        if len(vals) < 30:
            continue
        mean = vals.mean()
        std = vals.std(ddof=1)
        se = std / math.sqrt(len(vals))
        t = mean / se if se > 0 else 0.0
        wr = (vals > 0).mean() * 100
        out[f"mean_{k}"] = round(mean * 100, 4)       # %
        out[f"wr_{k}"] = round(wr, 1)
        out[f"t_{k}"] = round(t, 2)
    return out


def analyze_symbol(sym: str, client: httpx.Client) -> dict:
    df = fetch_klines(client, sym)
    if df.empty or len(df) < 400:
        return {"symbol": sym, "error": "insufficient data"}
    d = build_features(df)
    ok = d[d["valid"]].copy()
    results: list[dict] = []

    def add(sub, label, side="LONG"):
        st = bucket_stats(sub, label, side)
        if st:
            results.append(st)

    # 1. Базовое направление (симметрично)
    add(ok, "ALL", "LONG")
    add(ok[ok["h1_up"]], "H1_UP_STRICT", "LONG")
    add(ok[ok["h1_down"]], "H1_DOWN_STRICT", "SHORT")
    add(ok[~ok["h1_up"] & ~ok["h1_down"]], "H1_RANGE", "LONG")

    # 2. RSI-классы
    add(ok[ok["rsi"] < 40], "RSI<40", "LONG")
    add(ok[ok["rsi"] > 60], "RSI>60", "SHORT")
    add(ok[(ok["rsi"] >= 40) & (ok["rsi"] <= 60)], "RSI_40-60", "LONG")

    # 3. dist_to_ema20
    add(ok[ok["dist_e20_pct"] < -0.5], "BELOW_EMA20_0.5%", "LONG")
    add(ok[ok["dist_e20_pct"] > 0.5], "ABOVE_EMA20_0.5%", "SHORT")
    add(ok[ok["dist_e20_pct"].abs() <= 0.15], "NEAR_EMA20", "LONG")

    # 4. pullback_mature (направление по h1_up/h1_down)
    pb = ok[ok["pullback_mature"] & ok["h1_up"]]
    add(pb, "PULLBACK_MATURE_UP", "LONG")
    pb_dn = ok[ok["pullback_mature"] & ok["h1_down"]]
    add(pb_dn, "PULLBACK_MATURE_DOWN", "SHORT")

    # 5. vol_ratio
    add(ok[ok["vol_ratio"] > 1.5], "VOL>1.5x", "LONG")
    add(ok[ok["vol_ratio"] < 0.7], "VOL<0.7x", "LONG")

    # 6. Комбинация, близкая к сканеру: h1_up + mom_up + near_ema
    combo = ok[ok["h1_up"] & ok["mom_up"] & (ok["dist_e20_pct"].abs() <= 0.35)]
    add(combo, "SCANNER_LIKE_LONG", "LONG")
    combo_dn = ok[ok["h1_down"] & (~ok["mom_up"]) & (ok["dist_e20_pct"].abs() <= 0.35)]
    add(combo_dn, "SCANNER_LIKE_SHORT", "SHORT")

    # 7. Контроль: "против тренда" (должно быть плохо или случайно)
    add(ok[ok["h1_up"] & (~ok["mom_up"])], "H1_UP_RSI_BEARISH", "LONG")
    add(ok[ok["h1_down"] & ok["mom_up"]], "H1_DOWN_RSI_BULLISH", "SHORT")

    return {"symbol": sym, "bars": len(ok), "buckets": results}


def main() -> None:
    print(f"Direction-edge research — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Symbols: {len(SYMBOLS)} | H1 bars/symbol target: {TARGET_BARS}\n")

    all_results: dict[str, dict] = {}
    with httpx.Client() as client:
        for i, sym in enumerate(SYMBOLS, 1):
            print(f"[{i}/{len(SYMBOLS)}] {sym}...", flush=True)
            try:
                res = analyze_symbol(sym, client)
                if "error" not in res:
                    print(f"  bars={res['bars']} buckets={len(res['buckets'])}")
                all_results[sym] = res
            except Exception as e:
                print(f"  ERROR: {e}")
                all_results[sym] = {"symbol": sym, "error": str(e)}

    # Сводка по бакетам АГРЕГИРОВАННО по всем символам
    agg: dict[str, list[float]] = {}
    agg_wr: dict[str, list[float]] = {}
    agg_n: dict[str, int] = {}
    for sym, res in all_results.items():
        if "error" in res:
            continue
        for b in res["buckets"]:
            key = f"{b['label']}|{b['side']}"
            agg.setdefault(key, [])
            agg_wr.setdefault(key, [])
            agg_n[key] = agg_n.get(key, 0) + b["n"]
            for k in (1, 2, 5, 10):
                if f"mean_{k}" in b:
                    agg[key].append((k, b[f"mean_{k}"], b[f"t_{k}"]))
                if f"wr_{k}" in b:
                    agg_wr[key].append((k, b[f"wr_{k}"]))

    print("\n" + "=" * 90)
    print("AGGREGATED BUCKET EDGE (mean forward return %, WR%, t-stat)")
    print("=" * 90)
    header = f"{'bucket':<34}{'n':>8}{'fwd1':>12}{'fwd2':>12}{'fwd5':>12}{'fwd10':>12} | {'WR1':>6}{'WR5':>6}"
    print(header)
    print("-" * 90)
    for key in sorted(agg.keys(), key=lambda k: -agg_n[k]):
        n = agg_n[key]
        cells = {}
        wrcells = {}
        for k, m, t in agg[key]:
            cells[k] = f"{m:+.3f}%"
        for k, w in agg_wr.get(key, []):
            wrcells[k] = f"{w:.0f}%"
        row = f"{key:<34}{n:>8}"
        for k in (1, 2, 5, 10):
            row += f"{cells.get(k, '—'):>12}"
        row += f" | {wrcells.get(1, '—'):>6}{wrcells.get(5, '—'):>6}"
        print(row)

    # Сохранить сырые данные
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "raw.json").write_text(json.dumps(all_results, ensure_ascii=False, indent=2, default=str))
    print(f"\nRAW data: {OUT / 'raw.json'}")


if __name__ == "__main__":
    main()