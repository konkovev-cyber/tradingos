#!/usr/bin/env python3
"""
replay_v2.py — контрфактический replay TradingOS (read-only, out-of-sample).

Проверяет несколько моделей входа и выхода на одном и том же наборе
1427+ сильных сигналов (prob>=0.55). Out-of-sample: последние 30% по времени.

Модели (Test 10):
  M1 CURRENT:          MARKET entry, TP=4×ATR, SL=2×ATR, нет trail
  M2 ENTRY IMPROVED:   SKIP если EXHAUSTION/LATE; иначе MARKET
  M3 PROFIT EXTR:      TP1@1.5×ATR закрыть 50%, runner trailing
  M4 FULL ENGINE:      SKIP по фазе + TP1@1.5×ATR + Trail 0.5R

Метрики: net expectancy, PF, MFE capture, giveback, hit rate,
profit per trade, time in trade, после fees+slippage.

НЕ ТРОГАЕТ live. Никаких ордеров, никаких side effects.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path("/root/tradingos")
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"

# Кэш свечей: {sym: [bar...]}
_CACHE: dict[str, list[dict]] = {}
_BARS_TS_HR = 24 * 60 * 60  # 1 день


def _load_klines(client: httpx.Client, symbol: str, limit: int = 800) -> list[dict]:
    if symbol in _CACHE:
        return _CACHE[symbol]
    try:
        r = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "15", "limit": limit},
            timeout=15,
        )
        rows = (r.json().get("result") or {}).get("list") or []
    except Exception:
        rows = []
    out = []
    for b in rows:
        ts = int(b[0])
        ts = ts / 1000 if ts > 1e12 else ts
        out.append({"ts": ts, "o": float(b[1]), "h": float(b[2]),
                    "l": float(b[3]), "c": float(b[4]), "v": float(b[5])})
    out.sort(key=lambda x: x["ts"])
    _CACHE[symbol] = out
    return out


def _atr15m(bars: list[dict], ts: float, atr_bars: int = 30) -> float:
    """ATR по последним atr_bars барам ДО ts."""
    pre = [b for b in bars if b["ts"] <= ts - 60]
    if len(pre) < atr_bars + 1:
        return 0.0
    trs = []
    for i in range(len(pre) - atr_bars, len(pre)):
        if i < 1: continue
        b = pre[i]; p = pre[i - 1]
        tr = max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def _entry_phase(bars: list[dict], ts: float, side: str, price: float) -> tuple[str, float, int]:
    """Классификация фазы входа. Возвращает (phase, dist_origin_ATR, mins_since_impulse)."""
    pre = [b for b in bars if b["ts"] <= ts - 60]
    if len(pre) < 30:
        return ("NO_DATA", 0.0, 9999)
    atr = _atr15m(bars, ts)
    if atr <= 0:
        return ("NO_DATA", 0.0, 9999)
    win = pre[-24:]  # 6ч окно
    vols = [b["v"] for b in win]
    v_avg = sum(vols) / len(vols) if vols else 0
    fav = "UP" if side == "BUY" else "DOWN"
    imp_ts = imp_open = None
    for b in win:
        if b["v"] > 2.2 * v_avg and b["c"] != b["o"]:
            up = b["c"] > b["o"]
            if (fav == "UP" and up) or (fav == "DOWN" and not up):
                imp_ts = b["ts"]; imp_open = b["o"]
                break
    mins_since = int((ts - imp_ts) / 60) if imp_ts else 9999
    dist_origin = abs(price - imp_open) / atr if imp_open else 0.0
    # Follow-through
    follow = None
    if imp_ts:
        after = [b for b in win if b["ts"] > imp_ts]
        if after:
            follow = (after[-1]["c"] > after[0]["c"]) if fav == "UP" else (after[-1]["c"] < after[0]["c"])
    if not imp_ts:
        ph = "NO_FAV_IMPULSE"
    elif mins_since > 60 and follow is False:
        ph = "EXHAUSTION"
    elif mins_since > 60:
        ph = "LATE_MOMENTUM"
    elif mins_since <= 60 and follow is True:
        ph = "ACTIVE_MOMENTUM"
    else:
        ph = "EARLY_IMPULSE"
    return (ph, dist_origin, mins_since)


def _evaluate(sig: dict, bars: list[dict], model: str, params: dict) -> dict:
    """Запустить одну из 4 моделей. Возвращает dict с метриками сделки."""
    side = sig["direction"]
    entry_price = sig.get("entry") or sig.get("close") or 0
    entry_ts = datetime.fromisoformat(sig["timestamp"].replace("Z", "+00:00")).timestamp()
    atr = _atr15m(bars, entry_ts)
    if atr <= 0 or entry_price <= 0:
        return {"outcome": "NO_DATA"}
    sl = entry_price - 2 * atr if side == "BUY" else entry_price + 2 * atr
    tp_base = entry_price + 4 * atr if side == "BUY" else entry_price - 4 * atr
    risk_unit = abs(entry_price - sl)

    # Phase для M2/M4
    phase, dist_origin, mins_since = _entry_phase(bars, entry_ts, side, entry_price)

    # Forward-симуляция по 15m-свечам до закрытия
    fut = [b for b in bars if b["ts"] > entry_ts + 60]
    # Окно симуляции: до 7 дней (для неликвидных монет может быть мало данных)
    end_ts = entry_ts + 7 * 24 * 3600
    fut = [b for b in fut if b["ts"] <= end_ts]
    # Если нет достаточных данных — помечаем как DATA_INVALID
    if len(fut) < 50:
        return {"outcome": "DATA_INVALID"}

    # Подневный эквивалент для определения реалистичных TP
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.get("https://api.bybit.com/v5/market/kline",
                       params={"category": "linear", "symbol": sig["symbol"],
                               "interval": "D", "limit": 90})
            days = (r.json().get("result") or {}).get("list") or []
    except Exception:
        days = []
    day_highs = [float(x[2]) for x in days]; day_lows = [float(x[3]) for x in days]
    h30 = max(day_highs) if day_highs else entry_price
    l30 = min(day_lows) if day_lows else entry_price

    mfe_price = mfe_r = 0
    mae_price = mae_r = 0
    realized_r = -999.0
    exit_ts = exit_price = None
    closed_at_sl = closed_at_tp = False
    exit_reason = "EVAL"

    def _update_metrics(cur_price):
        nonlocal mfe_price, mfe_r, mae_price, mae_r
        if side == "BUY":
            p = (cur_price - entry_price) / risk_unit
            if p > mfe_r: mfe_r, mfe_price = p, cur_price
            if p < mae_r: mae_r, mae_price = p, cur_price
        else:
            p = (entry_price - cur_price) / risk_unit
            if p > mfe_r: mfe_r, mfe_price = p, cur_price
            if p < mae_r: mae_r, mae_price = p, cur_price

    def _r(cur_price):
        if side == "BUY": return (cur_price - entry_price) / risk_unit
        return (entry_price - cur_price) / risk_unit

    # Стратегии
    closed = False
    if model == "M1_CURRENT":
        # MARKET entry, TP=4×ATR, SL=2×ATR — без trail
        for b in fut:
            cur = b["h"] if side == "BUY" else b["l"]
            cur_l = b["l"] if side == "BUY" else b["h"]
            _update_metrics(b["h"] if side == "BUY" else b["l"])
            if cur_l <= sl if side == "BUY" else cur_l >= sl:
                exit_price, exit_ts, realized_r, exit_reason = sl, b["ts"], _r(sl), "SL"; closed_at_sl = True; closed = True; break
            if cur >= tp_base if side == "BUY" else cur <= tp_base:
                exit_price, exit_ts, realized_r, exit_reason = tp_base, b["ts"], _r(tp_base), "TP"; closed_at_tp = True; closed = True; break
        if not closed:
            exit_price, exit_ts, realized_r = fut[-1]["c"], fut[-1]["ts"], _r(fut[-1]["c"])
            exit_reason = "EXPIRED_7D"

    elif model == "M2_ENTRY_IMPROVED":
        # SKIP если фаза EXHAUSTION/LATE
        if phase in ("EXHAUSTION", "LATE_MOMENTUM"):
            return {"outcome": "SKIP", "phase": phase}
        # иначе M1
        return _evaluate(sig, bars, "M1_CURRENT", params)

    elif model == "M3_PROFIT_EXTR":
        # TP1 = 1.5×ATR закрыть 50%, runner trailing 0.5R
        tp1 = entry_price + (1.5 * atr if side == "BUY" else -1.5 * atr)
        qty_total = 1.0
        qty_open = 1.0
        realized = 0.0
        tp1_done = False
        for b in fut:
            cur = b["h"] if side == "BUY" else b["l"]
            cur_l = b["l"] if side == "BUY" else b["h"]
            _update_metrics(b["h"] if side == "BUY" else b["l"])
            if cur_l <= sl if side == "BUY" else cur_l >= sl:
                # SL hit: всё закрывается
                if tp1_done:
                    exit_price, exit_ts, exit_reason = sl, b["ts"], "SL_ON_RUNNER"
                    realized_r = realized + _r(sl) * qty_open
                else:
                    exit_price, exit_ts, exit_reason = sl, b["ts"], "SL"; realized_r = _r(sl)
                closed = True; closed_at_sl = True; break
            # TP1 hit (close confirms): закрыть 50%
            if not tp1_done:
                if cur >= tp1 if side == "BUY" else cur <= tp1:
                    realized += _r(tp1) * 0.5
                    qty_open = 0.5
                    tp1_done = True
            # trailing от MFE-пика, на закрытии свечи
            if tp1_done and qty_open > 0:
                trail_price = mfe_price - 0.5 * risk_unit if side == "BUY" else mfe_price + 0.5 * risk_unit
                if side == "BUY" and b["c"] <= trail_price:
                    realized += _r(b["c"]) * qty_open
                    exit_price, exit_ts, exit_reason = b["c"], b["ts"], "TRAIL"
                    closed = True; break
                if side == "SELL" and b["c"] >= trail_price:
                    realized += _r(b["c"]) * qty_open
                    exit_price, exit_ts, exit_reason = b["c"], b["ts"], "TRAIL"
                    closed = True; break
        if not closed:
            exit_price, exit_ts = fut[-1]["c"], fut[-1]["ts"]
            exit_reason = "EXPIRED_7D"
            if tp1_done:
                realized += _r(fut[-1]["c"]) * qty_open
            else:
                realized += _r(fut[-1]["c"])
            realized_r = realized

    elif model == "M4_FULL_ENGINE":
        if phase in ("EXHAUSTION", "LATE_MOMENTUM"):
            return {"outcome": "SKIP", "phase": phase}
        # TP1@1.5×ATR + Trail
        return _evaluate(sig, bars, "M3_PROFIT_EXTR", params)

    # Fees: Bybit taker 0.055% × 2 = 0.11%, slippage 0.02% (типичный вход+выход)
    fee_r = 0.0011  # от цены → в R-units: 0.0011 * entry_price / risk_unit
    fee_in_r = 0.0011 * entry_price / risk_unit
    fee_total_r = fee_in_r * 2  # вход + выход
    net_r = realized_r - fee_total_r

    return {
        "outcome": exit_reason,
        "realized_r_gross": round(realized_r, 3),
        "net_r": round(net_r, 3),
        "mfe_r": round(mfe_r, 3),
        "mae_r": round(mae_r, 3),
        "exit_ts": exit_ts,
        "hold_h": round((exit_ts - entry_ts) / 3600, 1) if exit_ts else None,
        "phase": phase,
        "dist_origin_atr": round(dist_origin, 2),
        "mins_since_imp": mins_since,
    }


def _metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    rs = [t["net_r"] for t in trades]
    wins = [t for t in trades if t["net_r"] > 0]
    mfes = [t["mfe_r"] for t in trades]
    maes = [t["mae_r"] for t in trades]
    gp = sum(r for r in rs if r > 0)
    gl = abs(sum(r for r in rs if r < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    # MFE capture: realized / MFE (only when MFE>0)
    caps = []
    for t in trades:
        if t["mfe_r"] > 0.05:
            caps.append(min(max(t["net_r"] / t["mfe_r"], 0), 1.5))
    avg_cap = statistics.mean(caps) if caps else 0.0
    # Giveback: MFE - realized (positive = отдали прибыль)
    gb = [max(0, t["mfe_r"] - t["net_r"]) for t in trades if t["mfe_r"] > 0]
    return {
        "n": len(trades),
        "skipped": sum(1 for t in trades if t["outcome"] == "SKIP"),
        "wr": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "pf": round(pf, 3),
        "expectancy_r": round(statistics.mean(rs), 4),
        "avg_win_r": round(statistics.mean([t["net_r"] for t in wins]) if wins else 0, 3),
        "avg_loss_r": round(statistics.mean([t["net_r"] for t in trades if t["net_r"] <= 0]) if any(t["net_r"]<=0 for t in trades) else 0, 3),
        "avg_mfe": round(statistics.mean(mfes), 3),
        "avg_mae": round(statistics.mean(maes), 3),
        "mfe_capture": round(avg_cap, 3),
        "avg_giveback": round(statistics.mean(gb), 3) if gb else 0,
        "tp_hit": sum(1 for t in trades if t["outcome"] == "TP"),
        "sl_hit": sum(1 for t in trades if t["outcome"] == "SL"),
        "expired": sum(1 for t in trades if "EXPIRED" in t["outcome"]),
    }


def main():
    sigs = [json.loads(l) for l in SIGNAL_LOG.open()
            if json.loads(l).get("direction") in ("BUY", "SELL")
            and json.loads(l).get("final_probability", 0) >= 0.55]
    sigs.sort(key=lambda r: r["timestamp"])
    split = int(len(sigs) * 0.7)
    in_s, oos_s = sigs[:split], sigs[split:]
    print(f"Всего: {len(sigs)} | IN-SAMPLE: {len(in_s)} | OOS: {len(oos_s)}")
    print(f"IN до: {in_s[-1]['timestamp'][:19]} | OOS после: {oos_s[0]['timestamp'][:19]}")
    print(f"Уникальных символов: {len(set(s['symbol'] for s in sigs))}")
    print()

    # Прогон по моделям
    models = ["M1_CURRENT", "M2_ENTRY_IMPROVED", "M3_PROFIT_EXTR", "M4_FULL_ENGINE"]
    results = {m: {"IN": [], "OOS": []} for m in models}

    with httpx.Client() as client:
        for i, s in enumerate(sigs):
            sym = s["symbol"]
            bars = _load_klines(client, sym)
            if len(bars) < 50:
                continue
            is_oos = s in oos_s
            for m in models:
                res = _evaluate(s, bars, m, {})
                if res["outcome"] in ("NO_DATA", "DATA_INVALID"):
                    continue
                bucket = "OOS" if is_oos else "IN"
                results[m][bucket].append(res)
            if (i + 1) % 200 == 0:
                print(f"  ...processed {i+1}")

    print("\n" + "=" * 90)
    print("РЕЗУЛЬТАТЫ (после fees+slippage, 30% OOS-разбиение):")
    print("=" * 90)
    print(f"{'Модель':22s} {'Set':4s} {'N':>5s} {'Skip':>5s} {'WR%':>5s} {'PF':>6s} {'ExpR':>7s} "
          f"{'avgWin':>6s} {'avgLoss':>7s} {'MFE':>5s} {'MAE':>5s} {'Cap':>5s} {'Giveback':>8s} "
          f"{'TP':>3s} {'SL':>3s} {'EXP':>4s}")
    for m in models:
        for bucket in ("IN", "OOS"):
            ms = _metrics(results[m][bucket])
            if ms["n"] == 0:
                continue
            tag = "**" if bucket == "OOS" else "  "
            print(f"{m:22s} {bucket:4s} {ms['n']:>5d} {ms['skipped']:>5d} {ms['wr']:>5.1f} "
                  f"{ms['pf']:>6.2f} {ms['expectancy_r']:>+7.4f} {ms['avg_win_r']:>+6.2f} {ms['avg_loss_r']:>+7.2f} "
                  f"{ms['avg_mfe']:>+5.2f} {ms['avg_mae']:>+5.2f} {ms['mfe_capture']:>5.2f} {ms['avg_giveback']:>8.2f} "
                  f"{ms['tp_hit']:>3d} {ms['sl_hit']:>3d} {ms['expired']:>4d}")
        print()

    # Сравнение критериев успеха
    print("=" * 90)
    print("КРИТЕРИИ УСПЕХА (NEW проходит одновременно по OOS):")
    print("=" * 90)
    cur = _metrics(results["M1_CURRENT"]["OOS"])
    for m in models[1:]:
        new = _metrics(results[m]["OOS"])
        if new["n"] == 0:
            continue
        checks = {
            "ExpR > CURRENT": new["expectancy_r"] > cur["expectancy_r"],
            "PF > CURRENT": new["pf"] > cur["pf"],
            "MFE capture > CURRENT": new["mfe_capture"] > cur["mfe_capture"],
            "Giveback <= CURRENT": new["avg_giveback"] <= cur["avg_giveback"],
        }
        ok = all(checks.values())
        print(f"{m}: {'PASS' if ok else 'FAIL'}")
        for k, v in checks.items():
            print(f"    {k}: {'✓' if v else '✗'}")


if __name__ == "__main__":
    main()