"""
tools/position_guard_outcome.py
Outcome Tracker v1 — анализирует историю Guard-сигналов и считает ROI.

READ-ONLY. Не трогает Guard, PIE, адаптер или позиции.
Только читает:
  - position_guard_shadow.jsonl (сигналы Guard)
  - tradingos_data.db (PIE: последующие snapshots для расчёта MFE/MAE после сигнала)

Для каждого сигнала MOVE_SL считает:
  - Достигла ли цена proposed_stop (защита прибыли)?
  - Какой был MFE/MAE после сигнала?
  - Категория: SUCCESS_PROTECTION / NEUTRAL / MISSED_UPSIDE / FALSE_SIGNAL

Usage:
    python3 tools/position_guard_outcome.py
    python3 tools/position_guard_outcome.py --since 24h
    python3 tools/position_guard_outcome.py --json
"""
import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SHADOW_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")
PIE_DB = Path("/root/tradingos/tradingos_data.db")

logger = logging.getLogger("position_guard_outcome")


def parse_since(s: str) -> datetime:
    unit = s[-1]
    value = int(s[:-1])
    now = datetime.now(timezone.utc)
    if unit == "h":
        return now - timedelta(hours=value)
    if unit == "m":
        return now - timedelta(minutes=value)
    if unit == "d":
        return now - timedelta(days=value)
    raise ValueError(f"Unsupported duration: {s}")


@dataclass
class GuardSignal:
    evaluation_id: str
    timestamp: datetime
    symbol: str
    side: str
    mark_price: float
    proposed_stop: Optional[float]
    pnl_pct: float
    mfe_pct: float
    health: float
    confidence: float
    position_id: str = ""


@dataclass
class OutcomeResult:
    evaluation_id: str
    symbol: str
    side: str
    signal_time: datetime
    signal_pnl_pct: float
    proposed_stop: Optional[float]
    actual_pnl_pct: Optional[float] = None
    actual_close_price: Optional[float] = None
    mfe_after_signal_pct: Optional[float] = None
    mae_after_signal_pct: Optional[float] = None
    stop_was_hit: Optional[bool] = None
    category: str = "PENDING"
    saved_pct: Optional[float] = None
    note: str = ""


