"""Regression tests for F7 side-mapping fix (crypto pipeline restoration).

T56 (crypto selection bias forensic) found: entry_quality_gate requires BUY/SELL,
but manual_scanner passed LONG/SHORT → since 2026-08-12 13:06 every impulse-bearing
signal was suppressed as 'NO_DATA: invalid side' (211 stock / 80 crypto).

Fix: normalize side to BUY/SELL at the call site in manual_scanner.score_symbol.

These tests pin the invariant so the bug cannot regress:
- LONG → BUY, SHORT → SELL (correct normalization)
- BUY/SELL pass through unchanged
- invalid/empty/None side is rejected (gate fail-closed, not silently flipped)
- valid signals are NOT destroyed by the mapping
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


def _df():
    return pd.DataFrame({
        "ts": [TS_BASE + i * 300 for i in range(N)],
        "o": [100.0] * N, "h": [101.0] * N, "l": [99.0] * N,
        "c": [100.0] * N, "v": [1000.0] * N,
        "atr": [0.5] * N, "vm": [1.0] * N,
    })


def _mapping(side):
    """Exact fix logic: normalize scanner side to gate side at call site."""
    return {"LONG": "BUY", "SHORT": "SELL"}.get(side, side)


def test_long_maps_to_buy():
    assert _mapping("LONG") == "BUY"


def test_short_maps_to_sell():
    assert _mapping("SHORT") == "SELL"


def test_buy_sell_pass_through():
    assert _mapping("BUY") == "BUY"
    assert _mapping("SELL") == "SELL"


def test_no_side_inversion():
    """LONG must never become SELL and SHORT must never become BUY."""
    assert _mapping("LONG") != "SELL"
    assert _mapping("SHORT") != "BUY"


def test_valid_signal_not_destroyed_by_gate():
    """A normal signal with a valid mapped side must pass the gate (not NO_DATA)."""
    for side, mapped in (("LONG", "BUY"), ("SHORT", "SELL")):
        g = gate("BTCUSDT", mapped, TS_BASE + N * 300, _df(), log=False)
        assert g["decision"] in ("ALLOW", "SKIP"), f"{side} exploded: {g}"
        assert "invalid side" not in g["reason"], f"{side} wrongly rejected: {g}"


def test_invalid_side_still_rejected():
    """Gate remains fail-closed for genuinely unknown sides (T14 invariant)."""
    for bad in ("", "UNKNOWN", "HOLD", None):
        mapped = _mapping(bad)
        g = gate("BTCUSDT", mapped, TS_BASE + N * 300, _df(), log=False)
        assert g["decision"] == "SKIP"
        assert "invalid side" in g["reason"]


def test_mapping_present_in_production_code():
    """The fix must be in manual_scanner (source-inspection regression pin)."""
    import inspect
    import tradingos.signals.manual_scanner as ms
    src = inspect.getsource(ms.score_symbol)
    assert '"LONG": "BUY"' in src or "'LONG': 'BUY'" in src, "F7 mapping missing in production"
    assert "LONG" in src and "SHORT" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
