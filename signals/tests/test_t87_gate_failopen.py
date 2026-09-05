"""Regression tests for T87 gate fail-open hardening.

T85 auditor found: entry_quality_gate returned ALLOW/NORMAL when analyze_impulse
returned None (no impulse) BEFORE validating the side — so invalid side + no
impulse = ALLOW. Safety rule: absence of a valid side always means NO ORDER.

Fix: side validation moved BEFORE the no-impulse early return.
"""
import sys
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from trade.entry_quality_gate import gate

N = 200
TS_BASE = 1786590000.0


def _flat_df():
    """Flat candles: analyze_impulse returns None (no displacement)."""
    return pd.DataFrame({
        "ts": [TS_BASE + i * 300 for i in range(N)],
        "o": [100.0] * N, "h": [100.1] * N, "l": [99.9] * N,
        "c": [100.0] * N, "v": [1000.0] * N,
        "atr": [0.5] * N, "vm": [1.0] * N,
    })


def test_invalid_side_no_impulse_is_skipped():
    """invalid side + no impulse must be SKIP (NO_DATA), never ALLOW."""
    for bad in ("", "UNKNOWN", "LONG", "SHORT", None, "HOLD"):
        g = gate("BTCUSDT", bad, TS_BASE + N * 300, _flat_df(), log=False)
        assert g["decision"] == "SKIP", f"side={bad!r} → {g}"
        assert "invalid side" in g["reason"]


def test_valid_side_no_impulse_is_allowed():
    """valid side + no impulse → NORMAL/ALLOW (unchanged behavior)."""
    for side in ("BUY", "SELL"):
        g = gate("BTCUSDT", side, TS_BASE + N * 300, _flat_df(), log=False)
        assert g["decision"] == "ALLOW", f"{side} + no impulse → {g}"


def test_side_check_precedes_impulse_analysis_in_source():
    """Source-inspection pin: side validation must appear before the no-impulse
    early return in the gate function."""
    src = (ROOT / "trade/entry_quality_gate.py").read_text()
    side_check = src.index("side not in (\"BUY\", \"SELL\")")
    no_impulse_return = src.index('imp is None')
    assert side_check < no_impulse_return, (
        "side validation must precede the no-impulse early return"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
