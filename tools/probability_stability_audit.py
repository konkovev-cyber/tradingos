#!/usr/bin/env python3
"""
Probability Stability Audit — насколько probability держится во времени?

Гипотеза (пользователь): лучшие сделки имеют prob, которая держится 2-3
последовательных цикла (60s), а худшие возникают при "вспышках" probability
на один цикл.

Метод (ретроспектива на накопленных логах, НЕ меняет архитектуру):
1. signal_log.jsonl → для каждого символа ряд (ts, prob, quality, score, close, atr).
2. Кандидаты = направленные сигналы prob>=0.55, quality MEDIUM+, score>=55.
3. "Пробег" (run) = последовательные циклы-кандидаты одного символа с разрывом <=10 мин.
   Длина пробега = сколько циклов probability держалась >=0.55.
4. Исход пробега: вход по close первого цикла, SL/TP = close ± 2×ATR (1:1, как в контуре),
   обход 1h-свечей до TP/SL (до 168ч; winrate также на горизонте 2ч).
5. Сравнение по длине пробега: 1 цикл (вспышка) vs 2 vs 3+.

Выход: печать + reports/probability_stability_report.json
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
REPORT = Path("/root/tradingos/reports/probability_stability_report.json")

PROB_MIN = 0.55
QUALITY_OK = ("MEDIUM", "GOOD", "EXCELLENT")
SCORE_MIN = 55
RUN_GAP_SEC = 600      # max gap between consecutive candidate cycles of one run
MAX_HOURS = 168        # outcome horizon
HOURS_2H = 2           # short-horizon winrate (калибровка пользователя)

SL_ATR = 2.0
TP_ATR = 2.0


def load_rows() -> list[dict]:
    """Stream signal_log.jsonl → rows with parsed timestamp (unix)."""
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


def is_candidate(r: dict) -> bool:
    return (r["direction"] in ("BUY", "SELL")
            and r["prob"] >= PROB_MIN
            and r["quality"] in QUALITY_OK
            and r["score"] >= SCORE_MIN)


def build_runs(rows: list[dict]) -> tuple[list[list[dict]], dict]:
    """Group candidate cycles into runs per symbol."""
    per_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        per_symbol[r["symbol"]].append(r)
    for sym in per_symbol:
        per_symbol[sym].sort(key=lambda x: x["ts"])

    runs: list[list[dict]] = []
    cand_cycles = 0
    for sym, sym_rows in per_symbol.items():
        cur: list[dict] = []
        prev_cand_ts = None
        for r in sym_rows:
            if is_candidate(r):
                cand_cycles += 1
                if cur and (r["ts"] - prev_cand_ts) <= RUN_GAP_SEC:
                    cur.append(r)
                else:
                    if cur:
                        runs.append(cur)
                    cur = [r]
                prev_cand_ts = r["ts"]
            else:
                if cur:
                    runs.append(cur)
                cur = []
                prev_cand_ts = None
        if cur:
            runs.append(cur)
    return runs, {"candidate_cycles": cand_cycles, "runs": len(runs)}


async def fetch_klines(symbol: str) -> list[Candle]:
    """Fetch ~14 days of 1h candles (paginated)."""
    import httpx

    all_c: list[Candle] = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=15) as client:
        for _ in range(6):  # up to 6 pages x 200 = 1200 bars
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
    """Walk forward from the bar at/after entry_ts_ms until TP/SL/expiry."""
    idx = None
    for i, c in enumerate(candles):
        if c.timestamp * 1000 >= entry_ts_ms - 60 * 60 * 1000:  # allow up to 1h slack
            idx = i
            break
    if idx is None or idx >= len(candles) - 1:
        return {"outcome": "NO_DATA", "hours": 0, "r": 0.0, "hit_2h": None}

    outcome = "EXPIRED"
    exit_price = entry
    bars = 0
    hit_2h = None
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
        if bars <= HOURS_2H and outcome == "EXPIRED":
            hit_2h = "OPEN"
    if outcome in ("TP", "SL") and bars <= HOURS_2H:
        hit_2h = outcome
    if outcome == "EXPIRED":
        exit_price = candles[min(idx + MAX_HOURS, len(candles) - 1)].close

    risk = abs(entry - sl)
    if direction == "BUY":
        r_m = (exit_price - entry) / risk if risk > 0 else 0.0
    else:
        r_m = (entry - exit_price) / risk if risk > 0 else 0.0
    return {"outcome": outcome, "hours": bars, "r": r_m, "hit_2h": hit_2h}


def bucket_stats(results: list[dict]) -> dict:
    """winrate (TP vs TP+SL), PF, avg R, 2h winrate."""
    decided = [r for r in results if r["outcome"] in ("TP", "SL")]
    n = len(decided)
    wins = [r for r in decided if r["outcome"] == "TP"]
    losses = [r for r in decided if r["outcome"] == "SL"]
    wr = len(wins) / n * 100 if n else 0.0
    gp = sum(r["r"] for r in wins)
    gl = sum(abs(r["r"]) for r in losses)
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    avg_r = sum(r["r"] for r in decided) / n if n else 0.0
    decided_2h = [r for r in decided if r["hit_2h"] in ("TP", "SL")]
    wr_2h = (sum(1 for r in decided_2h if r["hit_2h"] == "TP") / len(decided_2h) * 100
             if decided_2h else None)
    return {
        "n": n,
        "winrate_pct": round(wr, 1),
        "pf": round(pf, 2) if pf != float("inf") else None,
        "avg_r": round(avg_r, 3),
        "expired": sum(1 for r in results if r["outcome"] == "EXPIRED"),
        "no_data": sum(1 for r in results if r["outcome"] == "NO_DATA"),
        "winrate_2h_pct": round(wr_2h, 1) if wr_2h is not None else None,
        "median_hours": _median([r["hours"] for r in decided]) if decided else None,
    }


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def run_bucket(run_len: int) -> str:
    if run_len == 1:
        return "1_cycle_flash"
    if run_len == 2:
        return "2_cycles"
    return "3plus_cycles"


async def main():
    print("Loading signal_log.jsonl ...")
    rows = load_rows()
    print(f"  rows: {len(rows)}")

    runs, meta = build_runs(rows)
    print(f"  candidate cycles: {meta['candidate_cycles']}, runs: {meta['runs']}")

    symbols = sorted({run[0]["symbol"] for run in runs})
    print(f"  fetching klines for {len(symbols)} symbols ...")
    data: dict[str, list[Candle]] = {}
    for sym in symbols:
        data[sym] = await fetch_klines(sym)

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    prob_bucket: dict[str, list[dict]] = defaultdict(list)
    all_results = []
    for run in runs:
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
        res["symbol"] = first["symbol"]
        res["run_len"] = len(run)
        res["prob_at_entry"] = round(first["prob"], 3)
        res["prob_last"] = round(run[-1]["prob"], 3)
        res["prob_trend"] = round(run[-1]["prob"] - first["prob"], 3)
        all_results.append(res)
        by_bucket[run_bucket(len(run))].append(res)
        prob_bucket["p_55_60" if first["prob"] < 0.60 else "p_60plus"].append(res)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "method": "run = consecutive candidate cycles (gap<=10min); outcome = close±2ATR, 1:1, walk 1h bars to TP/SL, horizon 168h",
        "by_run_length": {k: bucket_stats(v) for k, v in sorted(by_bucket.items())},
        "by_prob_at_entry": {k: bucket_stats(v) for k, v in sorted(prob_bucket.items())},
        "overall": bucket_stats(all_results),
        "runs_detail": [{k: r[k] for k in ("symbol", "run_len", "prob_at_entry", "prob_last",
                                          "prob_trend", "outcome", "hours", "r")}
                        for r in all_results],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print("\n" + "=" * 70)
    print("PROBABILITY STABILITY AUDIT (вход по первому циклу пробега)")
    print("=" * 70)
    print(f"{'bucket':<18} {'n':>4} {'WR%':>6} {'WR2h%':>7} {'PF':>6} {'avgR':>7} {'med h':>6}")
    for k, v in report["by_run_length"].items():
        print(f"{k:<18} {v['n']:>4} {v['winrate_pct']:>6} "
              f"{str(v['winrate_2h_pct']):>7} {str(v['pf']):>6} {v['avg_r']:>+7.2f} "
              f"{str(v['median_hours']):>6}")
    print(f"{'OVERALL':<18} {report['overall']['n']:>4} {report['overall']['winrate_pct']:>6} "
          f"{str(report['overall']['winrate_2h_pct']):>7} {str(report['overall']['pf']):>6} "
          f"{report['overall']['avg_r']:>+7.2f} {str(report['overall']['median_hours']):>6}")
    print("\nпо prob на входе:")
    for k, v in report["by_prob_at_entry"].items():
        print(f"  {k:<14} n={v['n']:>3} WR={v['winrate_pct']}% PF={v['pf']} avgR={v['avg_r']:+.2f}")
    print(f"\nReport: {REPORT}")


if __name__ == "__main__":
    asyncio.run(main())
