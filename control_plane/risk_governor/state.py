"""
control_plane/risk_governor/state.py
Risk Governor persistent state.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import DailyState

STATE_PATH = Path("/root/tradingos/control_plane/risk_governor/governor_state.json")


def load_state() -> DailyState:
    if not STATE_PATH.exists():
        return DailyState(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    try:
        with STATE_PATH.open() as f:
            d = json.load(f)
        return DailyState(
            date=d.get("date", ""),
            trades_today=d.get("trades_today", 0),
            pnl_today=d.get("pnl_today", 0.0),
            max_loss_today=d.get("max_loss_today", 0.0),
            consecutive_losses=d.get("consecutive_losses", 0),
            kill_switch_active=d.get("kill_switch_active", False),
        )
    except Exception:
        return DailyState(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def save_state(state: DailyState) -> Path:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "date": state.date,
        "trades_today": state.trades_today,
        "pnl_today": state.pnl_today,
        "max_loss_today": state.max_loss_today,
        "consecutive_losses": state.consecutive_losses,
        "kill_switch_active": state.kill_switch_active,
    }
    with STATE_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return STATE_PATH


def reset_daily_if_needed(state: DailyState) -> DailyState:
    """Reset daily counters if date changed."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.date != today:
        return DailyState(date=today)
    return state
