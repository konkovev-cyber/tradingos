#!/usr/bin/env python3
"""
Probability Calibration Audit + Post-Trigger Dynamics.

Вопросы (пользователь):
1. Немонотонность probability: непрерывная calibration curve prob vs исход.
   Для хорошо калиброванной модели ожидается монотонный рост. Данные ранее
   показали "0.55-0.60 хорошо, 0.60+ резко хуже" — проверяем форму кривой
   по шагу 0.01 со сглаживанием, не по грубым бакетам.
2. Post-trigger dynamics: как probability меняется ПОСЛЕ появления кандидата.
   Гипотеза: плохие сделки — те, у которых prob сразу начинает снижаться
   (0.57 -> 0.54 -> 0.49), а не "вспышки" сами по себе.

Метод (ретроспектива на накопленных логах, НЕ меняет контур):
- Входы = "пробеги" принятых сигналов (direction BUY/SELL, разрыв циклов <=10 мин),
  вход по первому циклу пробега, prob = prob первого цикла.
- Исход = close ± 2×ATR (1:1, как в контуре), обход 1h-свечей до TP/SL, горизонт 168ч.
- Calibration: 0.01-бины + скользящее окно ±0.02 (n, WR, PF, avgR).
- Post-trigger: среди кандидатов (prob>=0.55) — prob в следующих циклах того же
  символа; классы rising/falling/flat по изменению через 1 и 2 цикла; сравнение исходов.

Выход: печать + reports/probability_calibration_report.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle

SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
REPORT = Path("/root/tradingos/reports/probability_calibration_report.json")

RUN_GAP_SEC = 600      # max gap between consecutive accepted-signal cycles of one run
POST_GAP_SEC = 600     # window to look for the next cycle of the same symbol
MAX_HOURS = 168
SL_ATR = 2.0
TP_ATR = 2.0

CAL_MIN = 0.40         # calibration range start
CAL_MAX = 0.72         # calibration range end
CAL_STEP = 0.01
WINDOW = 0.02          # rolling window half-width


def load_rows() -> list[dict]:
    rows = []
    for line in SIGNAL_LOG.open("r"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        try:
            ts = datetime.fromisoformat(r["timestamp"]).timestamp()
        except Exception:
            continue
        rows.append({
            "symbol": r.get("symbol", ""),
            "ts": ts,
            "direction": r.get("direction"),
            "prob": r.get("final_probability", 0.0) or 0.0,
            "quality": r.get("quality", "NONE"),
            "score": r.get("score", 0) or 0,
            "close": r.get("close", 0.0) or 0.0,
            "atr": r.get("atr", 0.0) or 0.0,
        })
    return rows


def accepted_signal_runs(rows: list[dict]) -> tuple[list[dict], dict]:
    """Runs of accepted signals (direction BUY/SELL, any prob). Entry = first cycle."""
    per_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["direction"] in ("BUY", "SELL"):
            per_symbol[r["symbol"]].append(r)
    for sym in per_symbol:
        per_symbol[sym].sort(key=lambda x: x["ts"])

    entries = []
    meta = {"signal_cycles": 0, "runs": 0}
    for sym, sym_rows in per_symbol.items():
        cur = []
        prev_ts = None
        for r in sym_rows:
            meta["signal_cycles"] += 1
            if cur and (r["ts"] - prev_ts) <= RUN_GAP_SEC:
                cur.append(r)
            else:
                if cur:
                    entries.append(cur)
                cur = [r]
            prev_ts = r["ts"]
        if cur:
            entries.append(cur)
    meta["runs"] = len(entries)
    return entries, meta


async def fetch_klines(symbol: str) -> list[Candle]:
    import httpx

    all_c: list[Candle] = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=15) as client:
        for _ in range(6):
            params = {"category": "linear", "symbol": symbol, "interval": "60", "limit": 200}
            if end_ms:
                params["end"] = str(end_ms)
            resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            data = resp.json()
            rows = data.get("result", {}).get("list", [])
            if not rows:
                break
            for row in rows:
                all_c.append(Candle(
                    timestamp=int(row[0]), open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]),
                    volume=float(row[5]) if row[5] else 0.0,
                    symbol=symbol, timeframe="1h",
                ))
            end_ms = int(rows[-1][0]) - 1
            await asyncio.sleep(0.1)
    all_c.sort(key=lambda x: x.timestamp)
    return all_c


def eval_entry(entry_ts_ms: int, entry: float, sl: float, tp: float,
               direction: str, candles: list[Candle]) -> dict:
    idx = None
    for i, c in enumerate(candles):
        if c.timestamp * 1000 >= entry_ts_ms - 60 * 60 * 1000:
            idx = i
            break
    if idx is None or idx >= len(candles) - 1:
        return {"outcome": "NO_DATA", "hours": 0, "r": 0.0}

    outcome = "EXPIRED"
    exit_price = entry
    bars = 0
    for j in range(idx + 1, min(idx + MAX_HOURS + 1, len(candles))):
        c = candles[j]
        bars = j - idx
        if direction == "BUY":
            if c.low <= sl:
                outcome, exit_price = "SL", sl
                break
            if c.high >= tp:
                outcome, exit_price = "TP", tp
                break
        else:
            if c.high >= sl:
                outcome, exit_price = "SL", sl
                break
            if c.low <= tp:
                outcome, exit_price = "TP", tp
                break
    if outcome == "EXPIRED":
        exit_price = candles[min(idx + MAX_HOURS, len(candles) - 1)].close

    risk = abs(entry - sl)
    if direction == "BUY":
        r_m = (exit_price - entry) / risk if risk > 0 else 0.0
    else:
        r_m = (entry - exit_price) / risk if risk > 0 else 0.0
    return {"outcome": outcome, "hours": bars, "r": r_m}


def stats_of(results: list[dict]) -> dict:
    decided = [r for r in results if r["outcome"] in ("TP", "SL")]
    n = len(decided)
    wins = [r for r in decided if r["outcome"] == "TP"]
    losses = [r for r in decided if r["outcome"] == "SL"]
    wr = len(wins) / n * 100 if n else 0.0
    gp = sum(r["r"] for r in wins)
    gl = sum(abs(r["r"]) for r in losses)
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    avg_r = sum(r["r"] for r in decided) / n if n else 0.0
    return {
        "n": n,
        "winrate_pct": round(wr, 1),
        "pf": round(pf, 2) if pf != float("inf") else None,
        "avg_r": round(avg_r, 3),
        "expired": sum(1 for r in results if r["outcome"] == "EXPIRED"),
        "no_data": sum(1 for r in results if r["outcome"] == "NO_DATA"),
    }


def calibration_curve(entries: list[dict]) -> dict:
    """0.01 bins + rolling window ±0.02 over prob at entry."""
    by_bin: dict[str, list] = defaultdict(list)
    for e in entries:
        p = e["prob"]
        if CAL_MIN <= p < CAL_MAX:
            by_bin[f"{p:.2f}"].append(e)

    raw = {k: stats_of(v) for k, v in sorted(by_bin.items())}

    centers = [round(CAL_MIN + i * CAL_STEP, 2) for i in range(int((CAL_MAX - CAL_MIN) / CAL_STEP) + 1)]
    smoothed = {}
    for c in centers:
        window_entries = [e for e in entries
                          if (c - WINDOW) <= e["prob"] < (c + WINDOW)]
        st = stats_of(window_entries)
        st["center"] = c
        smoothed[f"{c:.2f}"] = st

    # best expectancy location (n >= 20)
    best = None
    for k, st in smoothed.items():
        if st["n"] >= 20 and (best is None or (st["avg_r"] or -99) > (best["avg_r"] or -99)):
            best = {"prob": k, **st}
    return {"raw_bins": raw, "smoothed": smoothed, "best_expectancy": best}


def post_trigger(rows: list[dict], entries: list[dict], outcomes: dict) -> dict:
    """For candidate entries (prob>=0.55): prob in next cycles of the same symbol."""
    per_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        per_symbol[r["symbol"]].append(r)
    for sym in per_symbol:
        per_symbol[sym].sort(key=lambda x: x["ts"])

    groups = {"rising": [], "falling": [], "flat": [], "no_next": []}
    details = []
    for entry in entries:
        if entry["prob"] < 0.55:
            continue
        series = per_symbol[entry["symbol"]]
        i0 = None
        for i, r in enumerate(series):
            if r["ts"] == entry["ts"] and r["direction"] == entry["direction"]:
                i0 = i
                break
        if i0 is None:
            continue
        nxt = []
        for r in series[i0 + 1:i0 + 3]:
            if r["ts"] - entry["ts"] <= POST_GAP_SEC:
                nxt.append(r)
            else:
                break
        key = (entry["symbol"], entry["ts"], entry["direction"])
        res = outcomes.get(key, {"outcome": "NO_DATA", "r": 0.0})
        rec = {
            "symbol": entry["symbol"],
            "prob_at_trigger": round(entry["prob"], 3),
            "prob_next1": round(nxt[0]["prob"], 3) if len(nxt) >= 1 else None,
            "prob_next2": round(nxt[1]["prob"], 3) if len(nxt) >= 2 else None,
            "delta1": round(nxt[0]["prob"] - entry["prob"], 3) if len(nxt) >= 1 else None,
            "outcome": res["outcome"],
            "r": res["r"],
        }
        details.append(rec)

        if len(nxt) >= 1:
            d1 = nxt[0]["prob"] - entry["prob"]
            if d1 >= 0.005:
                groups["rising"].append(res)
            elif d1 <= -0.005:
                groups["falling"].append(res)
            else:
                groups["flat"].append(res)
        else:
            groups["no_next"].append(res)

    return {"groups": {k: stats_of(v) for k, v in groups.items()}, "details": details}


def controls(evaluated: list[dict]) -> dict:
    """Confounder checks: direction, time split, top symbols (lo vs hi prob)."""
    lo = [e for e in evaluated if e["prob"] < 0.55]
    hi = [e for e in evaluated if e["prob"] >= 0.55]

    by_dir = {}
    for d in ("BUY", "SELL"):
        by_dir[d] = {
            "lo_prob": stats_of([e for e in lo if e["direction"] == d]),
            "hi_prob": stats_of([e for e in hi if e["direction"] == d]),
        }

    mid = sorted(e["ts"] for e in evaluated)[len(evaluated) // 2]
    halves = {}
    for name, mask in (("first_half", [e for e in evaluated if e["ts"] <= mid]),
                       ("second_half", [e for e in evaluated if e["ts"] > mid])):
        halves[name] = {
            "lo_prob": stats_of([e for e in mask if e["prob"] < 0.55]),
            "hi_prob": stats_of([e for e in mask if e["prob"] >= 0.55]),
        }

    by_symbol = {}
    per_sym: dict[str, list] = defaultdict(list)
    for e in evaluated:
        per_sym[e["symbol"]].append(e)
    for sym, sym_e in sorted(per_sym.items(), key=lambda kv: -len(kv[1]))[:10]:
        by_symbol[sym] = {
            "n_total": len(sym_e),
            "lo_prob": stats_of([e for e in sym_e if e["prob"] < 0.55]),
            "hi_prob": stats_of([e for e in sym_e if e["prob"] >= 0.55]),
        }

    return {"by_direction": by_dir, "time_split": halves, "by_symbol": by_symbol}


async def main():
    print("Loading signal_log.jsonl ...")
    rows = load_rows()
    print(f"  rows: {len(rows)}")

    entries, meta = accepted_signal_runs(rows)
    print(f"  accepted-signal runs (entries): {meta['runs']} ({meta['signal_cycles']} cycles)")

    symbols = sorted({e[0]["symbol"] for e in entries})
    print(f"  fetching klines for {len(symbols)} symbols ...")
    data: dict[str, list[Candle]] = {}
    for sym in symbols:
        data[sym] = await fetch_klines(sym)

    evaluated = []
    outcomes = {}
    for run in entries:
        first = run[0]
        entry = first["close"]
        atr = first["atr"]
        if entry <= 0 or atr <= 0:
            continue
        direction = first["direction"]
        if direction == "BUY":
            sl, tp = entry - atr * SL_ATR, entry + atr * TP_ATR
        else:
            sl, tp = entry + atr * SL_ATR, entry - atr * TP_ATR
        res = eval_entry(first["ts"] * 1000, entry, sl, tp, direction, data[first["symbol"]])
        rec = {"symbol": first["symbol"], "ts": first["ts"], "direction": direction,
               "prob": first["prob"], "run_len": len(run), **res}
        evaluated.append(rec)
        outcomes[(first["symbol"], first["ts"], direction)] = res

    curve = calibration_curve(evaluated)
    post = post_trigger(rows, evaluated, outcomes)
    ctrl = controls(evaluated)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "method": "entry=first cycle of accepted-signal run (gap<=10min); outcome=close±2ATR 1:1, walk 1h bars to TP/SL, horizon 168h",
        "calibration": {
            "raw_bins": curve["raw_bins"],
            "smoothed": curve["smoothed"],
            "best_expectancy": curve["best_expectancy"],
        },
        "post_trigger": post["groups"],
        "controls": ctrl,
        "overall": stats_of(evaluated),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print("\n" + "=" * 78)
    print("CALIBRATION CURVE (rolling ±0.02 по prob на входе)")
    print("=" * 78)
    print(f"{'prob':>5} {'n':>4} {'WR%':>6} {'PF':>6} {'avgR':>7}   'prob':>5 {'n':>4} {'WR%':>6} {'PF':>6} {'avgR':>7}")
    sm = curve["smoothed"]
    keys = [k for k in sm if sm[k]["n"] >= 10]
    for i in range(0, len(keys), 2):
        left, right = keys[i], (keys[i + 1] if i + 1 < len(keys) else None)
        l, r = sm[left], (sm[right] if right else None)
        row = f"{left:>5} {l['n']:>4} {l['winrate_pct']:>6} {str(l['pf']):>6} {l['avg_r']:>+7.2f}"
        if r:
            row += f"   {right:>5} {r['n']:>4} {r['winrate_pct']:>6} {str(r['pf']):>6} {r['avg_r']:>+7.2f}"
        print(row)
    b = curve["best_expectancy"]
    if b:
        print(f"\nмаксимум expectancy (n>=20): prob={b['prob']} WR={b['winrate_pct']}% "
              f"PF={b['pf']} avgR={b['avg_r']:+.2f} (n={b['n']})")

    print("\n" + "=" * 78)
    print("POST-TRIGGER DYNAMICS (кандидаты prob>=0.55, следующая минута)")
    print("=" * 78)
    print(f"{'class':<10} {'n':>4} {'WR%':>6} {'PF':>6} {'avgR':>7}")
    for k, v in post["groups"].items():
        print(f"{k:<10} {v['n']:>4} {v['winrate_pct']:>6} {str(v['pf']):>6} {v['avg_r']:>+7.2f}")

    o = report["overall"]
    print(f"\nOVERALL: n={o['n']} WR={o['winrate_pct']}% PF={o['pf']} avgR={o['avg_r']:+.2f}")

    print("\n" + "=" * 78)
    print("CONTROLS (конфаундеры: направление, время, символы) — lo prob<0.55 vs hi prob>=0.55")
    print("=" * 78)
    for d, v in ctrl["by_direction"].items():
        print(f"  {d}: lo n={v['lo_prob']['n']:>4} WR={v['lo_prob']['winrate_pct']:>5}% "
              f"PF={v['lo_prob']['pf']:<6} | hi n={v['hi_prob']['n']:>3} WR={v['hi_prob']['winrate_pct']:>5}% "
              f"PF={v['hi_prob']['pf']}")
    for name, v in ctrl["time_split"].items():
        print(f"  {name}: lo n={v['lo_prob']['n']:>4} WR={v['lo_prob']['winrate_pct']:>5}% "
              f"| hi n={v['hi_prob']['n']:>3} WR={v['hi_prob']['winrate_pct']:>5}%")
    for sym, v in ctrl["by_symbol"].items():
        print(f"  {sym:<12} n={v['n_total']:>4} | lo WR={v['lo_prob']['winrate_pct']:>5}% (n={v['lo_prob']['n']}) "
              f"| hi WR={v['hi_prob']['winrate_pct']:>5}% (n={v['hi_prob']['n']})")

    print(f"\nReport: {REPORT}")


if __name__ == "__main__":
    asyncio.run(main())
