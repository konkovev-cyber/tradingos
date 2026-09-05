"""
control_plane/adapters/capital_adapter.py
Read-only adapter for Capital Intelligence.
Collects data from ubot_bingx bot_state.db.
"""
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Dict

from ..models import CapitalState

UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")


def collect() -> CapitalState:
    if not UBOT_DB.exists():
        return CapitalState(status="NO_DATA")

    try:
        uri = f"file:{UBOT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0.0) FROM trade_journal WHERE exit_price IS NULL"
        )
        n_open, unrealized = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0.0) FROM trade_journal WHERE exit_price IS NOT NULL"
        )
        n_closed, realized = cur.fetchone()

        cur.execute(
            "SELECT symbol, pnl FROM trade_journal WHERE exit_price IS NULL"
        )
        by_sym: Dict[str, float] = defaultdict(float)
        for sym, pnl in cur.fetchall():
            by_sym[sym] += abs(float(pnl or 0))

        conn.close()

        total_abs = sum(by_sym.values()) or 1.0
        top_sym = max(by_sym, key=by_sym.get) if by_sym else ""
        top_pct = round(by_sym.get(top_sym, 0) / total_abs * 100, 1) if by_sym else 0

        total_unrealized = float(unrealized)
        if total_unrealized > 0:
            status = "PROFITABLE"
        elif total_unrealized > -5:
            status = "NEUTRAL"
        else:
            status = "DRAWDOWN"

        return CapitalState(
            total_unrealized=round(total_unrealized, 4),
            total_realized=round(float(realized), 4),
            closed_trades=int(n_closed or 0),
            open_positions=int(n_open or 0),
            biggest_risk_symbol=top_sym,
            biggest_risk_pct=top_pct,
            status=status,
        )
    except Exception:
        return CapitalState(status="ERROR")
