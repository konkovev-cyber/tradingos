"""
tools/action_shadow_outcome.py
Action Outcome Tracker v1 — measures whether APPROVED Shadow Actions
would have actually been profitable if executed.

READ-ONLY. Reads:
  - /root/tradingos/logs/position_action_shadow.jsonl
  - /root/tradingos/tradingos_data.db (PIE position_events)

For each APPROVED action:
  1. De-duplicates (PIE repeats recommendations every ~60s)
  2. Pulls PIE events after the action timestamp
  3. Compares:
     - Actual: PnL over the next window
     - Virtual MOVE_SL_BE: PnL locked in (approximated as breakeven)
     - Virtual TAKE_PARTIAL: 25% closed at signal price + remaining moves
  4. Classifies into:
     - PROFIT_PROTECTED: action would have saved money
     - NEUTRAL: no real difference
     - MISSED_UPSIDE: action would have capped gains
     - EARLY_EXIT: position went negative, action would have avoided loss
     - PENDING: not enough post-action data yet
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
from typing import Dict, List, Optional

LOG_PATH = Path("/root/tradingos/logs/position_action_shadow.jsonl")
PIE_DB = Path("/root/tradingos/tradingos_data.db")

DEDUPE_WINDOW_MINUTES = 30

logger = logging.getLogger("action_shadow_outcome")


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
class ApprovedAction:
    evaluation_id: str
    rec_id: int
    symbol: str
    side: str
    action_type: str
    timestamp: datetime
    price_at_signal: float
    pnl_at_signal_pct: float
    mfe_at_signal_pct: float
    health_at_signal: float
    proposed_stop: Optional[float]
    proposed_qty_pct: Optional[float]


@dataclass
class OutcomeRow:
    evaluation_id: str
    symbol: str
    side: str
    action_type: str
    timestamp: datetime
    pnl_at_signal_pct: float

    actual_pnl_pct: Optional[float] = None
    actual_close_price: Optional[float] = None
    post_action_max_price: Optional[float] = None
    post_action_min_price: Optional[float] = None
    mfe_after_action_pct: Optional[float] = None
    mae_after_action_pct: Optional[float] = None

    virtual_pnl_pct: Optional[float] = None
    virtual_scenario: str = ""
    delta_pct: Optional[float] = None
    category: str = "PENDING"
    note: str = ""

    age_minutes: float = 0.0
    maturity: str = "FRESH"   # FRESH / YOUNG / MATURE / STALE
    waiting_for: List[str] = None

    def __post_init__(self):
        if self.waiting_for is None:
            self.waiting_for = []


def load_approved(since: datetime) -> List[ApprovedAction]:
    if not LOG_PATH.exists():
        return []
    out: List[ApprovedAction] = []
    with LOG_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("action", {}).get("verdict") != "APPROVE":
                    continue
                ts = datetime.fromisoformat(rec["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < since:
                    continue
                pie = rec.get("pie_recommendation", {})
                a = rec.get("action", {})
                out.append(ApprovedAction(
                    evaluation_id=rec.get("evaluation_id", ""),
                    rec_id=int(rec.get("rec_id", 0)),
                    symbol=rec["symbol"],
                    side=rec["side"],
                    action_type=a.get("type", "?"),
                    timestamp=ts,
                    price_at_signal=float(pie.get("price", 0.0)),
                    pnl_at_signal_pct=float(pie.get("pnl_pct", 0.0)),
                    mfe_at_signal_pct=float(pie.get("mfe_pct", 0.0)),
                    health_at_signal=float(pie.get("health", 0.0)),
                    proposed_stop=a.get("proposed_new_stop"),
                    proposed_qty_pct=a.get("proposed_quantity_pct"),
                ))
            except Exception as e:
                logger.debug(f"skip malformed: {e}")
                continue
    return out


def dedupe(actions: List[ApprovedAction]) -> List[ApprovedAction]:
    """
    Collapse PIE recommendation duplicates within DEDUPE_WINDOW_MINUTES
    for the same (symbol, side, action_type). Keep the first one.
    """
    by_key: Dict[tuple, ApprovedAction] = {}
    for a in actions:
        key = (a.symbol, a.side, a.action_type)
        if key in by_key:
            prev = by_key[key]
            if (a.timestamp - prev.timestamp).total_seconds() < DEDUPE_WINDOW_MINUTES * 60:
                continue
        by_key[key] = a
    return sorted(by_key.values(), key=lambda x: x.timestamp)


def load_post_action_outcome(symbol: str, after_ts: datetime) -> Optional[Dict]:
    """
    Reads PIE position_events after `after_ts` for the given symbol.
    Returns the max price, min price, and last known pnl_pct.
    """
    if not PIE_DB.exists():
        return None
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT current_price, pnl_pct, max_profit_seen, max_loss_seen, timestamp_utc
            FROM position_events
            WHERE symbol = ? AND timestamp_utc > ?
            ORDER BY id ASC
            """,
            (symbol, after_ts.isoformat()),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"PIE read for {symbol} after {after_ts} failed: {e}")
        return None
    if not rows:
        return None
    prices = [float(r["current_price"] or 0.0) for r in rows if r["current_price"]]
    pnls = [float(r["pnl_pct"] or 0.0) * 100.0 for r in rows]
    if not prices:
        return None
    return {
        "max_price": max(prices),
        "min_price": min(prices),
        "last_price": prices[-1],
        "last_pnl_pct": pnls[-1],
        "n_events": len(rows),
    }


