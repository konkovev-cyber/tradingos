#!/usr/bin/env python3
"""
trade_construction_scan.py — ОДИН проход Trade Construction Research.

Классифицирует каждый сильный сигнал (prob>=0.55) в один из 8 механизмов
входа ПО ДАННЫМ ДО СИГНАЛА (без look-ahead), затем симулирует полный цикл:

  Entry:  MARKET по цене сигнала (SL = 2×ATR, неизменен)
  Target: realistic = ближайшая структура/экстремум до входа (НЕ 4×ATR)
  Exit:   TP1 (realistic) → runner + trail 0.5R
  Costs:  fees 0.11%×2 (taker)

OOS: последние 30% по времени. KPI: ExpR, PF, MFE capture, giveback, hold.
Вердикт: EDGE FOUND / PROMISING / NO EDGE / DATA INSUFFICIENT.

Никакого look-ahead. Никаких изменений live. Один проход.
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

CLASSES = [
    "A_BREAKOUT", "B_BREAKOUT_RETEST", "C_TREND_PULLBACK", "D_RANGE_REJECTION",
    "E_MEAN_REVERSION", "F_MOMENTUM_CONT", "G_LATE_MOMENTUM", "H_NO_CLEAR_STRUCTURE",
]


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


def classify(df: pd.DataFrame, ts: float, side: str) -> str:
    """Классифицировать сигнал в ОДИН класс (приоритет A→G, иначе H)."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 60:
        return "H_NO_CLEAR_STRUCTURE"
    closes = pre["c"].tolist()
    atr = _atr_before(df, ts)
    if atr <= 0:
        return "H_NO_CLEAR_STRUCTURE"
    ema20 = _ema(closes[-40:], 20)
    ema50 = _ema(closes[-80:], 50)
    last_c = closes[-1]
    win = pre.tail(24)
    hi20 = float(win["h"].max())
    lo20 = float(win["l"].min())
    prev_hi = float(win.head(16)["h"].max())  # структура до последних 8 баров
    prev_lo = float(win.head(16)["l"].min())
    vol_ratio = float(win.tail(3)["v"].mean()) / (float(win["v"].mean()) or 1)
    fav_up = side == "BUY"

    # A_BREAKOUT: пробой уровня (prev_hi/prev_lo) закрытием + объём + не расширен
    if fav_up and last_c > prev_hi and vol_ratio > 1.2:
        # retest уже был? (цена вернулась к уровню после пробоя) → B
        below = [b for _, b in win.iterrows() if b["c"] < prev_hi]
        if below:
            return "B_BREAKOUT_RETEST"
        return "A_BREAKOUT"
    if not fav_up and last_c < prev_lo and vol_ratio > 1.2:
        above = [b for _, b in win.iterrows() if b["c"] > prev_lo]
        if above:
            return "B_BREAKOUT_RETEST"
        return "A_BREAKOUT"

    # C_TREND_PULLBACK: тренд + цена у EMA20 + возврат
    if fav_up and ema20 > ema50 and abs(last_c - ema20) / atr < 1.2 and last_c > ema20:
        return "C_TREND_PULLBACK"
    if not fav_up and ema20 < ema50 and abs(last_c - ema20) / atr < 1.2 and last_c < ema20:
        return "C_TREND_PULLBACK"

    # D_RANGE_REJECTION: в range + откат от границы
    rng = hi20 - lo20
    if rng / atr < 3.0:
        if fav_up and (last_c - lo20) / max(rng, 1e-9) < 0.25:
            return "D_RANGE_REJECTION"
        if not fav_up and (hi20 - last_c) / max(rng, 1e-9) < 0.25:
            return "D_RANGE_REJECTION"

    # E_MEAN_REVERSION: перерастяжение от EMA (RSI-подобный прокси по close vs ema)
    if fav_up and (ema20 - last_c) / atr > 1.5:
        return "E_MEAN_REVERSION"
    if not fav_up and (last_c - ema20) / atr > 1.5:
        return "E_MEAN_REVERSION"

    # F_MOMENTUM_CONT: сильный бар + малый откат
    imp = win.iloc[-3]
    imp_range = abs(imp["c"] - imp["o"])
    if imp_range > 1.5 * atr:
        pull = imp["h"] - imp["l"]
        if fav_up and last_c > imp["c"] and (imp["h"] - last_c) / max(pull, 1e-12) < 0.5:
            return "F_MOMENTUM_CONT"
        if not fav_up and last_c < imp["c"] and (last_c - imp["l"]) / max(pull, 1e-12) < 0.5:
            return "F_MOMENTUM_CONT"

    # G_LATE_MOMENTUM: импульс был > 60 мин назад без follow
    imp_ts_found = None
    for _, b in win.iterrows():
        if b["v"] > 2.2 * float(win["v"].mean() or 1) and b["c"] != b["o"]:
            up = b["c"] > b["o"]
            if (fav_up and up) or (not fav_up and not up):
                imp_ts_found = b["ts"]
    if imp_ts_found is not None:
        mins_since = (ts - imp_ts_found) / 60
        if mins_since > 60:
            return "G_LATE_MOMENTUM"

    return "H_NO_CLEAR_STRUCTURE"


