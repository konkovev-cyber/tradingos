"""
control_plane/reality/simulator.py
Reality Simulator — applies cost model to Paper results.

Read-only. No LIVE API. No exchange connection.
Pure mathematical simulation of real-world trading costs.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .models import CostBreakdown, RealityResult
from .cost_model import BingXCostModel

UBOT_DB = Path("/opt/ubot_bingx/bot_state.db")
REPORT_PATH = Path("/root/tradingos/control_plane/reality/reality_result.json")


def load_position(symbol: str) -> Optional[Dict]:
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


def simulate_reality(
    symbol: str,
    action: str = "TAKE_PARTIAL",
    hold_pnl: float = 0.0,
    action_pnl: float = 0.0,
) -> RealityResult:
    position = load_position(symbol)
    if not position:
        return RealityResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            action=action,
            gross_result=0,
            position_value=0,
            costs=CostBreakdown(),
            net_result=0,
            hold_result=hold_pnl,
            vs_hold=0,
            recommendation="NO_POSITION",
            notes="No open position found",
        )

    position_value = position["entry_price"] * position["qty"]
    cost_model = BingXCostModel()
    costs_dict = cost_model.calculate_costs(position_value, action)
    hold_costs = cost_model.calculate_costs(position_value, "HOLD")

    costs = CostBreakdown(
        commission=costs_dict["commission"],
        spread=costs_dict["spread"],
        slippage=costs_dict["slippage"],
        funding=costs_dict["funding"],
        latency_penalty=costs_dict["latency_penalty"],
        total=costs_dict["total"],
    )

    net_action = action_pnl - costs.total
    net_hold = hold_pnl - hold_costs["total"]
    vs_hold = net_action - net_hold

    if vs_hold > 0.10:
        rec = "ACTION_BETTER"
    elif vs_hold < -0.10:
        rec = "HOLD_BETTER"
    else:
        rec = "NEUTRAL"

    return RealityResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        symbol=symbol,
        action=action,
        gross_result=action_pnl,
        position_value=position_value,
        costs=costs,
        net_result=net_action,
        hold_result=net_hold,
        vs_hold=round(vs_hold, 4),
        recommendation=rec,
        notes=f"Position value: {position_value:.4f}, costs: {costs.total:.4f}",
    )


def save_result(result: RealityResult) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": result.timestamp,
        "symbol": result.symbol,
        "action": result.action,
        "gross_result": result.gross_result,
        "position_value": result.position_value,
        "costs": {
            "commission": result.costs.commission,
            "spread": result.costs.spread,
            "slippage": result.costs.slippage,
            "funding": result.costs.funding,
            "latency_penalty": result.costs.latency_penalty,
            "total": result.costs.total,
        },
        "net_result": result.net_result,
        "hold_result": result.hold_result,
        "vs_hold": result.vs_hold,
        "recommendation": result.recommendation,
        "notes": result.notes,
    }
    with REPORT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_PATH


def render_text(result: RealityResult) -> str:
    rec_icon = {"ACTION_BETTER": "🟢", "HOLD_BETTER": "🔴", "NEUTRAL": "⚪", "NO_POSITION": "⚫"}.get(result.recommendation, "?")
    lines = [
        "=" * 64,
        "  TRADINGOS — REALITY ENGINE v1",
        "  (Cost-adjusted simulation. No exchange API.)",
        "=" * 64,
        f"  Timestamp:  {result.timestamp}",
        f"  Symbol:     {result.symbol}",
        f"  Action:     {result.action}",
        f"  Position:   {result.position_value:.4f} USDT",
        "",
        "  ── Gross vs Net ──",
        f"  Gross action:  {result.gross_result:+.4f} USDT",
        f"  Costs:         {result.costs.total:.4f} USDT",
        f"  Net action:    {result.net_result:+.4f} USDT",
        f"  Net hold:      {result.hold_result:+.4f} USDT",
        f"  vs Hold:       {result.vs_hold:+.4f} USDT",
        "",
        "  ── Cost Breakdown ──",
        f"  Commission:   {result.costs.commission:.4f} USDT",
        f"  Spread:       {result.costs.spread:.4f} USDT",
        f"  Slippage:     {result.costs.slippage:.4f} USDT",
        f"  Funding:      {result.costs.funding:.4f} USDT",
        f"  Latency:      {result.costs.latency_penalty:.4f} USDT",
        "",
        f"  Recommendation: {rec_icon} {result.recommendation}",
        "",
        f"  {result.notes}",
        "",
        "  Note: Cost simulation only. No exchange API. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