def classify(action: ApprovedAction, post: Optional[Dict]) -> OutcomeRow:
    row = OutcomeRow(
        evaluation_id=action.evaluation_id,
        symbol=action.symbol,
        side=action.side,
        action_type=action.action_type,
        timestamp=action.timestamp,
        pnl_at_signal_pct=action.pnl_at_signal_pct,
    )
    age_min = (datetime.now(timezone.utc) - action.timestamp).total_seconds() / 60.0
    row.age_minutes = round(age_min, 1)

    if age_min < 60:
        row.maturity = "FRESH"
    elif age_min < 240:
        row.maturity = "YOUNG"
    elif age_min < 1440:
        row.maturity = "MATURE"
    else:
        row.maturity = "STALE"

    if post is None:
        row.category = "PENDING"
        row.note = "No post-action PIE data"
        if row.maturity in ("MATURE", "STALE"):
            row.waiting_for = ["position_close_or_next_pie_update"]
        else:
            row.waiting_for = ["next_pie_update", "position_close"]
        return row

    row.actual_close_price = post["last_price"]
    row.actual_pnl_pct = post["last_pnl_pct"]
    row.post_action_max_price = post["max_price"]
    row.post_action_min_price = post["min_price"]
    row.mfe_after_action_pct = round(
        (post["max_price"] - action.price_at_signal) / action.price_at_signal * 100.0
        if action.side == "BUY" else
        (action.price_at_signal - post["min_price"]) / action.price_at_signal * 100.0,
        4,
    )
    row.mae_after_action_pct = round(
        (post["min_price"] - action.price_at_signal) / action.price_at_signal * 100.0
        if action.side == "BUY" else
        (action.price_at_signal - post["max_price"]) / action.price_at_signal * 100.0,
        4,
    )

    if action.action_type == "MOVE_SL_BE":
        row.virtual_scenario = "SL moved to breakeven+0.05%; position closed there if hit"
        if action.proposed_stop is not None and action.side == "SELL":
            if post["max_price"] >= action.proposed_stop:
                row.virtual_pnl_pct = round(
                    (action.proposed_stop - action.price_at_signal) / action.price_at_signal * 100.0,
                    4,
                )
                row.category = "PROFIT_PROTECTED" if row.virtual_pnl_pct >= 0 else "EARLY_EXIT"
            else:
                row.virtual_pnl_pct = row.actual_pnl_pct
                row.category = "NEUTRAL"
        elif action.proposed_stop is not None and action.side == "BUY":
            if post["min_price"] <= action.proposed_stop:
                row.virtual_pnl_pct = round(
                    (action.proposed_stop - action.price_at_signal) / action.price_at_signal * 100.0,
                    4,
                )
                row.category = "PROFIT_PROTECTED" if row.virtual_pnl_pct >= 0 else "EARLY_EXIT"
            else:
                row.virtual_pnl_pct = row.actual_pnl_pct
                row.category = "NEUTRAL"
    elif action.action_type == "TAKE_PARTIAL":
        partial = (action.proposed_qty_pct or 25.0) / 100.0
        if action.side == "BUY":
            partial_pnl = (post["last_price"] - action.price_at_signal) / action.price_at_signal * 100.0
        else:
            partial_pnl = (action.price_at_signal - post["last_price"]) / action.price_at_signal * 100.0
        row.virtual_pnl_pct = round(partial * partial_pnl + (1 - partial) * row.actual_pnl_pct, 4)
        row.virtual_scenario = f"Closed {action.proposed_qty_pct:.0f}% at signal price + remaining at last"
        if row.actual_pnl_pct >= 0.3 and row.mfe_after_action_pct > 1.0:
            row.category = "MISSED_UPSIDE"
        elif row.virtual_pnl_pct > row.actual_pnl_pct + 0.2:
            row.category = "PROFIT_PROTECTED"
        else:
            row.category = "NEUTRAL"
    else:
        row.category = "UNKNOWN_ACTION"

    if row.virtual_pnl_pct is not None and row.actual_pnl_pct is not None:
        row.delta_pct = round(row.virtual_pnl_pct - row.actual_pnl_pct, 4)

    return row


