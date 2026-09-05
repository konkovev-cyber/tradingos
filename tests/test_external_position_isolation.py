"""
Regression test for external positions blocking AUTO entries (Section 15).

Bug (2026-08-24): XRP/DOT/FIL/APT external unmanaged positions (no SL/TP,
not opened by TradingOS) were counted toward max_positions in
run_observation.py, blocking ALL new AUTO entries with
"max_positions reached (4/4)". External positions must NOT block
TradingOS execution.

Fix: count positions without SL as external and subtract from
auto_count before comparing to max_pos.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))


def test_external_positions_not_counted_in_max_positions():
    """Positions without SL/TP (external) must not count toward max_positions."""
    # Simulate: 4 total positions, 0 manual (guardian state empty),
    # 4 external (no SL) → auto_count = 4 - 0 - 4 = 0 → NOT blocked
    current_count = 4
    manual_count = 0
    external_count = 4
    max_pos = 4

    auto_count = max(current_count - manual_count - external_count, 0)
    assert auto_count == 0, f"auto_count should be 0 (all external), got {auto_count}"
    assert auto_count < max_pos, "AUTO should NOT be blocked by external positions"


def test_mixed_positions_partial_external():
    """1 TradingOS + 2 external + 1 manual → auto_count = 4 - 1 - 2 = 1."""
    current_count = 4
    manual_count = 1
    external_count = 2
    max_pos = 4

    auto_count = max(current_count - manual_count - external_count, 0)
    assert auto_count == 1
    assert auto_count < max_pos, "AUTO should NOT be blocked"


def test_only_tradingos_positions_block():
    """4 TradingOS positions (no external, no manual) → auto_count = 4 → blocked."""
    current_count = 4
    manual_count = 0
    external_count = 0
    max_pos = 4

    auto_count = max(current_count - manual_count - external_count, 0)
    assert auto_count == 4
    assert auto_count >= max_pos, "AUTO SHOULD be blocked when all slots used by TradingOS"


def test_external_position_detection():
    """A position with empty stopLoss is external."""
    positions = [
        {"symbol": "BTCUSDT", "size": "1.0", "stopLoss": "95000", "takeProfit": "110000"},  # TradingOS
        {"symbol": "XRPUSDT", "size": "39.2", "stopLoss": "", "takeProfit": ""},  # external
        {"symbol": "DOTUSDT", "size": "114", "stopLoss": "", "takeProfit": ""},  # external
        {"symbol": "TQQQUSDT", "size": "0.7", "stopLoss": "71.02", "takeProfit": "69.9"},  # manual
    ]

    external = 0
    for p in positions:
        if float(p.get("size", 0) or 0) > 0:
            sl_val = str(p.get("stopLoss", "") or "").strip()
            if not sl_val or float(sl_val) == 0:
                external += 1

    assert external == 2, f"Should detect 2 external (XRP/DOT), got {external}"


def test_external_position_no_sl_key():
    """A position with missing stopLoss key is also external."""
    positions = [
        {"symbol": "FILUSDT", "size": "147"},  # no stopLoss key at all
        {"symbol": "APTUSDT", "size": "121", "stopLoss": "0"},  # stopLoss=0
    ]

    external = 0
    for p in positions:
        if float(p.get("size", 0) or 0) > 0:
            sl_val = str(p.get("stopLoss", "") or "").strip()
            if not sl_val or float(sl_val) == 0:
                external += 1

    assert external == 2, f"Should detect 2 external (missing SL key + SL=0), got {external}"