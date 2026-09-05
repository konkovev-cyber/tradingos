"""
control_plane/risk_governor/cli.py
Risk Governor CLI — evaluate current risk state.

Usage:
    python3 -m control_plane.risk_governor.cli status
    python3 -m control_plane.risk_governor.cli evaluate
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from control_plane.risk_governor.models import RiskLimits, DailyState
from control_plane.risk_governor.evaluator import evaluate
from control_plane.risk_governor.state import load_state, save_state, reset_daily_if_needed


def cmd_status() -> int:
    state = load_state()
    state = reset_daily_if_needed(state)
    save_state(state)

    limits = RiskLimits()
    decision = evaluate(limits, state)

    icon = {"ALLOW": "🟢", "REDUCE": "🟡", "BLOCK": "🔴"}.get(decision.verdict, "?")

    print("=" * 56)
    print("  RISK GOVERNOR — STATUS")
    print("=" * 56)
    print(f"  Date:            {state.date}")
    print(f"  Trades today:    {state.trades_today}")
    print(f"  PnL today:       {state.pnl_today:+.4f}%")
    print(f"  Max loss today:  {state.max_loss_today:.4f}%")
    print(f"  Consecutive loss:{state.consecutive_losses}")
    print(f"  Kill switch:     {'ACTIVE' if state.kill_switch_active else 'OFF'}")
    print()
    print("  ── Limits ──")
    print(f"  Max daily loss:  {limits.max_daily_loss_pct}%")
    print(f"  Max position:    {limits.max_position_pct}%")
    print(f"  Max positions:   {limits.max_positions}")
    print(f"  Kill switch:     {limits.kill_switch_loss_pct}%")
    print()
    print("  ── Verdict ──")
    print(f"  {icon} {decision.verdict}")
    print(f"  Confidence:      {decision.confidence:.0%}")
    for r in decision.reasons:
        print(f"    • {r}")
    print("=" * 56)
    return 0


def cmd_evaluate() -> int:
    """Evaluate with current BingX positions."""
    import asyncio
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from control_plane.bingx_read.client import BingXReadClient

    async def eval_with_real():
        client = BingXReadClient()
        try:
            positions = await client.get_positions()
        finally:
            await client.close()

        state = load_state()
        state = reset_daily_if_needed(state)
        save_state(state)

        limits = RiskLimits()
        total_notional = sum(p["entry_price"] * p["qty"] for p in positions)
        # rough estimate: if we had $350 equity, each position is ~3% of portfolio
        equity = 350.0  # rough estimate
        position_pct = (total_notional / equity * 100) if equity > 0 else 0

        decision = evaluate(
            limits,
            state,
            proposed_position_pct=position_pct,
            current_positions=len(positions),
        )

        icon = {"ALLOW": "🟢", "REDUCE": "🟡", "BLOCK": "🔴"}.get(decision.verdict, "?")

        print("=" * 56)
        print("  RISK GOVERNOR — EVALUATION")
        print("=" * 56)
        print(f"  Positions:      {len(positions)}")
        print(f"  Total notional: ${total_notional:.2f}")
        print(f"  Position %:     {position_pct:.1f}%")
        print()
        print("  ── Verdict ──")
        print(f"  {icon} {decision.verdict}")
        print(f"  Confidence:     {decision.confidence:.0%}")
        for r in decision.reasons:
            print(f"    • {r}")
        print("=" * 56)

    asyncio.run(eval_with_real())
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 -m control_plane.risk_governor.cli {status|evaluate}")
        return 1
    cmd = sys.argv[1]
    if cmd == "status":
        return cmd_status()
    if cmd == "evaluate":
        return cmd_evaluate()
    print(f"Unknown: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
