"""
control_plane/bingx_read/adapter.py
BingX Real Account Adapter — feeds real positions into Unified State.

ЕДИНСТВЕННЫЙ источник истины по BingX.
READ-ONLY. No order execution. No LIVE changes.
Источник ключей: /opt/ubot_bingx/.env
Проверка: userId = 1586188652750692355 (BingX)
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .client import BingXReadClient, KNOWN_USER_ID

REALITY_SNAPSHOT = Path("/root/tradingos/control_plane/bingx_read/real_account_state.json")


async def fetch_real_account_state() -> Dict:
    """Fetch real BingX account state and verify identity."""
    client = BingXReadClient()
    try:
        positions = await client.get_positions()
        balance = await client.get_balance()
        client.verify(balance)

        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "BINGX_API_HMAC",
            "userId": KNOWN_USER_ID,
            "account": {
                "equity": balance.get("equity", 0) if balance else 0,
                "wallet": balance.get("wallet", 0) if balance else 0,
                "available": balance.get("available", 0) if balance else 0,
                "unrealized_pnl": balance.get("unrealized_pnl", 0) if balance else 0,
                "realized_pnl": balance.get("realized_pnl", 0) if balance else 0,
            },
            "positions": positions,
            "position_count": len(positions),
            "total_unrealized": round(sum(p["unrealized_pnl"] for p in positions), 4),
        }

        # Check SL/TP on each position
        no_tp = [p for p in positions if p.get("take_profit") is None]
        no_sl = [p for p in positions if p.get("stop_loss") is None]
        if no_tp:
            state["warnings"] = {"no_tp": [p["symbol"] for p in no_tp]}
        if no_sl:
            state["warnings"] = state.get("warnings", {})
            state["warnings"]["no_sl"] = [p["symbol"] for p in no_sl]

        return state
    finally:
        await client.close()


def save_state(state: Dict) -> Path:
    REALITY_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    with REALITY_SNAPSHOT.open("w") as f:
        json.dump(state, f, indent=2)
    return REALITY_SNAPSHOT


def render_text(state: Dict) -> str:
    positions = state.get("positions", [])
    account = state.get("account", {})
    total = state.get("total_unrealized", 0)
    warnings = state.get("warnings", {})

    lines = [
        "=" * 60,
        f"  BINGX REAL ACCOUNT (userId: {KNOWN_USER_ID})",
        "=" * 60,
        f"  Timestamp:    {state.get('timestamp', '')}",
        f"  Source:       {state.get('source', '')}",
        "",
        "  ── Account ──",
        f"  Equity:       {account.get('equity', 0):.2f} USDT",
        f"  Available:    {account.get('available', 0):.2f} USDT",
        f"  Unrealized:   {account.get('unrealized_pnl', 0):+.4f}",
        f"  Realized:     {account.get('realized_pnl', 0):+.4f}",
        "",
        "  ── Positions ──",
    ]
    for p in sorted(positions, key=lambda x: -x["unrealized_pnl"]):
        emoji = "🟢" if p["unrealized_pnl"] > 0 else "🔴"
        ratio = float(p.get("pnl_ratio", 0) or 0) * 100
        tp = f"TP={p['take_profit']}" if p.get("take_profit") else "⚠️ NO TP"
        sl = f"SL={p['stop_loss']}" if p.get("stop_loss") else "⚠️ NO SL"
        lines.append(
            f"    {emoji} {p['symbol']:12s} {p['side']:5s} "
            f"entry={p['entry_price']:.6f} pnl={p['unrealized_pnl']:+.4f} ({ratio:+.2f}%) | {tp} {sl}"
        )

    no_tp = warnings.get("no_tp", [])
    no_sl = warnings.get("no_sl", [])
    if no_tp:
        lines.append(f"\n  ⚠️  Без TP: {', '.join(no_tp)}")
    if no_sl:
        lines.append(f"  ⚠️  Без SL: {', '.join(no_sl)}")

    lines += [
        "",
        "  Mode: SHADOW_REAL_ACCOUNT",
        "  Execution: BLOCKED",
        "  Note: Read-only. No orders sent.",
        "=" * 60,
    ]
    return "\n".join(lines)
