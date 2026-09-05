#!/usr/bin/env python3
"""
Trade Genome Join — объединяет OPEN и CLOSE записи сделок в единый геном.

OPEN  — memory/trade_genome.jsonl (entry-features + expected_R, пишется при fill)
CLOSE — logs/trades/trade_results.jsonl (outcome, MFE, MAE, holding, realized PnL)

После объединения считается диагностика expected_R vs realized_R:
  где модель систематически переоценивает себя (expected_R > realized_R),
  где недооценивает. Через 100-200 сделок это заменяет очередную гипотезу-фильтр.

ПРИМЕЧАНИЕ о калибровке: expected_R = (rr+1)*prob - 1 — модельное ожидание при
фиксированном RR=2. Смысл prob ещё сверяется с прежней калибровкой ("58.5% на 2ч" —
вероятно направление цены, а не TP-hit), поэтому absolute expected_R пока ориентировочный;
сравнение по группам (например по prob-бакетам) — надёжнее отдельных значений.

Не меняет контур. Чистый анализ.
Выход: печать + reports/trade_genome_analysis.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENOME = Path("/root/tradingos/memory/trade_genome.jsonl")
RESULTS = Path("/root/tradingos/logs/trades/trade_results.jsonl")
REPORT = Path("/root/tradingos/reports/trade_genome_analysis.json")

ENTRY_TOLERANCE = 0.01   # |entry_open - entry_close| / entry_open
MAX_HOURS_OPEN = 14 * 24


def load_open() -> list[dict]:
    if not GENOME.exists():
        return []
    out = []
    for line in GENOME.open("r"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") != "OPEN":
            continue
        try:
            r["_ts"] = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            continue
        out.append(r)
    return out


def load_close() -> list[dict]:
    if not RESULTS.exists():
        return []
    out = []
    for line in RESULTS.open("r"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("status") != "CLOSED":
            continue
        try:
            r["_ts"] = datetime.fromisoformat(r["timestamp"]).timestamp()
        except Exception:
            continue
        r["_side"] = "BUY" if str(r.get("side", "")).lower() == "buy" else "SELL"
        out.append(r)
    return out


def join(opens: list[dict], closes: list[dict]) -> list[dict]:
    """Open -> ближайший CLOSED того же symbol/side с entry ≈ и close_ts > open_ts."""
    joined = []
    for o in opens:
        best_c, best_gap = None, None
        for c in closes:
            if c["symbol"] != o["symbol"] or c["_side"] != o["side"]:
                continue
            entry = o.get("entry") or 0
            if entry <= 0:
                continue
            if abs(c["entry"] - entry) / entry > ENTRY_TOLERANCE:
                continue
            if c["_ts"] <= o["_ts"] or c["_ts"] - o["_ts"] > MAX_HOURS_OPEN * 3600:
                continue
            gap = c["_ts"] - o["_ts"]
            if best_c is None or gap < best_gap:
                best_c, best_gap = c, gap
        if best_c is None:
            joined.append({"open": o, "close": None, "joined": False})
        else:
            joined.append({"open": o, "close": best_c, "joined": True})
    return joined


def realized_r(o: dict, c: dict) -> float | None:
    """realized R в единицах риска (как expected_R): net_pnl / (size * |entry - sl|)."""
    entry = o.get("entry") or 0
    sl = o.get("sl") or 0
    size = c.get("size") or 0
    if entry <= 0 or sl <= 0 or not size:
        return None
    risk = abs(entry - sl) * size
    if risk <= 0:
        return None
    return (c.get("net_pnl") or 0) / risk


def summarize(rows: list[dict]) -> dict:
    decided = []
    for row in rows:
        if not row["joined"] or row["close"] is None:
            continue
        r_r = realized_r(row["open"], row["close"])
        if r_r is None:
            continue
        decided.append({
            "symbol": row["open"]["symbol"],
            "side": row["open"]["side"],
            "prob": row["open"].get("prob"),
            "score": row["open"].get("score"),
            "adx": row["open"].get("adx"),
            "expected_R": row["open"].get("expected_R"),
            "realized_R": round(r_r, 3),
            "outcome": row["close"].get("outcome"),
            "mfe_r": row["close"].get("mfe_peak_r"),
            "mae_r": row["close"].get("mae_trough_r"),
            "holding_hours": round((row["close"]["_ts"] - row["open"]["_ts"]) / 3600, 2),
            "entry": row["open"].get("entry"),
            "utc_hour": row["open"].get("utc_hour"),
            "weekday": row["open"].get("weekday"),
        })
    n = len(decided)
    if not n:
        return {"n": 0}

    def agg(items, key):
        vals = [x[key] for x in items if x.get(key) is not None]
        return (sum(vals) / len(vals)) if vals else None

    bias = [x["realized_R"] - x["expected_R"] for x in decided]
    return {
        "n": n,
        "winrate_pct": round(sum(1 for x in decided if x["outcome"] == "TP") / n * 100, 1),
        "avg_expected_R": round(agg(decided, "expected_R") or 0, 3),
        "avg_realized_R": round(agg(decided, "realized_R") or 0, 3),
        "avg_bias_R": round(sum(bias) / n, 3),   # + = недооценивает, - = переоценивает
        "by_prob_bucket": {},
        "trades": decided,
    }


def main():
    opens = load_open()
    closes = load_close()
    print(f"OPEN records: {len(opens)}, CLOSED records: {len(closes)}")
    rows = join(opens, closes)
    joined_n = sum(1 for r in rows if r["joined"])
    print(f"joined: {joined_n}/{len(rows)}")

    summary = summarize(rows)
    print(f"\n=== TRADE GENOME: expected vs realized R ===")
    if summary["n"] == 0:
        print("нет объединённых сделок (пока нет закрытых LIVE MICRO сделок)")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "opens": len(opens), "closes": len(closes), "joined": joined_n,
            "summary": summary,
        }, indent=2, ensure_ascii=False))
        print(f"\nReport: {REPORT}")
        return
    print(f"n={summary['n']}  WR={summary['winrate_pct']}%")
    print(f"avg expected_R: {summary['avg_expected_R']:+.3f}")
    print(f"avg realized_R: {summary['avg_realized_R']:+.3f}")
    print(f"avg bias (realized-expected): {summary['avg_bias_R']:+.3f} "
          f"(+ = модель недооценивает, - = переоценивает)")
    if summary["n"]:
        print(f"\nсделки:")
        for t in summary["trades"]:
            print(f"  {t['symbol']:<12} {t['side']:<4} prob={t['prob']:.2f} "
                  f"exp={t['expected_R']:+.2f} real={t['realized_R']:+.2f} "
                  f"{str(t['outcome']):<6} mfe={t['mfe_r']} mae={t['mae_r']} "
                  f"hold={t['holding_hours']}h")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "opens": len(opens), "closes": len(closes), "joined": joined_n,
        "summary": summary,
    }, indent=2, ensure_ascii=False))
    print(f"\nReport: {REPORT}")


if __name__ == "__main__":
    main()
