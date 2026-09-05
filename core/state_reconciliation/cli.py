"""
core/state_reconciliation/cli.py
State Reconciliation CLI — runs reconciliation + safety gate.

Usage:
    python3 -m core.state_reconciliation.cli run
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path("/root/tradingos")))

from control_plane.bingx_read.client import BingXReadClient
from core.state_reconciliation.models import PositionState
from core.state_reconciliation.reconciler import reconcile_positions, render_results
from core.state_reconciliation.safety_gate import evaluate_safety, render_safety


async def run():
    client = BingXReadClient()
    try:
        positions = await client.get_positions()
    finally:
        await client.close()

    actual = [PositionState.from_bingx(p) for p in positions]

    # Load expected state
    expected_path = Path("/root/tradingos/control_plane/bingx_read/real_account_state.json")
    expected = []
    if expected_path.exists():
        try:
            with expected_path.open() as f:
                data = json.load(f)
            for p in data.get("positions", []):
                expected.append(PositionState(
                    symbol=p.get("symbol", ""),
                    side=p.get("side", ""),
                    qty=float(p.get("qty", 0)),
                    entry_price=float(p.get("entry_price", 0)),
                    mark_price=float(p.get("mark_price", 0)),
                    stop_loss=p.get("stop_loss"),
                    take_profit=p.get("take_profit"),
                    leverage=int(p.get("leverage", 1)),
                    unrealized_pnl=float(p.get("unrealized_pnl", 0)),
                    source="BINGX_API_SAVED",
                    position_id=p.get("position_id", ""),
                ))
        except Exception:
            pass

    # Reconcile
    results = reconcile_positions(expected, actual)
    print(render_results(results))

    # Safety gate
    safety = evaluate_safety(results)
    print(render_safety(safety))

    # Save
    report_path = Path("/root/tradingos/control_plane/state_reconciliation/reconciliation_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reconciliation": [r.to_dict() for r in results],
            "safety": safety,
        }, f, indent=2)
    print(f"\nReport saved to {report_path}")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
