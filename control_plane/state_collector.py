"""
control_plane/state_collector.py
Unified State Collector — aggregates all adapters into single snapshot.

Read-only. No LIVE changes. No experiment modifications.
Outputs: /root/tradingos/control_plane/snapshots/current_state.json
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from control_plane.models import TradingOSState
from control_plane.adapters.position_adapter import collect as position_collect
from control_plane.adapters.capital_adapter import collect as capital_collect
from control_plane.adapters.execution_adapter import collect as execution_collect
from control_plane.adapters.research_adapter import collect as research_collect

SNAPSHOT_PATH = Path("/root/tradingos/control_plane/snapshots/current_state.json")


def collect_state() -> TradingOSState:
    """Gather state from all adapters."""
    state = TradingOSState(
        timestamp=TradingOSState.now_iso(),
        system_status="OPERATIONAL",
    )
    state.research = research_collect()
    state.positions = position_collect()
    state.capital = capital_collect()
    state.execution = execution_collect()
    return state


def state_to_dict(state: TradingOSState) -> dict:
    """Convert state to plain dict for JSON serialization."""
    return {
        "timestamp": state.timestamp,
        "system_status": state.system_status,
        "research": {
            "status": state.research.status,
            "active_experiments": state.research.active_experiments,
            "evidence_score": state.research.evidence_score,
            "experiments": state.research.experiments,
        },
        "positions": {
            "open_count": state.positions.open_count,
            "unrealized_pnl": state.positions.unrealized_pnl,
            "in_profit": state.positions.in_profit,
            "in_loss": state.positions.in_loss,
            "guard_mode": state.positions.guard_mode,
            "guard_valid_signals": state.positions.guard_valid_signals,
            "action_shadow_unique": state.positions.action_shadow_unique,
            "pending_actions": state.positions.pending_actions,
            "risk_concentration": state.positions.risk_concentration,
        },
        "capital": {
            "total_unrealized": state.capital.total_unrealized,
            "total_realized": state.capital.total_realized,
            "closed_trades": state.capital.closed_trades,
            "open_positions": state.capital.open_positions,
            "biggest_risk_symbol": state.capital.biggest_risk_symbol,
            "biggest_risk_pct": state.capital.biggest_risk_pct,
            "status": state.capital.status,
        },
        "execution": {
            "ubot_status": state.execution.ubot_status,
            "pie_status": state.execution.pie_status,
            "guard_status": state.execution.guard_status,
            "live_permission": state.execution.live_permission,
            "services_running": state.execution.services_running,
            "services_total": state.execution.services_total,
        },
    }


def save_snapshot(state: TradingOSState) -> Path:
    """Write state to JSON file."""
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = state_to_dict(state)
    with SNAPSHOT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return SNAPSHOT_PATH


def render_text(state: TradingOSState) -> str:
    """Human-readable output."""
    cap_emoji = {"PROFITABLE": "🟢", "NEUTRAL": "🟡", "DRAWDOWN": "🔴"}.get(state.capital.status, "⚪")
    lines = [
        "=" * 60,
        "  TRADINGOS — UNIFIED STATE",
        "=" * 60,
        f"  Timestamp:  {state.timestamp}",
        f"  Status:     {state.system_status}",
        "",
        "  ── Research ──",
        f"    Status:           {state.research.status}",
        f"    Experiments:      {state.research.active_experiments}",
        f"    Evidence score:   {state.research.evidence_score}/100",
        "",
        "  ── Positions ──",
        f"    Open:             {state.positions.open_count}",
        f"    Guard mode:       {state.positions.guard_mode}",
        f"    Valid signals:    {state.positions.guard_valid_signals}/20",
        f"    Action unique:    {state.positions.action_shadow_unique}/20",
        "",
        "  ── Capital ──",
        f"    Unrealized PnL:   {state.capital.total_unrealized:+.4f} USDT",
        f"    Realized PnL:     {state.capital.total_realized:+.4f} USDT",
        f"    Biggest risk:     {state.capital.biggest_risk_symbol} ({state.capital.biggest_risk_pct}%)",
        f"    Status:           {cap_emoji} {state.capital.status}",
        "",
        "  ── Execution ──",
        f"    Services:         {state.execution.services_running}/{state.execution.services_total} running",
        f"    ubot-bingx:       {state.execution.ubot_status}",
        f"    pie-observer:     {state.execution.pie_status}",
        f"    position-guard:   {state.execution.guard_status}",
        f"    Live permission:  {state.execution.live_permission}",
        "",
        "=" * 60,
    ]
    return "\n".join(lines)


def main() -> int:
    state = collect_state()
    path = save_snapshot(state)
    print(render_text(state))
    print(f"\nSnapshot saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
