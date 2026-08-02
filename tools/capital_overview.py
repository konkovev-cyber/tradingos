"""
tools/capital_overview.py
TradingOS Capital / PnL Overview v1.

Pure read-only. Does NOT touch any live system, does NOT trade.
Aggregates real closed-trade data from:
  - /opt/ubot_bingx/bot_state.db:trade_journal  (ubot-bingx closed trades)
  - /root/tradingos/tradingos_data.db:live_assist_log  (PIE recommendations)
  - /root/tradingos/logs/position_guard_shadow.jsonl  (Guard signals)

Usage:
    python3 tools/capital_overview.py
    python3 tools/capital_overview.py --json
    python3 tools/capital_overview.py --since 7d
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
PIE_DB = Path("/root/tradingos/tradingos_data.db")
GUARD_LOG = Path("/root/tradingos/logs/position_guard_shadow.jsonl")


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


def load_ubot_trades(since: datetime) -> dict:
    """Читает ubot_bingx/bot_state.db:trade_journal.
    Возвращает {'closed': [...], 'open_with_unrealized': [...], 'all_n': ...}
    closed = exit_price IS NOT NULL (реально закрытые).
    open_with_unrealized = status='OPEN' (текущая позиция с плавающим PnL)."""
    if not UBOT_DB.exists():
        return {"closed": [], "open_with_unrealized": [], "all_n": 0}
    closed: list = []
    open_unr: list = []
    try:
        uri = f"file:{UBOT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, timestamp, symbol, side, entry_price, qty,
                   exit_price, pnl, reason, status
            FROM trade_journal
            ORDER BY timestamp DESC
            """
        )
        for r in cur.fetchall():
            ts = datetime.fromtimestamp(float(r["timestamp"]), tz=timezone.utc)
            if ts < since:
                continue
            base = {
                "id": r["id"],
                "timestamp_utc": ts.isoformat(),
                "symbol": r["symbol"],
                "side": r["side"],
                "entry_price": float(r["entry_price"] or 0.0),
                "qty": float(r["qty"] or 0.0),
                "pnl_usdt": float(r["pnl"] or 0.0),
            }
            if r["exit_price"] is not None:
                base["exit_price"] = float(r["exit_price"])
                base["reason"] = r["reason"] or ""
                closed.append(base)
            else:
                base["status"] = r["status"] or ""
                base["unrealized_pnl"] = float(r["pnl"] or 0.0)
                open_unr.append(base)
        conn.close()
    except Exception as e:
        print(f"  WARN: ubot DB read failed: {e}", file=sys.stderr)
    return {"closed": closed, "open_with_unrealized": open_unr, "all_n": len(closed) + len(open_unr)}


