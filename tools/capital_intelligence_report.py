"""
tools/capital_intelligence_report.py
Capital Intelligence Layer v0.1 — financial overview with position-level detail.

Read-only. Aggregates from:
  - /opt/ubot_bingx/bot_state.db (real open positions + unrealized PnL)
  - /root/tradingos/tradingos_data.db (PIE recommendations, position history)

Usage:
    python3 tools/capital_intelligence_report.py
    python3 tools/capital_intelligence_report.py --json
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
PIE_DB = Path("/root/tradingos/tradingos_data.db")


def load_positions() -> list:
    if not UBOT_DB.exists():
        return []
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
            WHERE exit_price IS NULL
            ORDER BY timestamp DESC
            """
        )
        out = []
        for r in cur.fetchall():
            out.append({
                "symbol": r["symbol"],
                "side": r["side"],
                "entry_price": float(r["entry_price"] or 0.0),
                "qty": float(r["qty"] or 0.0),
                "unrealized_pnl": float(r["pnl"] or 0.0),
            })
        conn.close()
        return out
    except Exception:
        return []


def load_pie_rec_counts() -> dict:
    if not PIE_DB.exists():
        return {"move_sl_be": 0, "take_partial": 0, "correct": 0, "total": 0}
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM live_assist_log")
        total = cur.fetchone()[0]
        cur.execute("SELECT recommendation, COUNT(*) FROM live_assist_log GROUP BY recommendation")
        by_rec = dict(cur.fetchall())
        cur.execute("SELECT decision_quality, COUNT(*) FROM live_assist_log GROUP BY decision_quality")
        by_quality = dict(cur.fetchall())
        conn.close()
        return {
            "total": total,
            "move_sl_be": by_rec.get("MOVE_SL_BE", 0),
            "take_partial": by_rec.get("TAKE_PARTIAL", 0),
            "correct": by_quality.get("correct", 0),
        }
    except Exception:
        return {"move_sl_be": 0, "take_partial": 0, "correct": 0, "total": 0}


def concentration(positions: list) -> dict:
    if not positions:
        return {}
    total_abs = sum(abs(p["unrealized_pnl"]) for p in positions) or 1.0
    by_sym: Dict[str, float] = defaultdict(float)
    for p in positions:
        by_sym[p["symbol"]] += abs(p["unrealized_pnl"])
    return {
        sym: round(val / total_abs * 100, 1)
        for sym, val in sorted(by_sym.items(), key=lambda x: -x[1])
    }


def render(positions: list, pie: dict, conc: dict) -> str:
    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    in_profit = sum(1 for p in positions if p["unrealized_pnl"] > 0)
    in_loss = len(positions) - in_profit

    lines = [
        "=" * 64,
        "  TRADINGOS — CAPITAL INTELLIGENCE v0.1",
        "=" * 64,
        "",
        "  ── Portfolio Summary ──",
        f"  Open positions:     {len(positions)}",
        f"  Unrealized PnL:     {total_unrealized:+.4f} USDT",
        f"  In profit:          {in_profit}",
        f"  In loss:            {in_loss}",
        "",
        "  ── Position Detail ──",
    ]
    for p in sorted(positions, key=lambda x: -x["unrealized_pnl"]):
        emoji = "🟢" if p["unrealized_pnl"] > 0 else "🔴"
        lines.append(
            f"  {emoji} {p['symbol']:12s} {p['side']:5s} | "
            f"entry={p['entry_price']:.6f} qty={p['qty']:.2f} "
            f"PnL={p['unrealized_pnl']:+.4f} USDT"
        )
    lines += [
        "",
        "  ── Risk Concentration (by |PnL|) ──",
    ]
    if conc:
        for sym, pct in list(conc.items())[:10]:
            bar = "█" * int(pct / 5)
            lines.append(f"  {sym:12s} {pct:5.1f}% {bar}")
    else:
        lines.append("  No positions")
    lines += [
        "",
        "  ── PIE Recommendation History ──",
        f"  Total recs:         {pie['total']}",
        f"  MOVE_SL_BE:         {pie['move_sl_be']}",
        f"  TAKE_PARTIAL:       {pie['take_partial']}",
        f"  Marked correct:     {pie['correct']} ({pie['correct'] * 100 // max(pie['total'], 1)}%)",
        "",
        "  ── Capital Health ──",
    ]
    if total_unrealized > 0:
        lines.append(f"  Status:             🟢 PROFITABLE ({total_unrealized:+.4f})")
    elif total_unrealized > -5:
        lines.append(f"  Status:             🟡 NEUTRAL ({total_unrealized:+.4f})")
    else:
        lines.append(f"  Status:             🔴 IN DRAWDOWN ({total_unrealized:+.4f})")
    lines += [
        "",
        "  Note: Unrealized PnL from ubot_bingx trade_journal.",
        "        No orders executed. Read-only analysis.",
        "=" * 64,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capital Intelligence Report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    positions = load_positions()
    pie = load_pie_rec_counts()
    conc = concentration(positions)

    if args.json:
        print(json.dumps({
            "positions": positions,
            "total_unrealized": sum(p["unrealized_pnl"] for p in positions),
            "pie": pie,
            "concentration": conc,
        }, indent=2))
    else:
        print(render(positions, pie, conc))

    return 0


if __name__ == "__main__":
    sys.exit(main())
