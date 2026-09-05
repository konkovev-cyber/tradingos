"""
core/state_reconciliation/protection_state.py
Protection State v1 — cross-references positions + open orders for SL/TP.

Read-only. No execution. Detects missing protection on positions.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("tradingos.protection_state")


@dataclass
class ProtectionState:
    """Protection status for a single position."""
    symbol: str
    has_position: bool
    has_sl: bool
    has_tp: bool
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_order_id: str = ""
    tp_order_id: str = ""
    checked_at: str = ""

    @property
    def fully_protected(self) -> bool:
        return self.has_position and self.has_sl and self.has_tp

    @property
    def partially_protected(self) -> bool:
        return self.has_position and (self.has_sl or self.has_tp)

    @property
    def unprotected(self) -> bool:
        return self.has_position and not self.has_sl and not self.has_tp


async def check_protection_state() -> List[ProtectionState]:
    """
    Cross-reference positions with open orders to determine SL/TP status.
    Returns ProtectionState for each position.
    """
    from control_plane.bingx_read.client import BingXReadClient

    client = BingXReadClient()
    try:
        positions = await client.get_positions()
        orders = await client._get("/openApi/swap/v2/trade/openOrders") or []
        if isinstance(orders, dict):
            orders = orders.get("orders", [])
    finally:
        await client.close()

    # Build order map by symbol
    orders_by_symbol: Dict[str, List[dict]] = {}
    for o in orders:
        sym = o.get("symbol", "")
        if sym not in orders_by_symbol:
            orders_by_symbol[sym] = []
        orders_by_symbol[sym].append(o)

    results = []
    now = datetime.now(timezone.utc).isoformat()

    for pos in positions:
        sym = pos.get("symbol", "")
        pos_orders = orders_by_symbol.get(sym, [])

        sl_orders = [o for o in pos_orders if o.get("type") == "STOP_MARKET"]
        tp_orders = [o for o in pos_orders if o.get("type") == "TAKE_PROFIT_MARKET"]

        has_sl = len(sl_orders) > 0
        has_tp = len(tp_orders) > 0
        sl_price = float(sl_orders[0].get("stopPrice", 0)) if sl_orders else None
        tp_price = float(tp_orders[0].get("stopPrice", 0)) if tp_orders else None
        sl_id = str(sl_orders[0].get("orderId", "")) if sl_orders else ""
        tp_id = str(tp_orders[0].get("orderId", "")) if tp_orders else ""

        ps = ProtectionState(
            symbol=sym,
            has_position=True,
            has_sl=has_sl,
            has_tp=has_tp,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_order_id=sl_id,
            tp_order_id=tp_id,
            checked_at=now,
        )
        results.append(ps)

    return results


def render_protection(states: List[ProtectionState]) -> str:
    lines = [
        "=" * 64,
        "  PROTECTION STATE v1",
        "=" * 64,
        f"  Positions checked: {len(states)}",
        "",
    ]
    for ps in sorted(states, key=lambda x: x.symbol):
        if ps.unprotected:
            icon = "🔴"
            status = "UNPROTECTED"
        elif ps.fully_protected:
            icon = "🟢"
            status = "FULLY PROTECTED"
        else:
            icon = "🟡"
            status = "PARTIALLY PROTECTED"

        sl_str = f"SL={ps.sl_price:.4f}" if ps.has_sl else "NO SL"
        tp_str = f"TP={ps.tp_price:.4f}" if ps.has_tp else "NO TP"
        lines.append(f"  {icon} {ps.symbol:12s} {status:20s} {sl_str} {tp_str}")

    lines += [
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