def aggregate(rows: List[OutcomeRow]) -> Dict:
    by_category: Dict[str, int] = defaultdict(int)
    total_delta = 0.0
    n_with_delta = 0
    for r in rows:
        by_category[r.category] += 1
        if r.delta_pct is not None:
            total_delta += r.delta_pct
            n_with_delta += 1
    return {
        "total": len(rows),
        "by_category": dict(by_category),
        "avg_delta_pct": round(total_delta / n_with_delta, 4) if n_with_delta else 0.0,
    }


def render(rows: List[OutcomeRow], summary: Dict, since_str: str) -> str:
    maturity_buckets: Dict[str, int] = defaultdict(int)
    for r in rows:
        maturity_buckets[r.maturity] += 1
    lines = [
        "=" * 64,
        "  ACTION SHADOW — OUTCOME TRACKER v1",
        "=" * 64,
        f"  Period: {since_str}",
        f"  Approved actions (after dedupe): {summary['total']}",
        "",
        "  Category breakdown:",
    ]
    for k, v in sorted(summary["by_category"].items(), key=lambda x: -x[1]):
        pct = (v / summary["total"] * 100) if summary["total"] else 0
        lines.append(f"    {k:20s} {v:3d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("  Maturity breakdown:")
    for k in ("FRESH", "YOUNG", "MATURE", "STALE"):
        if k in maturity_buckets:
            lines.append(f"    {k:8s} {maturity_buckets[k]}")
    lines.append("")
    lines.append(f"  Avg virtual-vs-actual delta: {summary['avg_delta_pct']:+.3f}%")
    lines.append("")
    if rows:
        lines.append("  Per-action details (most recent first):")
        for r in sorted(rows, key=lambda x: x.timestamp, reverse=True)[:20]:
            d = f"{r.delta_pct:+.3f}%" if r.delta_pct is not None else "n/a"
            lines.append(
                f"    {r.timestamp.isoformat()[:19]} | {r.symbol:10s} {r.side:5s} | "
                f"{r.action_type:15s} | signal_pnl={r.pnl_at_signal_pct:+.2f}% "
                f"actual={r.actual_pnl_pct if r.actual_pnl_pct is None else f'{r.actual_pnl_pct:+.2f}%'} "
                f"virtual={r.virtual_pnl_pct if r.virtual_pnl_pct is None else f'{r.virtual_pnl_pct:+.2f}%'} "
                f"delta={d} cat={r.category} maturity={r.maturity} age={r.age_minutes:.0f}m"
            )
    lines += [
        "",
        "  Note: virtual_pnl estimates assume PIE recommendation was executed",
        "        at signal time. No real orders sent. Read-only analysis.",
        "  Maturity: FRESH<1h, YOUNG<4h, MATURE<24h, STALE>=24h",
        "=" * 64,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Action Shadow Outcome Tracker")
    parser.add_argument("--since", default="24h")
    parser.add_argument("--json", action="store_true")
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

    raw = load_approved(cutoff)
    deduped = dedupe(raw)

    rows: List[OutcomeRow] = []
    for a in deduped:
        post = load_post_action_outcome(a.symbol, a.timestamp)
        rows.append(classify(a, post))

    summary = aggregate(rows)

    if args.json:
        out = {
            "summary": summary,
            "raw_approved": len(raw),
            "after_dedupe": len(deduped),
            "rows": [
                {
                    "evaluation_id": r.evaluation_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "action_type": r.action_type,
                    "timestamp": r.timestamp.isoformat(),
                    "pnl_at_signal_pct": r.pnl_at_signal_pct,
                    "actual_pnl_pct": r.actual_pnl_pct,
                    "virtual_pnl_pct": r.virtual_pnl_pct,
                    "delta_pct": r.delta_pct,
                    "category": r.category,
                    "note": r.note,
                }
                for r in rows
            ],
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        print(render(rows, summary, args.since))

    return 0


if __name__ == "__main__":
    sys.exit(main())
