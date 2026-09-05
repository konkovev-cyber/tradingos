#!/usr/bin/env python3
"""
Transfer Efficiency Audit — на каком этапе контур меняет ожидание.

Модель vs торговая система (рамка пользователя):
  probability — свойство МОДЕЛИ (классификатор);
  TP/SL/ranking/Top-1/cooldown — свойства СИСТЕМЫ.
  PF(сырые сигналы) и PF(сделки) могут не совпадать — здесь мы меряем КАЖДЫЙ переход.

Этапы (единая геометрия 2:1, SL=2ATR, TP=4ATR — как в proposals контура):
  1. raw         — все пробеги принятых сигналов (direction BUY/SELL, любой prob)
  2. candidates  — те же пробеги с prob>=0.55, quality MEDIUM+, score>=55
  3. ranked      — реплей отбора контура: окно 60с + лучший по prob*score +
                   ADX>=20 + символьный cooldown 1h (логика run_observation)

Результат: PF/WR/avgR на каждом этапе (+ split BUY/SELL, чтобы не спутать
режим с эффектом отбора). Ретроспектива на логах; контур НЕ меняется.

Выход: печать + reports/transfer_efficiency_report.json
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
REPORT = Path("/root/tradingos/reports/transfer_efficiency_report.json")

RUN_GAP_SEC = 600
MAX_HOURS = 168
SL_ATR = 2.0
TP_ATR = 4.0          # 2:1 RR — реальная геометрия proposals контура

PROB_MIN = 0.55
QUALITY_OK = ("MEDIUM", "GOOD", "EXCELLENT")
SCORE_MIN = 55
ADX_MIN = 20
COOLDOWN_SEC = 3600
RANK_WINDOW_SEC = 60


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
            "adx": r.get("adx", 0.0) or 0.0,
            "close": r.get("close", 0.0) or 0.0,
            "atr": r.get("atr", 0.0) or 0.0,
        })
    return rows


def signal_runs(rows: list[dict]) -> list[list[dict]]:
    """Runs of accepted signals (direction BUY/SELL, any prob). Entry = first cycle."""
    per_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["direction"] in ("BUY", "SELL"):
            per_symbol[r["symbol"]].append(r)
    for sym in per_symbol:
        per_symbol[sym].sort(key=lambda x: x["ts"])

    runs: list[list[dict]] = []
    for sym, sym_rows in per_symbol.items():
        cur = []
        prev_ts = None
        for r in sym_rows:
            if cur and (r["ts"] - prev_ts) <= RUN_GAP_SEC:
                cur.append(r)
            else:
                if cur:
                    runs.append(cur)
                cur = [r]
            prev_ts = r["ts"]
        if cur:
            runs.append(cur)
    return runs


def is_candidate(r: dict) -> bool:
    return (r["direction"] in ("BUY", "SELL")
            and r["prob"] >= PROB_MIN
            and r["quality"] in QUALITY_OK
            and r["score"] >= SCORE_MIN)


def replay_ranking(candidate_runs: list[list[dict]]) -> list[list[dict]]:
    """Реплей отбора run_observation: окно 60с → лучший по prob*score → ADX>=20 → cooldown 1h."""
    ordered = sorted(candidate_runs, key=lambda run: run[0]["ts"])
    selected: list[list[dict]] = []
    pending: list[list[dict]] = []
    last_proposal = float("-inf")
    cooldown_until: dict[str, float] = {}

    for run in ordered:
        first = run[0]
        if first["ts"] >= last_proposal + RANK_WINDOW_SEC:
            if pending:
                best = None
                for cand in pending:
                    c = cand[0]
                    if c["adx"] < ADX_MIN or cooldown_until.get(c["symbol"], 0) > c["ts"]:
                        continue
                    if best is None or c["prob"] * c["score"] > best[0]["prob"] * best[0]["score"]:
                        best = cand
                if best is not None:
                    selected.append(best)
                    last_proposal = best[0]["ts"]
                    cooldown_until[best[0]["symbol"]] = best[0]["ts"] + COOLDOWN_SEC
                pending = []
            pending.append(run)
        else:
            pending.append(run)
    if pending:
        best = None
        for cand in pending:
            c = cand[0]
            if c["adx"] < ADX_MIN or cooldown_until.get(c["symbol"], 0) > c["ts"]:
                continue
            if best is None or c["prob"] * c["score"] > best[0]["prob"] * best[0]["score"]:
                best = cand
        if best is not None:
            selected.append(best)
    return selected


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
    return {"n": n, "winrate_pct": round(wr, 1),
            "pf": round(pf, 2) if pf != float("inf") else None,
            "avg_r": round(avg_r, 3),
            "expired": sum(1 for r in results if r["outcome"] == "EXPIRED"),
            "no_data": sum(1 for r in results if r["outcome"] == "NO_DATA")}


def evaluate_runs(runs: list[list[dict]], data: dict) -> list[dict]:
    out = []
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
        out.append({"symbol": first["symbol"], "direction": direction,
                    "prob": first["prob"], "adx": first["adx"], "score": first["score"],
                    **res})
    return out


def split_stats(results: list[dict]) -> dict:
    return {
        "all": stats_of(results),
        "BUY": stats_of([r for r in results if r["direction"] == "BUY"]),
        "SELL": stats_of([r for r in results if r["direction"] == "SELL"]),
    }


async def main():
    print("Loading signal_log.jsonl ...")
    rows = load_rows()
    print(f"  rows: {len(rows)}")

    all_runs = signal_runs(rows)
    cand_runs = [r for r in all_runs if is_candidate(r[0])]
    ranked_runs = replay_ranking(cand_runs)
    print(f"  raw runs={len(all_runs)}, candidate runs={len(cand_runs)}, ranked runs={len(ranked_runs)}")

    symbols = sorted({r[0]["symbol"] for r in all_runs})
    print(f"  fetching klines for {len(symbols)} symbols ...")
    data: dict[str, list[Candle]] = {}
    for sym in symbols:
        data[sym] = await fetch_klines(sym)

    stages = {
        "raw": evaluate_runs(all_runs, data),
        "candidates": evaluate_runs(cand_runs, data),
        "ranked_top1": evaluate_runs(ranked_runs, data),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "единая геометрия 2:1 (SL=2ATR, TP=4ATR), горизонт 168h; ranked = реплей (окно 60s, prob*score, ADX>=20, cooldown 1h)",
        "stages": {k: split_stats(v) for k, v in stages.items()},
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print("\n" + "=" * 82)
    print("TRANSFER EFFICIENCY (геометрия 2:1 для всех этапов — изолируем отбор от TP/SL)")
    print("=" * 82)
    hdr = f"{'stage':<14} {'n':>4} {'WR%':>6} {'PF':>6} {'avgR':>7} | {'BUY n':>6} {'WR%':>6} {'PF':>6} | {'SELL n':>6} {'WR%':>6} {'PF':>6}"
    print(hdr)
    print("-" * 82)
    for name, res in stages.items():
        s = split_stats(res)
        print(f"{name:<14} {s['all']['n']:>4} {s['all']['winrate_pct']:>6} {str(s['all']['pf']):>6} "
              f"{s['all']['avg_r']:>+7.2f} | {s['BUY']['n']:>6} {s['BUY']['winrate_pct']:>6} "
              f"{str(s['BUY']['pf']):>6} | {s['SELL']['n']:>6} {s['SELL']['winrate_pct']:>6} "
              f"{str(s['SELL']['pf']):>6}")

    o, c, r = report["stages"]["raw"]["all"], report["stages"]["candidates"]["all"], report["stages"]["ranked_top1"]["all"]
    print("\nпереходы (avgR):")
    print(f"  raw -> candidates:  {o['avg_r']:+.2f} -> {c['avg_r']:+.2f}  (Δ {c['avg_r'] - o['avg_r']:+.2f}R, n {o['n']} -> {c['n']})")
    print(f"  candidates -> ranked: {c['avg_r']:+.2f} -> {r['avg_r']:+.2f}  (Δ {r['avg_r'] - c['avg_r']:+.2f}R, n {c['n']} -> {r['n']})")
    print(f"  raw -> ranked:     {o['avg_r']:+.2f} -> {r['avg_r']:+.2f}  (Δ {r['avg_r'] - o['avg_r']:+.2f}R)")
    print(f"\nReport: {REPORT}")


if __name__ == "__main__":
    asyncio.run(main())
