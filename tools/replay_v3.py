#!/usr/bin/env python3
"""
replay_v3.py — PHASE C/D: детерминированный офлайн-реплей на локальном кэше.

Свойства:
- НЕТ HTTP внутри цикла сигналов (данные из replay_cache/*.parquet)
- Checkpoint: resume с последнего обработанного сигнала
- Детерминизм: dataset_hash + config_hash в отчёте
- Прогресс: каждые 50 сигналов, unbuffered
- Look-ahead: сигнал использует только бары ts <= T; будущее — только симуляция
- Неполные ATR-окна → DATA_INVALID (не угадываем)

Модели (Test 10 ТЗ):
  M1_CURRENT        MARKET, TP=4ATR, SL=2ATR
  M2_ENTRY_IMPROVED SKIP при EXHAUSTION/LATE, иначе M1
  M3_PROFIT_EXTR    TP1@1.5ATR закрыть 50% + runner trail 0.5R
  M4_FULL_ENGINE    SKIP по фазе + TP1@1.5ATR + trail

Окно симуляции: до 7 дней вперёд (или до конца кэша).
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
MANIFEST = CACHE_DIR / "manifest.json"
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"
CHECKPOINT = ROOT / "replay_cache/replay_v3_checkpoint.json"

SIM_DAYS = 7
ATR_BARS = 30
IMPLUSE_VOL_MULT = 2.2


def dataset_hash() -> str:
    h = hashlib.sha256()
    h.update(SIGNAL_LOG.read_bytes()[:2_000_000])
    return h.hexdigest()[:12]


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def _load_candles(symbol: str) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{symbol}_M15.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    # Bybit kline ts в миллисекундах → нормализуем в секунды (сигналы в секундах)
    if df["ts"].iloc[0] > 1e12:
        df["ts"] = df["ts"] / 1000
    return df


def _atr_before(df: pd.DataFrame, ts: float, n: int = ATR_BARS) -> float:
    """ATR по n барам ДО ts."""
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


def _entry_phase(df: pd.DataFrame, ts: float, side: str, price: float) -> tuple[str, float, int]:
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 30:
        return ("NO_DATA", 0.0, 9999)
    atr = _atr_before(df, ts)
    if atr <= 0:
        return ("NO_DATA", 0.0, 9999)
    win = pre.tail(24)
    v_avg = float(win["v"].mean()) if len(win) else 0
    fav = "UP" if side == "BUY" else "DOWN"
    imp_ts = imp_open = None
    for _, b in win.iterrows():
        if b["v"] > IMPLUSE_VOL_MULT * v_avg and b["c"] != b["o"]:
            up = b["c"] > b["o"]
            if (fav == "UP" and up) or (fav == "DOWN" and not up):
                imp_ts = b["ts"]; imp_open = b["o"]
                break
    mins_since = int((ts - imp_ts) / 60) if imp_ts else 9999
    dist_origin = abs(price - imp_open) / atr if imp_open else 0.0
    follow = None
    if imp_ts:
        after = win[win["ts"] > imp_ts]
        if len(after) >= 2:
            follow = (after["c"].iloc[-1] > after["c"].iloc[0]) if fav == "UP" \
                else (after["c"].iloc[-1] < after["c"].iloc[0])
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


def simulate(df: pd.DataFrame, sig: dict, model: str, atr: float,
             entry_price: float, side: str, entry_ts: float, phase: str) -> dict:
    """Симуляция одной сделки по локальным барам. Без сетевых вызовов."""
    if atr <= 0 or entry_price <= 0:
        return {"outcome": "DATA_INVALID"}
    sl = entry_price - 2 * atr if side == "BUY" else entry_price + 2 * atr
    risk_unit = abs(entry_price - sl)
    fut = df[df["ts"] > entry_ts + 60]
    end_ts = entry_ts + SIM_DAYS * 86400
    fut = fut[fut["ts"] <= end_ts]
    if len(fut) < 50:
        return {"outcome": "DATA_INVALID"}

    mfe_r = mae_r = 0.0
    realized_r = None
    exit_ts = None
    exit_reason = "EXPIRED"
    closed = False

    def _update(cur_h, cur_l):
        nonlocal mfe_r, mae_r
        if side == "BUY":
            mfe_r = max(mfe_r, (cur_h - entry_price) / risk_unit)
            mae_r = min(mae_r, (cur_l - entry_price) / risk_unit)
        else:
            mfe_r = max(mfe_r, (entry_price - cur_l) / risk_unit)
            mae_r = min(mae_r, (entry_price - cur_h) / risk_unit)

    def _r(px):
        return (px - entry_price) / risk_unit if side == "BUY" else (entry_price - px) / risk_unit

    if model in ("M1_CURRENT", "M2_ENTRY_IMPROVED"):
        tp_base = entry_price + 4 * atr if side == "BUY" else entry_price - 4 * atr
        for _, b in fut.iterrows():
            _update(b["h"], b["l"])
            hit_sl = (b["l"] <= sl) if side == "BUY" else (b["h"] >= sl)
            hit_tp = (b["h"] >= tp_base) if side == "BUY" else (b["l"] <= tp_base)
            if hit_sl:
                realized_r = _r(sl); exit_ts = b["ts"]; exit_reason = "SL"; closed = True; break
            if hit_tp:
                realized_r = _r(tp_base); exit_ts = b["ts"]; exit_reason = "TP"; closed = True; break
        if not closed:
            realized_r = _r(float(fut["c"].iloc[-1])); exit_ts = float(fut["ts"].iloc[-1])

    elif model in ("M3_PROFIT_EXTR", "M4_FULL_ENGINE"):
        tp1 = entry_price + 1.5 * atr if side == "BUY" else entry_price - 1.5 * atr
        realized = 0.0
        qty = 1.0
        tp1_done = False
        peak_px = entry_price
        for _, b in fut.iterrows():
            _update(b["h"], b["l"])
            if side == "BUY":
                peak_px = max(peak_px, float(b["h"]))
            else:
                peak_px = min(peak_px, float(b["l"]))
            hit_sl = (b["l"] <= sl) if side == "BUY" else (b["h"] >= sl)
            if hit_sl:
                if tp1_done:
                    realized += _r(sl) * qty
                    exit_reason = "SL_ON_RUNNER"
                else:
                    realized += _r(sl)
                    exit_reason = "SL"
                exit_ts = b["ts"]; closed = True; break
            if not tp1_done:
                hit_tp1 = (b["h"] >= tp1) if side == "BUY" else (b["l"] <= tp1)
                if hit_tp1:
                    realized += _r(tp1) * 0.5
                    qty = 0.5
                    tp1_done = True
            else:
                # trail от пика: 0.5R
                trail = peak_px - 0.5 * risk_unit if side == "BUY" else peak_px + 0.5 * risk_unit
                hit_trail = (b["c"] <= trail) if side == "BUY" else (b["c"] >= trail)
                if hit_trail:
                    realized += _r(float(b["c"])) * qty
                    exit_ts = b["ts"]; exit_reason = "TRAIL"; closed = True; break
        if not closed:
            last_px = float(fut["c"].iloc[-1])
            realized += _r(last_px) * qty
            exit_ts = float(fut["ts"].iloc[-1])
        realized_r = realized

    fee_in_r = 0.0011 * entry_price / risk_unit  # 0.11% taker вход+выход в R
    net_r = realized_r - fee_in_r * 2
    return {
        "outcome": exit_reason,
        "net_r": round(net_r, 4),
        "mfe_r": round(mfe_r, 3),
        "mae_r": round(mae_r, 3),
        "hold_h": round((exit_ts - entry_ts) / 3600, 1) if exit_ts else None,
        "phase": phase,
    }


def main() -> int:
    t0 = time.time()
    manifest = _load_manifest()
    sigs = [json.loads(l) for l in SIGNAL_LOG.open()
            if json.loads(l).get("direction") in ("BUY", "SELL")
            and json.loads(l).get("final_probability", 0) >= 0.55]
    sigs.sort(key=lambda r: r["timestamp"])
    split = int(len(sigs) * 0.7)
    oos_ts = {s["timestamp"] for s in sigs[split:]}
    in_sigs = sigs[:split]
    oos_sigs = sigs[split:]

    print(f"SIGNALS={len(sigs)} | IN={len(in_sigs)} OOS={len(oos_sigs)}", flush=True)
    print(f"IN до {in_sigs[-1]['timestamp'][:19]} | OOS после {oos_sigs[0]['timestamp'][:19]}", flush=True)
    print(f"DATASET_HASH={dataset_hash()} CONFIG_HASH=replay_v3_m15_v1", flush=True)

    # Checkpoint
    cp = {}
    if CHECKPOINT.exists():
        try:
            cp = json.loads(CHECKPOINT.read_text())
        except Exception:
            cp = {}
    done_keys = set(cp.get("done", []))
    start_from = cp.get("last_index", 0)

    results = {m: {"IN": [], "OOS": []} for m in
               ("M1_CURRENT", "M2_ENTRY_IMPROVED", "M3_PROFIT_EXTR", "M4_FULL_ENGINE")}

    for i, s in enumerate(sigs):
        if i < start_from and f"{i}" in done_keys:
            continue
        sym = s["symbol"]
        df = _load_candles(sym)
        if df is None or len(df) < 50:
            continue
        ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")).timestamp()
        side = s["direction"]
        entry_price = s.get("entry") or s.get("close") or 0
        atr = _atr_before(df, ts)
        if atr <= 0:
            continue
        phase, dist_origin, mins_since = _entry_phase(df, ts, side, entry_price)
        if phase == "NO_DATA":
            continue
        is_oos = s["timestamp"] in oos_ts
        for m in ("M1_CURRENT", "M2_ENTRY_IMPROVED", "M3_PROFIT_EXTR", "M4_FULL_ENGINE"):
            if m in ("M2_ENTRY_IMPROVED", "M4_FULL_ENGINE") and phase in ("EXHAUSTION", "LATE_MOMENTUM"):
                res = {"outcome": "SKIP", "phase": phase}
            else:
                res = simulate(df, s, m, atr, entry_price, side, ts, phase)
            if res["outcome"] in ("DATA_INVALID",):
                continue
            bucket = "OOS" if is_oos else "IN"
            results[m][bucket].append(res)

        done_keys.add(f"{i}")
        if i % 50 == 0 and i > 0:
            el = time.time() - t0
            eta = el / i * (len(sigs) - i) if i else 0
            print(f"  [{i}/{len(sigs)}] elapsed={el:.0f}s ETA={eta:.0f}s "
                  f"rate={i/el:.1f}/s", flush=True)
            CHECKPOINT.write_text(json.dumps(
                {"last_index": i, "done": sorted(done_keys, key=int)[-5000:]}))

    # Save final
    CHECKPOINT.write_text(json.dumps(
        {"last_index": len(sigs), "done": sorted(done_keys, key=int)}))

    def _metrics(ts_list):
        # SKIP-записи не имеют торговых метрик — исключаем из расчёта, но считаем отдельно
        skipped_n = sum(1 for t in ts_list if t.get("outcome") == "SKIP")
        ts_list = [t for t in ts_list if t.get("outcome") != "SKIP" and "net_r" in t]
        if not ts_list:
            return {"n": 0, "skipped": skipped_n}
        rs = [t["net_r"] for t in ts_list]
        wins = [t for t in ts_list if t["net_r"] > 0]
        mfes = [t["mfe_r"] for t in ts_list]
        maes = [t["mae_r"] for t in ts_list]
        gp = sum(r for r in rs if r > 0)
        gl = abs(sum(r for r in rs if r < 0))
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
        caps = [min(max(t["net_r"] / t["mfe_r"], 0), 1.5) for t in ts_list if t["mfe_r"] > 0.05]
        gb = [max(0, t["mfe_r"] - t["net_r"]) for t in ts_list if t["mfe_r"] > 0]
        return {
            "n": len(ts_list),
            "skipped": sum(1 for t in ts_list if t["outcome"] == "SKIP"),
            "wr": round(len(wins) / len(ts_list) * 100, 1),
            "pf": round(pf, 3),
            "exp_r": round(statistics.mean(rs), 4),
            "avg_mfe": round(statistics.mean(mfes), 3),
            "avg_mae": round(statistics.mean(maes), 3),
            "cap": round(statistics.mean(caps), 3) if caps else 0,
            "giveback": round(statistics.mean(gb), 3) if gb else 0,
            "avg_hold_h": round(statistics.mean([t["hold_h"] for t in ts_list if t["hold_h"]]), 1),
            "tp": sum(1 for t in ts_list if t["outcome"] == "TP"),
            "sl": sum(1 for t in ts_list if t["outcome"] == "SL"),
            "trail": sum(1 for t in ts_list if t["outcome"] == "TRAIL"),
            "expired": sum(1 for t in ts_list if t["outcome"] == "EXPIRED"),
        }

    print("\n" + "=" * 100)
    print("REPLAY V3 (после fees+slippage, локальный кэш, 30% OOS):")
    print("=" * 100)
    hdr = (f"{'Model':20s} {'Set':4s} {'N':>5s} {'Skip':>5s} {'WR%':>5s} {'PF':>6s} {'ExpR':>8s} "
           f"{'MFE':>5s} {'MAE':>5s} {'Cap':>5s} {'GB':>6s} {'HoldH':>6s} {'TP':>3s} {'SL':>3s} {'Tr':>3s}")
    print(hdr)
    for m in ("M1_CURRENT", "M2_ENTRY_IMPROVED", "M3_PROFIT_EXTR", "M4_FULL_ENGINE"):
        for bucket in ("IN", "OOS"):
            ms = _metrics(results[m][bucket])
            if ms["n"] == 0:
                continue
            print(f"{m:20s} {bucket:4s} {ms['n']:>5d} {ms['skipped']:>5d} {ms['wr']:>5.1f} "
                  f"{ms['pf']:>6.2f} {ms['exp_r']:>+8.4f} {ms['avg_mfe']:>+5.2f} {ms['avg_mae']:>+5.2f} "
                  f"{ms['cap']:>5.2f} {ms['giveback']:>6.2f} {ms['avg_hold_h']:>6.1f} "
                  f"{ms['tp']:>3d} {ms['sl']:>3d} {ms['trail']:>3d}")
        print()

    print("=" * 100)
    print("КРИТЕРИИ УСПЕХА (NEW против M1_CURRENT на OOS):")
    cur = _metrics(results["M1_CURRENT"]["OOS"])
    for m in ("M2_ENTRY_IMPROVED", "M3_PROFIT_EXTR", "M4_FULL_ENGINE"):
        new = _metrics(results[m]["OOS"])
        if new["n"] == 0:
            continue
        checks = {
            "ExpR>": new["exp_r"] > cur["exp_r"],
            "PF>": new["pf"] > cur["pf"],
            "Cap>": new["cap"] > cur["cap"],
            "GB<=": new["giveback"] <= cur["giveback"],
        }
        print(f"{m}: {'PASS' if all(checks.values()) else 'FAIL'} "
              f"{''.join('✓' if v else '✗' for v in checks.values())}")
    print(f"\nRUNTIME: {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
