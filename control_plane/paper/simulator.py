"""
control_plane/paper/simulator.py
Paper Simulator — runs ACTION vs HOLD scenarios.

Read-only. No exchange API. No LIVE changes.
Pure mathematical simulation.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import PaperScenario, PaperSimulation

UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
PIE_DB = Path("/root/tradingos/tradingos_data.db")
APPROVAL_PATH = Path("/root/tradingos/control_plane/approval/approval.json")
REPORT_PATH = Path("/root/tradingos/control_plane/paper/paper_simulation.json")


def load_position(symbol: str) -> Optional[Dict]:
    """Load current position from ubot bot_state.db."""
    if not UBOT_DB.exists():
        return None
    try:
        uri = f"file:{UBOT_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, side, entry_price, qty, pnl "
            "FROM trade_journal WHERE symbol = ? AND exit_price IS NULL "
            "ORDER BY timestamp DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return {
                "symbol": row[0],
                "side": row[1],
                "entry_price": float(row[2] or 0),
                "qty": float(row[3] or 0),
                "unrealized_pnl": float(row[4] or 0),
            }
        return None
    except Exception:
        return None


def load_mfe_for_symbol(symbol: str, side: str) -> float:
    """Load max profit seen (MFE) from PIE DB."""
    if not PIE_DB.exists():
        return 0.0
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT MAX(max_profit_seen) FROM position_events "
            "WHERE symbol = ? AND side = ?",
            (symbol, side),
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0] or 0) * 100 if row[0] else 0.0
    except Exception:
        return 0.0


def simulate_hold(position: Dict) -> float:
    """Baseline: just keep the current unrealized PnL."""
    return position.get("unrealized_pnl", 0)


def simulate_move_sl_be(position: Dict, protect_pct: float = 0.5) -> float:
    """Simulate moving SL to breakeven + protect_pct% profit."""
    entry = position.get("entry_price", 0)
    qty = position.get("qty", 0)
    side = position.get("side", "")
    if side in ("BUY", "LONG"):
        new_sl = entry * (1 + protect_pct / 100)
        # If position is profitable, SL guarantees some profit
        # Worst case: SL hit at breakeven+protect_pct
        guaranteed = (new_sl - entry) * qty
        return max(guaranteed, position.get("unrealized_pnl", 0))
    else:  # SHORT
        new_sl = entry * (1 - protect_pct / 100)
        guaranteed = (entry - new_sl) * qty
        return max(guaranteed, position.get("unrealized_pnl", 0))


def simulate_take_partial(position: Dict, partial_pct: float = 25.0) -> float:
    """Simulate closing partial_pct% at current price."""
    pnl = position.get("unrealized_pnl", 0)
    qty = position.get("qty", 0)
    if qty <= 0:
        return pnl
    # Lock in partial_pct of current profit
    locked = pnl * (partial_pct / 100)
    # Remaining position continues with assumed momentum
    remaining_pnl = pnl * (1 - partial_pct / 100)
    return locked + remaining_pnl * 0.8  # 0.8 factor = expected reduction


def classify_delta(delta: float) -> str:
    if delta > 0.1:
        return "ACTION_BETTER"
    elif delta < -0.1:
        return "HOLD_BETTER"
    else:
        return "NEUTRAL"


def run_simulation(symbol: str) -> PaperSimulation:
    position = load_position(symbol)
    if not position:
        return PaperSimulation(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            base_position_value=0,
            scenarios=[],
            best_action="HOLD",
            best_delta=0,
        )

    hold = simulate_hold(position)
    be = simulate_move_sl_be(position)
    partial = simulate_take_partial(position)

    scenarios = [
        PaperScenario(
            scenario_id="S1",
            action="HOLD",
            symbol=symbol,
            hold_pnl=hold,
            action_pnl=hold,
            delta=0,
            verdict="BASELINE",
            notes="No action, keep current position",
        ),
        PaperScenario(
            scenario_id="S2",
            action="MOVE_SL_BE",
            symbol=symbol,
            hold_pnl=hold,
            action_pnl=be,
            delta=round(be - hold, 4),
            verdict=classify_delta(be - hold),
            notes=f"Move SL to entry+0.5%, worst case breakeven",
        ),
        PaperScenario(
            scenario_id="S3",
            action="TAKE_PARTIAL",
            symbol=symbol,
            hold_pnl=hold,
            action_pnl=partial,
            delta=round(partial - hold, 4),
            verdict=classify_delta(partial - hold),
            notes="Close 25% at current price",
        ),
    ]

    best = max(scenarios, key=lambda s: s.action_pnl)
    return PaperSimulation(
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        base_position_value=position.get("qty", 0) * position.get("entry_price", 0),
        scenarios=scenarios,
        best_action=best.action,
        best_delta=best.delta,
    )


def save_simulation(sim: PaperSimulation) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": sim.timestamp,
        "symbol": sim.symbol,
        "base_position_value": sim.base_position_value,
        "best_action": sim.best_action,
        "best_delta": sim.best_delta,
        "scenarios": [
            {
                "scenario_id": s.scenario_id,
                "action": s.action,
                "hold_pnl": s.hold_pnl,
                "action_pnl": s.action_pnl,
                "delta": s.delta,
                "verdict": s.verdict,
                "notes": s.notes,
            }
            for s in sim.scenarios
        ],
    }
    with REPORT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_PATH


def render_text(sim: PaperSimulation) -> str:
    lines = [
        "=" * 64,
        "  TRADINGOS — PAPER SIMULATION v1",
        "  (No exchange API. No LIVE changes. Pure math.)",
        "=" * 64,
        f"  Timestamp: {sim.timestamp}",
        f"  Symbol:    {sim.symbol}",
        f"  Base value: {sim.base_position_value:.4f} USDT",
        "",
        "  ── Scenarios ──",
    ]
    for s in sim.scenarios:
        v_icon = {"ACTION_BETTER": "🟢", "HOLD_BETTER": "🔴", "NEUTRAL": "⚪", "BASELINE": "⚫"}.get(s.verdict, "?")
        lines.append(f"    {v_icon} [{s.scenario_id}] {s.action:15s} pnl={s.action_pnl:+.4f} delta={s.delta:+.4f}")
        lines.append(f"        {s.notes}")
    lines += [
        "",
        f"  Best action: {sim.best_action} (delta {sim.best_delta:+.4f})",
        "",
        "  Note: Virtual simulation. No exchange API. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