def load_pie_recommendations(since: datetime) -> list:
    """Читает PIE live_assist_log (recommendations + their quality assessment)."""
    if not PIE_DB.exists():
        return []
    out = []
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, position_id, symbol, side, recommendation,
                   price_at_recommendation, pnl_at_recommendation,
                   mfe_at_recommendation, health_at_recommendation,
                   decision_quality, quality_notes, timestamp_utc
            FROM live_assist_log
            WHERE timestamp_utc >= ?
            ORDER BY timestamp_utc DESC
            """,
            (since.isoformat(),),
        )
        for r in cur.fetchall():
            out.append({
                "id": r["id"],
                "timestamp_utc": r["timestamp_utc"],
                "symbol": r["symbol"],
                "side": r["side"],
                "recommendation": r["recommendation"],
                "pnl_at": float(r["pnl_at_recommendation"] or 0.0),
                "decision_quality": r["decision_quality"] or "",
                "quality_notes": r["quality_notes"] or "",
            })
        conn.close()
    except Exception as e:
        print(f"  WARN: PIE DB read failed: {e}", file=sys.stderr)
    return out


def load_guard_signals(since: datetime) -> dict:
    """Суммаризация Guard сигналов из shadow JSONL."""
    if not GUARD_LOG.exists():
        return {"total": 0, "by_action": {}}
    by_action: dict = defaultdict(int)
    total = 0
    try:
        with GUARD_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["timestamp"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < since:
                        continue
                    total += 1
                    action = rec.get("decision", {}).get("action", "?")
                    by_action[action] += 1
                except Exception:
                    continue
    except Exception:
        pass
    return {"total": total, "by_action": dict(by_action)}


def aggregate_ubot_closed(trades: list) -> dict:
    if not trades:
        return {
            "n_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0,
            "winrate": 0.0, "avg_pnl": 0.0, "best": 0.0, "worst": 0.0,
        }
    pnls = [t["pnl_usdt"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "n_trades": len(trades),
        "total_pnl": round(sum(pnls), 4),
        "wins": wins,
        "losses": losses,
        "winrate": round(wins / len(pnls) * 100, 1) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "best": round(max(pnls), 4) if pnls else 0.0,
        "worst": round(min(pnls), 4) if pnls else 0.0,
    }


def aggregate_ubot_open(open_pos: list) -> dict:
    if not open_pos:
        return {"n": 0, "total_unrealized": 0.0, "winners": 0, "losers": 0}
    pnls = [p["unrealized_pnl"] for p in open_pos]
    return {
        "n": len(open_pos),
        "total_unrealized": round(sum(pnls), 4),
        "winners": sum(1 for p in pnls if p > 0),
        "losers": sum(1 for p in pnls if p < 0),
    }


def aggregate_pie(recs: list) -> dict:
    if not recs:
        return {"n": 0, "by_recommendation": {}, "quality": {}}
    by_rec: dict = defaultdict(int)
    by_quality: dict = defaultdict(int)
    for r in recs:
        by_rec[r["recommendation"]] += 1
        by_quality[r["decision_quality"] or "unknown"] += 1
    return {
        "n": len(recs),
        "by_recommendation": dict(by_rec),
        "quality": dict(by_quality),
    }


def render_text(ubot_closed, ubot_open, pie, guard, since_str: str) -> str:
    lines = [
        "=" * 60,
        "  TRADINGOS — CAPITAL / PnL OVERVIEW",
        "=" * 60,
        f"  Period: {since_str}",
        "",
        "  ── ubot-bingx — CLOSED trades (realized) ──",
        f"    Trades:        {ubot_closed['n_trades']}",
        f"    Total PnL:     {ubot_closed['total_pnl']:+.4f} USDT",
        f"    Wins / Losses: {ubot_closed['wins']} / {ubot_closed['losses']}",
        f"    Winrate:       {ubot_closed['winrate']:.1f}%",
        f"    Avg PnL:       {ubot_closed['avg_pnl']:+.4f} USDT",
        f"    Best / Worst:  {ubot_closed['best']:+.4f} / {ubot_closed['worst']:+.4f} USDT",
        "",
        "  ── ubot-bingx — OPEN positions (unrealized) ──",
        f"    Positions:     {ubot_open['n']}",
        f"    Unrealized:    {ubot_open['total_unrealized']:+.4f} USDT",
        f"    In profit:     {ubot_open['winners']}",
        f"    In loss:       {ubot_open['losers']}",
        "",
        "  ── PIE live_assist (recommendations) ──",
        f"    Total: {pie['n']}",
    ]
    if pie["by_recommendation"]:
        lines.append("    By recommendation:")
        for k, v in sorted(pie["by_recommendation"].items(), key=lambda x: -x[1]):
            lines.append(f"      {k:15s} {v}")
    if pie["quality"]:
        lines.append("    Quality assessment:")
        for k, v in sorted(pie["quality"].items(), key=lambda x: -x[1]):
            lines.append(f"      {k:15s} {v}")
    lines += [
        "",
        "  ── Position Guard (SHADOW signals) ──",
        f"    Total evaluations: {guard['total']}",
    ]
    if guard["by_action"]:
        for k, v in sorted(guard["by_action"].items(), key=lambda x: -x[1]):
            lines.append(f"      {k:10s} {v}")
    lines += [
        "",
        "  Note: Position Guard runs in SHADOW ONLY —",
        "        no real PnL impact yet (awaiting valid MOVE_SL evidence).",
        "=" * 60,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="TradingOS Capital / PnL Overview")
    parser.add_argument("--since", default="7d", help="Window (e.g. 24h, 7d, 30d)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        cutoff = parse_since(args.since)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    ubot_data = load_ubot_trades(cutoff)
    pie_recs = load_pie_recommendations(cutoff)
    guard = load_guard_signals(cutoff)

    ubot_closed = aggregate_ubot_closed(ubot_data["closed"])
    ubot_open = aggregate_ubot_open(ubot_data["open_with_unrealized"])
    pie = aggregate_pie(pie_recs)

    if args.json:
        print(json.dumps({
            "period": args.since,
            "cutoff_utc": cutoff.isoformat(),
            "ubot_bingx": {
                "closed_aggregate": ubot_closed,
                "open_aggregate": ubot_open,
                "closed_trades": ubot_data["closed"],
                "open_positions": ubot_data["open_with_unrealized"],
            },
            "pie_recommendations": {"aggregate": pie, "recent": pie_recs[:20]},
            "position_guard": guard,
        }, indent=2, default=str))
    else:
        print(render_text(ubot_closed, ubot_open, pie, guard, args.since))

    return 0


if __name__ == "__main__":
    sys.exit(main())
