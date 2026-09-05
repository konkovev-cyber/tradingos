"""Regression tests for T85 NEUTRAL-contamination fix in _derive_trigger.

T85 found: `_derive_trigger` used `else: momentum_ok = momentum < -threshold`,
so a NEUTRAL setup.direction received SELL-like momentum confirmation
(negative momentum boosted trigger_score by +0.3 and raised final_probability).
NEUTRAL must NEVER become BUY or SELL confirmation via a generic else branch.

Fix: explicit three-state (BUY → BUY-criterion, SELL → SELL-criterion,
NEUTRAL/None/invalid → momentum_ok=False). No directional semantics in else.
"""
import sys
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))

import pytest

from tradingos.signals.signal_generator import SignalGenerator


def _trigger(direction, momentum):
    gen = SignalGenerator.__new__(SignalGenerator)
    gen.CONFIG = {"volume_spike_ratio": 2.0, "momentum_threshold": 0.001}
    from tradingos.signals.signal_generator import Setup
    from tradingos.signals.feature_vector import FeatureVector

    fv = FeatureVector(
        timestamp_ms=0, symbol="TESTUSDT",
        open=100.0, high=100.0, low=100.0, close=100.0 + momentum * 100.0,
        volume=1000.0, ema20=100.0, ema50=100.0, ema200=100.0,
        rsi=50.0, macd_line=0.0, macd_signal=0.0, atr=1.0,
        bb_upper=101.0, bb_lower=99.0, bb_middle=100.0, adx=20.0,
        volume_ma=1000.0, volume_ratio=1.0, obv=0.0, vwap=100.0,
        ema_bullish=False, price_above_ema50=False,
        rsi_overbought=False, rsi_oversold=False,
    )
    setup = Setup(direction=direction, ema_distance_pct=1.0, rsi=50.0,
                  volume_ratio=1.0, is_pullback=False, score=0.5)
    return gen._derive_trigger(fv, setup)


@pytest.mark.parametrize("direction", ["NEUTRAL", None, "UNKNOWN", ""])
def test_neutral_never_gets_directional_confirmation(direction):
    """NEUTRAL/None/invalid + any momentum → momentum_ok must be False."""
    for momentum in (0.005, -0.005, 0.0):
        t = _trigger(direction, momentum)
        assert t.momentum_ok is False, (
            f"direction={direction!r} momentum={momentum} → momentum_ok={t.momentum_ok} "
            "(NEUTRAL/invalid must never confirm a side)"
        )


def test_buy_positive_momentum_confirms():
    t = _trigger("BUY", 0.005)
    assert t.momentum_ok is True


def test_buy_negative_momentum_not_confirmed():
    t = _trigger("BUY", -0.005)
    assert t.momentum_ok is False


def test_sell_negative_momentum_confirms():
    t = _trigger("SELL", -0.005)
    assert t.momentum_ok is True


def test_sell_positive_momentum_not_confirmed():
    t = _trigger("SELL", 0.005)
    assert t.momentum_ok is False


def test_production_code_has_no_generic_else_momentum():
    """Source-inspection pin: the SELL momentum assignment must be guarded by
    an explicit `elif direction == "SELL"`, never a generic `else`."""
    src = (ROOT / "signals/signal_generator.py").read_text()
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if "momentum_ok = momentum < -self.CONFIG" in ln:
            prev = lines[i - 1].strip()
            assert prev.startswith('elif setup.direction == "SELL"'), (
                f"line {i+1}: SELL momentum not guarded by elif SELL: prev={prev!r}"
            )
    # The explicit three-state and NEUTRAL invariant comment must be present
    assert 'if setup.direction == "BUY":' in src
    assert 'elif setup.direction == "SELL":' in src
    assert "NEUTRAL никогда не получает" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