def realistic_target(df: pd.DataFrame, ts: float, side: str, price: float,
                     atr: float) -> float:
    """Ближайшая структура/экстремум ДО входа как реалистичная цель (в цене)."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 60:
        return price + (4 * atr if side == "BUY" else -4 * atr)
    win = pre.tail(40)
    if side == "BUY":
        # ближайший уровень СВЫШЕ (сопротивление), иначе экстремум + 1 ATR
        above = [b["h"] for b in win.iloc[:-0].iterrows()] if False else \
            [float(b["h"]) for _, b in win.iterrows() if float(b["h"]) > price]
        if above:
            tgt = min(above) + atr * 0.3  # до уровня + buffer
        else:
            tgt = float(win["h"].max()) + atr * 0.3
    else:
        below = [float(b["l"]) for _, b in win.iterrows() if float(b["l"]) < price]
        if below:
            tgt = max(below) - atr * 0.3
        else:
            tgt = float(win["l"].min()) - atr * 0.3
    return tgt


def simulate(df: pd.DataFrame, ts: float, side: str, price: float,
             atr: float) -> dict:
    """Полный цикл: MARKET entry, SL=2ATR, TP1=realistic, runner+trail 0.5R."""
    if atr <= 0 or price <= 0:
        return {}
    sl = price - 2 * atr if side == "BUY" else price + 2 * atr
    risk = abs(price - sl)
    tp1 = realistic_target(df, ts, side, price, atr)
    fut = df[df["ts"] > ts + 60]
    fut = fut[fut["ts"] <= ts + 7 * 86400]
    if len(fut) < 50:
        return {}

    mfe = mae = 0.0
    realized = 0.0
    qty = 1.0
    tp1_done = False
    peak_px = price
    exit_reason = "EXPIRED"
    exit_ts = None

    def _r(px):
        return (px - price) / risk if side == "BUY" else (price - px) / risk

    for _, b in fut.iterrows():
        if side == "BUY":
            mfe = max(mfe, (b["h"] - price) / risk)
            mae = min(mae, (b["l"] - price) / risk)
            peak_px = max(peak_px, float(b["h"]))
        else:
            mfe = max(mfe, (price - b["l"]) / risk)
            mae = min(mae, (price - b["h"]) / risk)
            peak_px = min(peak_px, float(b["l"]))
        hit_sl = (b["l"] <= sl) if side == "BUY" else (b["h"] >= sl)
        if hit_sl:
            realized += _r(sl) * qty
            exit_reason = "SL" if not tp1_done else "SL_RUNNER"
            exit_ts = b["ts"]
            break
        if not tp1_done:
            hit_tp1 = (b["h"] >= tp1) if side == "BUY" else (b["l"] <= tp1)
            if hit_tp1:
                realized += _r(tp1) * 0.5
                qty = 0.5
                tp1_done = True
        else:
            trail = peak_px - 0.5 * risk if side == "BUY" else peak_px + 0.5 * risk
            hit_trail = (b["c"] <= trail) if side == "BUY" else (b["c"] >= trail)
            if hit_trail:
                realized += _r(float(b["c"])) * qty
                exit_reason = "TRAIL"
                exit_ts = b["ts"]
                break
    if exit_ts is None:
        realized += _r(float(fut["c"].iloc[-1])) * qty
        exit_ts = float(fut["ts"].iloc[-1])
        exit_reason = "EXPIRED"

    fee_r = 0.0011 * price / risk
    net_r = realized - fee_r * 2
    return {"net_r": net_r, "mfe": mfe, "mae": mae, "reason": exit_reason,
            "hold_h": round((exit_ts - ts) / 3600, 1)}


def main() -> int:
    rows = [json.loads(l) for l in SIGNAL_LOG.open()]
    sigs = [r for r in rows if r.get("direction") in ("BUY", "SELL")
            and (r.get("final_probability") or 0) >= 0.55]
    sigs.sort(key=lambda r: r["timestamp"])
    split = int(len(sigs) * 0.7)
    oos_ts = {s["timestamp"] for s in sigs[split:]}

    stats = {c: {"IS": [], "OOS": []} for c in CLASSES}
    cache = {}
    dist = {}
    n_classified = 0
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
        cls = classify(df, ts, side)
        out = simulate(df, ts, side, price, atr)
        if not out:
            continue
        n_classified += 1
        dist[cls] = dist.get(cls, 0) + 1
        bucket = "OOS" if s["timestamp"] in oos_ts else "IS"
        stats[cls][bucket].append(out)

    print(f"Signals: {len(sigs)} | классифицировано+симулировано: {n_classified}")
    print("Распределение по классам:", json.dumps(dist))
    print("OOS: последние 30% по времени\n")

    def _metrics(lst):
        if not lst:
            return None
        rs = [x["net_r"] for x in lst]
        wins = [x for x in lst if x["net_r"] > 0]
        gp = sum(r for r in rs if r > 0)
        gl = abs(sum(r for r in rs if r < 0))
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
        caps = [min(max(x["net_r"] / x["mfe"], 0), 1.5) for x in lst if x["mfe"] > 0.05]
        gb = [max(0, x["mfe"] - x["net_r"]) for x in lst if x["mfe"] > 0]
        return {
            "n": len(lst), "pf": round(pf, 2), "exp": round(statistics.mean(rs), 4),
            "wr": round(len(wins) / len(lst) * 100, 1),
            "mfe": round(statistics.mean(x["mfe"] for x in lst), 2),
            "mae": round(statistics.mean(x["mae"] for x in lst), 2),
            "cap": round(statistics.mean(caps), 3) if caps else 0,
            "gb": round(statistics.mean(gb), 3) if gb else 0,
            "hold": round(statistics.mean([x["hold_h"] for x in lst if x["hold_h"]]), 1),
        }

    hdr = (f"{'CLASS':26s} {'Set':4s} {'N':>5s} {'WR%':>5s} {'PF':>6s} {'ExpR':>8s} "
           f"{'MFE':>5s} {'MAE':>5s} {'Cap':>5s} {'GB':>5s} {'HoldH':>5s}")
    print(hdr)
    print("-" * 90)
    for c in CLASSES:
        for bucket in ("IS", "OOS"):
            m = _metrics(stats[c][bucket])
            if not m or m["n"] == 0:
                continue
            print(f"{c:26s} {bucket:4s} {m['n']:>5d} {m['wr']:>5.1f} {m['pf']:>6.2f} "
                  f"{m['exp']:>+8.4f} {m['mfe']:>+5.2f} {m['mae']:>+5.2f} "
                  f"{m['cap']:>5.2f} {m['gb']:>5.2f} {m['hold']:>5.1f}")
        print()

    print("=" * 90)
    print("ВЕРДИКТ (критерий: OOS ExpR>0 И PF>1 после издержек):")
    any_edge = False
    for c in CLASSES:
        o = _metrics(stats[c]["OOS"])
        i = _metrics(stats[c]["IS"])
        if not o or o["n"] < 30:
            print(f"  {c}: DATA INSUFFICIENT (OOS N={o['n'] if o else 0})")
            continue
        ok = o["exp"] > 0 and o["pf"] > 1
        stable = ok and i and i["exp"] > 0 and i["pf"] > 1
        tag = "EDGE FOUND" if stable else ("PROMISING" if ok else "NO EDGE")
        if stable:
            any_edge = True
        print(f"  {c}: {tag} | IS ExpR={i['exp']:+.3f} PF={i['pf']} | "
              f"OOS ExpR={o['exp']:+.3f} PF={o['pf']} | OOS N={o['n']}")

    print("\n" + "=" * 90)
    if any_edge:
        print("ИТОГ: EDGE FOUND — есть полный цикл с положительным OOS ожиданием.")
    else:
        print("ИТОГ: NO EDGE в entry/exit механике на текущем сигнальном universe.")
        print("→ Проблема не в конструкции сделки, а в отсутствии predictive edge сигналов.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
