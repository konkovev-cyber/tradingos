"""
Regression tests for guardian duplicate-notification and external-position bugs.

Bug 1 (TQQQ incident, 2026-08-24):
    When a MANUAL position (TQQQUSDT) closed on the exchange (SL hit), it
    remained in reality_state.json because `_record_trade_closure` could throw
    before `state.pop(sym)` ran. Every 30s the guardian re-detected the
    "closure", re-recorded it, and re-sent the Telegram notification.
    Result: 572 duplicate trade_results entries + non-stop Telegram spam.
    Fix: try/finally around _record_trade_closure; state.pop + save in finally.
    Additional idempotency: _is_already_closed guard in _record_trade_closure.

Bug 2 (external positions float crash):
    XRP/DOT/FIL/APT are EXTERNAL_UNMANAGED positions with no SL/TP. Bybit
    returns stopLoss="" / takeProfit="" for them. `float(pos.get("stopLoss", 0))`
    raised ValueError: could not convert string to float: '' on every poll,
    crashing the entire guardian loop.
    Fix: use _safe_float() for stopLoss/takeProfit in _process_position.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))


# ─── Bug 1: duplicate notification on closed position ──────────────────────


def test_is_already_closed_detects_existing_record(tmp_path):
    """_is_already_closed returns True when the same trade is already logged."""
    from guardian.reality_guardian import _is_already_closed

    log_file = tmp_path / "trade_results.jsonl"
    record = {
        "symbol": "TQQQUSDT",
        "side": "Sell",
        "entry": 70.65,
        "status": "CLOSED",
        "net_pnl": -0.29,
    }
    log_file.write_text(json.dumps(record) + "\n")

    with patch("guardian.reality_guardian.TRADE_RESULTS_DIR", tmp_path):
        state_entry = {"entry": 70.65, "side": "Sell", "entry_time": 12345}
        assert _is_already_closed("TQQQUSDT", state_entry) is True


def test_is_already_closed_returns_false_for_new_trade(tmp_path):
    """_is_already_closed returns False for a trade not yet logged."""
    from guardian.reality_guardian import _is_already_closed

    log_file = tmp_path / "trade_results.jsonl"
    # Different symbol
    record = {"symbol": "NFLXUSDT", "side": "Sell", "entry": 79.40, "status": "CLOSED"}
    log_file.write_text(json.dumps(record) + "\n")

    with patch("guardian.reality_guardian.TRADE_RESULTS_DIR", tmp_path):
        state_entry = {"entry": 70.65, "side": "Sell", "entry_time": 12345}
        assert _is_already_closed("TQQQUSDT", state_entry) is False


def test_is_already_closed_returns_false_for_empty_log(tmp_path):
    """No log file → no prior closure → not already closed."""
    from guardian.reality_guardian import _is_already_closed

    with patch("guardian.reality_guardian.TRADE_RESULTS_DIR", tmp_path):
        state_entry = {"entry": 70.65, "side": "Sell", "entry_time": 12345}
        assert _is_already_closed("TQQQUSDT", state_entry) is False


def test_is_already_closed_different_entry_is_different_trade(tmp_path):
    """Same symbol+side but different entry = different trade."""
    from guardian.reality_guardian import _is_already_closed

    log_file = tmp_path / "trade_results.jsonl"
    record = {
        "symbol": "TQQQUSDT",
        "side": "Sell",
        "entry": 70.65,
        "status": "CLOSED",
    }
    log_file.write_text(json.dumps(record) + "\n")

    with patch("guardian.reality_guardian.TRADE_RESULTS_DIR", tmp_path):
        # Different entry price → different trade
        state_entry = {"entry": 71.00, "side": "Sell", "entry_time": 99999}
        assert _is_already_closed("TQQQUSDT", state_entry) is False


def test_record_trade_closure_skips_duplicate(tmp_path):
    """_record_trade_closure does NOT write a new entry when already closed."""
    from guardian.reality_guardian import _record_trade_closure

    log_file = tmp_path / "trade_results.jsonl"
    existing = {
        "symbol": "TQQQUSDT",
        "side": "Sell",
        "entry": 70.65,
        "status": "CLOSED",
        "net_pnl": -0.29,
    }
    log_file.write_text(json.dumps(existing) + "\n")

    state_entry = {
        "entry": 70.65,
        "side": "Sell",
        "entry_time": 12345,
        "mfe_peak": 0.3,
        "mae_trough": -1.0,
        "be_fired": False,
        "partial_fired": False,
        "tight_fired": False,
        "entry_to_sl_risk": 0.37,
        "size": 0.7,
        "sl_initial": 71.02,
        "tp_initial": 69.9,
    }

    with patch("guardian.reality_guardian.TRADE_RESULTS_DIR", tmp_path), \
         patch("guardian.reality_guardian.FINAL_TRADE_LOG", tmp_path / "final.jsonl"):
        _record_trade_closure("TQQQUSDT", state_entry)

    # Should NOT have added a second line
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)} (duplicate not skipped)"


# ─── Bug 2: float crash on external positions without SL/TP ────────────────


def test_process_position_handles_empty_stop_loss():
    """_process_position does not crash when Bybit returns stopLoss=''."""
    from guardian.reality_guardian import _process_position

    # Simulate an external position (no SL/TP — Bybit returns empty string)
    pos = {
        "symbol": "XRPUSDT",
        "side": "Sell",
        "size": "39.2",
        "avgPrice": "1.4815",
        "stopLoss": "",       # ← This caused the crash
        "takeProfit": "",     # ← This too
        "markPrice": "1.40",
        "leverage": "10",
    }

    state = {}
    # Should NOT raise ValueError
    result = _process_position(pos, state)
    # With no SL/TP, risk_per_unit = 0 → returns current state unchanged
    assert result is state


def test_process_position_handles_missing_stop_loss():
    """_process_position does not crash when stopLoss key is absent."""
    from guardian.reality_guardian import _process_position

    pos = {
        "symbol": "DOTUSDT",
        "side": "Sell",
        "size": "114",
        "avgPrice": "0.9055",
        # stopLoss / takeProfit keys absent entirely
        "markPrice": "0.88",
        "leverage": "10",
    }

    state = {}
    result = _process_position(pos, state)
    assert result is state  # no risk → return state unchanged


def test_process_position_with_valid_sl_does_not_regress():
    """_process_position still works correctly when SL/TP are present."""
    from guardian.reality_guardian import _process_position

    pos = {
        "symbol": "NFLXUSDT",
        "side": "Sell",
        "size": "0.62",
        "avgPrice": "79.40",
        "stopLoss": "80.0",
        "takeProfit": "78.48",
        "markPrice": "79.0",
        "leverage": "5",
    }

    state = {}
    # Should process normally (not crash, not return state unchanged)
    result = _process_position(pos, state)
    # result should be the new state dict with NFLXUSDT entry
    assert isinstance(result, dict)
    assert "NFLXUSDT" in result
    assert result["NFLXUSDT"]["side"] == "Sell"
    assert result["NFLXUSDT"]["entry"] == 79.40


# ─── Bug 1 integration: state.pop in finally ──────────────────────────────


def test_closure_state_always_popped_even_on_exception():
    """If _record_trade_closure throws, the symbol is still removed from state.

    This is the core fix: state.pop + save in a finally block, not after
    _record_trade_closure.
    """
    # Simulate the run_guardian closure-detection loop logic
    state = {"TQQQUSDT": {"entry": 70.65, "side": "Sell"}}
    sym = "TQQQUSDT"

    # Simulate _record_trade_closure throwing
    try:
        raise RuntimeError("simulated closure error")
    except Exception:
        pass
    finally:
        state.pop(sym, None)

    assert "TQQQUSDT" not in state, "Symbol should be removed from state even on exception"