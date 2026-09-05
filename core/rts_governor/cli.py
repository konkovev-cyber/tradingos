"""
core/rts_governor/cli.py
RTS Governor Runtime CLI — evaluate positions through the RTS Governor pipeline.

Usage:
    python3 -m core.rts_governor.cli run
    python3 -m core.rts_governor.cli log
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.rts_governor.controller import (
    full_evaluation_cycle, render_governor_status,
)
from core.state_reconciliation.protection_state import check_protection_state


async def cmd_run() -> int:
    """Run full RTS Governor evaluation cycle."""
    from control_plane.bingx_read.client import BingXReadClient
    from core.state_reconciliation.reconciler import reconcile_positions
    from core.state_reconciliation.models import PositionState

    client = BingXReadClient()
    try:
        positions = await client.get_positions()
        balance_data = await client.get_balance()
    finally:
        await client.close()

    # Build position data
    positions_data = []
    prot_states = await check_protection_state()
    prot_map = {ps.symbol: ps for ps in prot_states}

    for p in positions:
        sym = p.get("symbol", "")
        ps = prot_map.get(sym)
        positions_data.append({
            "symbol": sym,
            "side": p.get("side", "LONG"),
            "pnl": float(p.get("unrealized_pnl", 0)),
            "drawdown": max(0, -float(p.get("unrealized_pnl", 0)) / max(float(p.get("entry_price", 1)), 0.001) * 100),
            "has_sl": ps.has_sl if ps else False,
            "has_tp": ps.has_tp if ps else False,
            "sl_price": ps.sl_price if ps else None,
            "tp_price": ps.tp_price if ps else None,
        })

    daily_loss = 0.0
    if balance_data:
        realized = balance_data.get("realized_profit", 0)
        daily_loss = -float(realized)

    # Run evaluation
    decisions = await full_evaluation_cycle(positions_data, daily_loss=daily_loss)
    print(render_governor_status(decisions))
    return 0


def cmd_log() -> int:
    """Show decision log."""
    log_path = Path("/root/tradingos/control_plane/rts_governor/decision_log.jsonl")
    if not log_path.exists():
        print("No decisions logged yet")
        return 0
    with log_path.open() as f:
        lines = f.readlines()
    print(f"=== DECISION LOG ({len(lines)} entries) ===")
    for line in lines[-10:]:
        entry = json.loads(line)
        print(f"[{entry['timestamp'][:19]}] {entry['position']}: {entry['action']} | {entry['reason']}")
    return 0


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run", choices=["run", "log"])
    args = parser.parse_args()
    if args.command == "run":
        return asyncio.run(cmd_run())
    elif args.command == "log":
        return cmd_log()
    return 1


if __name__ == "__main__":
    main()
