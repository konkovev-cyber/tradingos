"""
control_plane/bingx_read/pipeline.py
Full pipeline with BingX Real Account as ground truth.

READ-ONLY. No execution. No LIVE changes.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path("/root/tradingos")))

from control_plane.bingx_read.adapter import fetch_real_account_state, save_state
from control_plane.bingx_read.client import BingXReadClient

REPORT_PATH = Path("/root/tradingos/control_plane/bingx_read/real_shadow_report.json")


def render_positions(positions: list) -> str:
    lines = []
    for p in sorted(positions, key=lambda x: -x["unrealized_pnl"]):
        emoji = "🟢" if p["unrealized_pnl"] > 0 else "🔴"
        lines.append(
            f"    {emoji} {p['symbol']:12s} {p['side']:5s} "
            f"qty={p['qty']:8.4f} entry={p['entry_price']:10.4f} "
            f"pnl={p['unrealized_pnl']:+.4f}"
        )
    return "\n".join(lines)


def generate_report(state: dict) -> dict:
    """Generate real account shadow report."""
    positions = state.get("positions", [])
    total_unrealized = state.get("total_unrealized", 0)
    balance = state.get("balance", {})

    in_profit = sum(1 for p in positions if p["unrealized_pnl"] > 0)
    in_loss = len(positions) - in_profit

    concentration = {}
    total_abs = sum(abs(p["unrealized_pnl"]) for p in positions) or 1.0
    for p in positions:
        concentration[p["symbol"]] = round(abs(p["unrealized_pnl"]) / total_abs * 100, 1)
    biggest_sym = max(concentration, key=concentration.get) if concentration else ""
    biggest_pct = concentration.get(biggest_sym, 0)

    report = {
        "timestamp": state.get("timestamp", ""),
        "source": "BINGX_API_HMAC",
        "execution": "BLOCKED",
        "account": {
            "equity": balance.get("equity", 0),
            "unrealized_pnl": total_unrealized,
            "position_count": len(positions),
            "in_profit": in_profit,
            "in_loss": in_loss,
        },
        "concentration": {
            "biggest_symbol": biggest_sym,
            "biggest_pct": biggest_pct,
            "status": "CRITICAL" if biggest_pct > 70 else "HIGH" if biggest_pct > 50 else "OK",
        },
        "positions": positions,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w") as f:
        json.dump(report, f, indent=2)
    return report


async def run_pipeline():
    print("=" * 60)
    print("  TRADINGOS — REAL ACCOUNT SHADOW TEST")
    print("  BingX API → Read Adapter → Report")
    print("=" * 60)

    state = await fetch_real_account_state()
    save_state(state)
    report = generate_report(state)

    positions = state.get("positions", [])
    total = state.get("total_unrealized", 0)
    balance = state.get("balance", {})
    conc = report.get("concentration", {})

    print(f"\n  Timestamp:    {state.get('timestamp', '')}")
    print(f"  Source:       BingX API (HMAC signed)")
    print(f"  Positions:    {len(positions)}")
    print(f"  Unrealized:   {total:+.4f} USDT")
    print(f"  Equity:       {balance.get('equity', 0):.4f} USDT")
    print(f"\n  ── Positions ──")
    print(render_positions(positions))
    print(f"\n  ── Concentration ──")
    print(f"  Biggest:      {conc.get('biggest_symbol', '')} ({conc.get('biggest_pct', 0)}%)")
    print(f"  Status:       {conc.get('status', 'UNKNOWN')}")
    print(f"\n  Mode:         SHADOW_REAL_ACCOUNT")
    print(f"  Execution:    BLOCKED")
    print(f"  Report:       {REPORT_PATH}")
    print("=" * 60)


def main():
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
