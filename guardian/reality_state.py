"""
guardian/reality_state.py
Reality Guardian State — minimal persistence for Reality Pilot positions.
Stores Guardian memory (MFE peak, trigger states) for recovery after restart.
Source of truth: Bybit exchange. JSON is supporting memory only.
state.json NEVER opens, closes, or modifies positions/SL/TP.
"""
import json, logging, os, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("reality_guardian")

STATE_PATH = Path("/root/tradingos/guardian/reality_state.json")

# Allowed fields only — no execution authority
STATE_SCHEMA = {
    "symbol": str,
    "side": str,
    "entry": float,
    "open_time": float,
    "mfe_peak": float,
    "be_fired": bool,
    "partial_fired": bool,
}


def _validate_state(state: dict) -> bool:
    """Validate state against allowed schema."""
    for field, field_type in STATE_SCHEMA.items():
        if field not in state:
            return False
        if not isinstance(state[field], field_type):
            return False
    return True


def load_state() -> Optional[dict]:
    """Load Guardian state from file. Returns None if missing or invalid."""
    if not STATE_PATH.exists():
        return None
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
        if _validate_state(state):
            return state
        else:
            logger.warning("Invalid state.json schema — ignoring")
            return None
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Failed to load state.json: {e}")
        return None


def save_state(symbol: str, side: str, entry: float, open_time: float,
               mfe_peak: float = 0.0, be_fired: bool = False,
               partial_fired: bool = False):
    """
    Save Guardian state to file.
    Only stores memory — does NOT affect positions, SL, or TP.
    """
    state = {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "open_time": open_time,
        "mfe_peak": mfe_peak,
        "be_fired": be_fired,
        "partial_fired": partial_fired,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    logger.info(f"Guardian state saved: {symbol} {side} mfe_peak={mfe_peak:.2f}R")


def clear_state():
    """Clear Guardian state after position close."""
    if STATE_PATH.exists():
        STATE_PATH.unlink()
        logger.info("Guardian state cleared")


def reconcile_with_exchange(exchange_position: Optional[dict]) -> Optional[dict]:
    """
    Reconcile saved state with actual exchange position.
    Exchange is source of truth.
    
    Args:
        exchange_position: dict with symbol, side, entry from Bybit API
    
    Returns:
        Restored state dict if match, None if mismatch or no position.
    """
    saved = load_state()
    if not saved:
        return None
    
    if not exchange_position:
        logger.warning("No exchange position found — clearing stale state")
        clear_state()
        return None
    
    # Compare key fields
    if (saved["symbol"] == exchange_position.get("symbol") and
            saved["side"] == exchange_position.get("side") and
            abs(saved["entry"] - float(exchange_position.get("avgPrice", 0))) < 0.01):
        logger.info(f"Guardian state reconciled: {saved['symbol']} {saved['side']}")
        return saved
    else:
        logger.warning(
            f"State mismatch: saved={saved['symbol']}/{saved['side']} "
            f"vs exchange={exchange_position.get('symbol')}/{exchange_position.get('side')} "
            f"— clearing stale state"
        )
        clear_state()
        return None