def load_move_sl_signals(since: datetime) -> List[GuardSignal]:
    """Читает все MOVE_SL сигналы из shadow log с timestamp >= since."""
    if not SHADOW_LOG.exists():
        return []
    signals: List[GuardSignal] = []
    with SHADOW_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("decision", {}).get("action") != "MOVE_SL":
                    continue
                ts = datetime.fromisoformat(rec["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < since:
                    continue
                signals.append(GuardSignal(
                    evaluation_id=rec.get("evaluation_id", ""),
                    timestamp=ts,
                    symbol=rec["symbol"],
                    side=rec["side"],
                    mark_price=float(rec["snapshot"]["mark_price"] or 0.0),
                    proposed_stop=rec["decision"].get("proposed_stop"),
                    pnl_pct=float(rec["snapshot"]["pnl_pct"] or 0.0),
                    mfe_pct=float(rec["snapshot"]["mfe_pct"] or 0.0),
                    health=float(rec["snapshot"]["health"] or 0.0),
                    confidence=float(rec["decision"].get("confidence", 0.0)),
                    position_id=rec.get("position_id", "") or "",
                ))
            except Exception as e:
                logger.debug(f"skip malformed record: {e}")
                continue
    return signals


def load_position_outcome(symbol: str, side: str, signal_time: datetime, position_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Для позиции, по которой был сигнал, читает PIE events
    (сначала после signal_time, потом последний известный) и возвращает:
    actual_pnl_pct, mfe_after, mae_after, last_price.

    Fallback: если после signal_time ничего нет (позиция закрыта и PIE
    перестал её обновлять), берём последний известный event и
    помечаем это в результате через флаг staleness.
    """
    if not PIE_DB.exists():
        return None
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if position_id:
            cur.execute(
                """
                SELECT pnl_pct, current_price, max_profit_seen, max_loss_seen, timestamp_utc
                FROM position_events
                WHERE position_id = ? AND timestamp_utc > ?
                ORDER BY id ASC
                """,
                (position_id, signal_time.isoformat()),
            )
        else:
            cur.execute(
                """
                SELECT pnl_pct, current_price, max_profit_seen, max_loss_seen, timestamp_utc
                FROM position_events
                WHERE symbol = ? AND side = ? AND timestamp_utc > ?
                ORDER BY id ASC
                """,
                (symbol, side, signal_time.isoformat()),
            )
        after_rows = cur.fetchall()

        if not after_rows and position_id:
            cur.execute(
                """
                SELECT pnl_pct, current_price, max_profit_seen, max_loss_seen, timestamp_utc
                FROM position_events
                WHERE position_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (position_id,),
            )
            last_known = cur.fetchall()
        else:
            last_known = []

        conn.close()
    except Exception as e:
        logger.warning(f"PIE read for {symbol} after {signal_time} failed: {e}")
        return None

    if after_rows:
        rows = after_rows
        staleness = "fresh"
    elif last_known:
        rows = last_known
        staleness = "stale_known"
    else:
        return None

    last = rows[-1]
    mfe_after_pct = max((float(r["max_profit_seen"] or 0) for r in rows), default=0.0) * 100
    mae_after_pct = min((float(r["max_loss_seen"] or 0) for r in rows), default=0.0) * 100

    return {
        "actual_pnl_pct": float(last["pnl_pct"] or 0.0) * 100,
        "last_price": float(last["current_price"] or 0.0),
        "mfe_after_pct": mfe_after_pct,
        "mae_after_pct": mae_after_pct,
        "last_event_time": last["timestamp_utc"],
        "staleness": staleness,
    }


def classify(signal: GuardSignal, outcome: Optional[Dict[str, Any]]) -> OutcomeResult:
    """
    Классифицирует результат сигнала. Категории:
      - SUCCESS_PROTECTION: stop был достигнут, цена не ушла сильно выше
      - NEUTRAL: разница в пределах шума
      - MISSED_UPSIDE: цена ушла сильно выше proposed_stop
      - FALSE_SIGNAL: защита сработала преждевременно
    """
    result = OutcomeResult(
        evaluation_id=signal.evaluation_id,
        symbol=signal.symbol,
        side=signal.side,
        signal_time=signal.timestamp,
        signal_pnl_pct=signal.pnl_pct,
        proposed_stop=signal.proposed_stop,
    )

    if outcome is None:
        result.category = "STALE_SIGNAL"
        result.note = "No PIE data found (position likely closed before signal)"
        return result

    result.actual_pnl_pct = outcome["actual_pnl_pct"]
    result.actual_close_price = outcome["last_price"]
    result.mfe_after_signal_pct = outcome["mfe_after_pct"]
    result.mae_after_signal_pct = outcome["mae_after_pct"]

    if signal.proposed_stop is not None:
        if signal.side == "LONG":
            stop_hit = outcome["last_price"] <= signal.proposed_stop
        else:
            stop_hit = outcome["last_price"] >= signal.proposed_stop
        result.stop_was_hit = stop_hit

    if signal.proposed_stop is None:
        result.category = "NO_PROPOSED_STOP"
        return result

    side_sign = 1.0 if signal.side == "LONG" else -1.0
    upside = (outcome["mfe_after_pct"] - signal.mfe_pct)
    downside = (signal.pnl_pct - outcome["actual_pnl_pct"])

    saved_pct = signal.pnl_pct - outcome["actual_pnl_pct"]
    result.saved_pct = round(saved_pct, 4)

    if result.stop_was_hit and downside >= 0.3:
        result.category = "SUCCESS_PROTECTION"
    elif upside > 1.0 and downside < 0.2:
        result.category = "MISSED_UPSIDE"
    elif saved_pct < -0.3:
        result.category = "FALSE_SIGNAL"
    else:
        result.category = "NEUTRAL"

    return result


def aggregate(results: List[OutcomeResult]) -> Dict[str, Any]:
    """Считает сводные метрики по списку результатов."""
    by_category: Dict[str, int] = defaultdict(int)
    total_saved_pct = 0.0
    n = 0
    for r in results:
        by_category[r.category] += 1
        if r.saved_pct is not None:
            total_saved_pct += r.saved_pct
            n += 1

    return {
        "total_signals": len(results),
        "by_category": dict(by_category),
        "avg_saved_pct": round(total_saved_pct / n, 4) if n else 0.0,
    }


def print_text_report(signals: List[GuardSignal], results: List[OutcomeResult], summary: Dict[str, Any]) -> None:
    print("=" * 64)
    print("  POSITION GUARD — OUTCOME TRACKER v1")
    print("=" * 64)
    print(f"  MOVE_SL signals found:    {len(signals)}")
    print(f"  Outcomes computed:        {len(results)}")
    print()
    print("  Category breakdown:")
    for cat, cnt in sorted(summary["by_category"].items(), key=lambda x: -x[1]):
        pct = (cnt / len(results) * 100) if results else 0
        print(f"    {cat:20s} {cnt:4d}  ({pct:5.1f}%)")
    print()
    print(f"  Avg saved_pct (across completed): {summary['avg_saved_pct']:+.3f}%")
    print()
    if results:
        print("  Per-signal details:")
        for r in results:
            saved = f"{r.saved_pct:+.3f}%" if r.saved_pct is not None else "n/a"
            stop_hit = (
                "STOP_HIT" if r.stop_was_hit
                else "no_stop" if r.stop_was_hit is None
                else "stop_not_hit"
            )
            actual = f"{r.actual_pnl_pct:+.2f}%" if r.actual_pnl_pct is not None else "n/a"
            mfe = f"{r.mfe_after_signal_pct:+.2f}%" if r.mfe_after_signal_pct is not None else "n/a"
            mae = f"{r.mae_after_signal_pct:+.2f}%" if r.mae_after_signal_pct is not None else "n/a"
            print(
                f"    {r.evaluation_id[:8]} {r.symbol:10s} {r.side:5s} | "
                f"signal_pnl={r.signal_pnl_pct:+.2f}% actual={actual} "
                f"mfe_after={mfe} mae_after={mae} "
                f"saved={saved} cat={r.category} ({stop_hit})"
            )
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="Position Guard Outcome Tracker")
    parser.add_argument("--since", default="72h", help="Window (e.g. 24h, 6h, 30m)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    try:
        cutoff = parse_since(args.since)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    signals = load_move_sl_signals(cutoff)

    if not signals:
        print(f"No MOVE_SL signals found since {cutoff.isoformat()}")
        return 0

    results: List[OutcomeResult] = []
    for sig in signals:
        outcome = load_position_outcome(
            sig.symbol, sig.side, sig.timestamp, position_id=sig.position_id
        )
        results.append(classify(sig, outcome))

    summary = aggregate(results)

    if args.json:
        out = {
            "summary": summary,
            "signals": [
                {
                    "evaluation_id": r.evaluation_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "signal_time": r.signal_time.isoformat(),
                    "signal_pnl_pct": r.signal_pnl_pct,
                    "proposed_stop": r.proposed_stop,
                    "actual_pnl_pct": r.actual_pnl_pct,
                    "mfe_after_pct": r.mfe_after_signal_pct,
                    "mae_after_pct": r.mae_after_signal_pct,
                    "stop_was_hit": r.stop_was_hit,
                    "category": r.category,
                    "saved_pct": r.saved_pct,
                }
                for r in results
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print_text_report(signals, results, summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
