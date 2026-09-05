#!/usr/bin/env python3
"""
test_canonical_trade_sim.py — Unit tests for canonical trade simulator.
8 required synthetic scenarios + edge cases.

Each test has a KNOWN expected outcome. If the simulator returns anything
different, the test FAILS and no money verdict is allowed.
"""
import sys
sys.path.insert(0, "/root/tradingos/research")
import numpy as np
import pytest
from canonical_trade_sim import simulate, TradeResult

# Helper: create simple OHLC arrays
def ohlc(highs, lows, closes, ts_start=1000, ts_step=900000):
    """Create OHLC arrays. ts in ms, step=15min."""
    n = len(highs)
    ts = np.array([ts_start + i * ts_step for i in range(n)], dtype=np.int64)
    return np.array(highs, dtype=float), np.array(lows, dtype=float), np.array(closes, dtype=float), ts

# === LONG TESTS ===

def test_long_sl_only():
    """1. LONG: only SL hit on bar 3."""
    # entry=100, SL=95 (5R below), TP=115 (15R above, far away)
    # Bar 1: H=102 L=98 → no hit
    # Bar 2: H=101 L=97 → no hit
    # Bar 3: H=99 L=94 → SL hit (low <= 95)
    h, l, c, t = ohlc([102, 101, 99], [98, 97, 94], [100, 99, 96])
    r = simulate(entry_price=100, side="LONG", sl_price=95, tp_price=115,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "SL"
    assert r.gross_R == -1.0
    assert r.bars_held == 3
    assert r.net_R < 0  # SL minus costs

def test_long_tp_only():
    """2. LONG: only TP hit on bar 2."""
    # entry=100, SL=90 (10R below), TP=120 (20R above = 2R TP at SL=10)
    # Bar 1: H=105 L=95 → no hit
    # Bar 2: H=121 L=105 → TP hit (high >= 120)
    h, l, c, t = ohlc([105, 121], [95, 105], [102, 118])
    r = simulate(entry_price=100, side="LONG", sl_price=90, tp_price=120,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "TP"
    assert r.gross_R == 2.0  # (120-100)/(100-90) = 2R
    assert r.bars_held == 2

def test_long_both_same_bar():
    """3. LONG: both SL and TP hit on same bar → conservative: SL first."""
    # entry=100, SL=95, TP=110 (TP=2R since SL=5)
    # Bar 1: H=112 L=93 → both hit → SL first
    h, l, c, t = ohlc([112], [93], [100])
    r = simulate(entry_price=100, side="LONG", sl_price=95, tp_price=110,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "SL"
    assert r.gross_R == -1.0

def test_long_neither_timeout():
    """4. LONG: neither hit → timeout exit at last close."""
    # entry=100, SL=90, TP=120, all bars stay within range
    h, l, c, t = ohlc([101, 102, 103], [95, 96, 97], [100, 101, 102])
    r = simulate(entry_price=100, side="LONG", sl_price=90, tp_price=120,
                 highs=h, lows=l, closes=c, timestamps=t, max_horizon=3)
    assert r.exit_reason == "TIMEOUT"
    assert r.gross_R == (102 - 100) / (100 - 90)  # (last_close - entry) / sl_dist

# === SHORT TESTS ===

def test_short_sl_only():
    """5. SHORT: only SL hit on bar 2."""
    # entry=100, SL=105 (above for SHORT), TP=85 (below = 3R at SL=5)
    # Bar 1: H=102 L=98 → no hit
    # Bar 2: H=106 L=99 → SL hit (high >= 105)
    h, l, c, t = ohlc([102, 106], [98, 99], [100, 102])
    r = simulate(entry_price=100, side="SHORT", sl_price=105, tp_price=85,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "SL"
    assert r.gross_R == -1.0
    assert r.bars_held == 2

def test_short_tp_only():
    """6. SHORT: only TP hit on bar 3."""
    # entry=100, SL=110 (above), TP=85 (below = 3R at SL=10)
    # Bar 1: H=105 L=95 → no hit
    # Bar 2: H=102 L=90 → no hit (TP=85, low=90 > 85)
    # Bar 3: H=103 L=84 → TP hit (low <= 85)
    h, l, c, t = ohlc([105, 102, 103], [95, 90, 84], [100, 98, 86])
    r = simulate(entry_price=100, side="SHORT", sl_price=110, tp_price=85,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "TP"
    assert r.gross_R == (100 - 85) / (110 - 100)  # 1.5R

def test_short_both_same_bar():
    """7. SHORT: both SL and TP hit on same bar → conservative: SL first."""
    # entry=100, SL=105, TP=90
    # Bar 1: H=108 L=88 → both hit → SL first
    h, l, c, t = ohlc([108], [88], [100])
    r = simulate(entry_price=100, side="SHORT", sl_price=105, tp_price=90,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "SL"
    assert r.gross_R == -1.0

def test_short_neither_timeout():
    """8. SHORT: neither hit → timeout exit at last close."""
    h, l, c, t = ohlc([102, 101, 103], [95, 96, 94], [100, 99, 98])
    r = simulate(entry_price=100, side="SHORT", sl_price=110, tp_price=85,
                 highs=h, lows=l, closes=c, timestamps=t, max_horizon=3)
    assert r.exit_reason == "TIMEOUT"
    assert r.gross_R == (100 - 98) / (110 - 100)  # (entry - last_close) / sl_dist

# === EDGE CASES ===

def test_long_gap_through_sl():
    """Gap through SL: bar opens below SL."""
    # entry=100, SL=95, TP=120
    # Bar 1: H=96 L=90 C=91 → low=90 <= 95 → SL hit
    h, l, c, t = ohlc([96], [90], [91])
    r = simulate(entry_price=100, side="LONG", sl_price=95, tp_price=120,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "SL"
    assert r.gross_R == -1.0

def test_short_gap_through_sl():
    """Gap through SL for SHORT: bar opens above SL."""
    h, l, c, t = ohlc([108], [106], [107])
    r = simulate(entry_price=100, side="SHORT", sl_price=105, tp_price=85,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "SL"

def test_zero_range_candle():
    """Zero-range candle: H=L=C. Should not trigger SL or TP."""
    h, l, c, t = ohlc([100, 100, 101], [100, 100, 99], [100, 100, 100])
    r = simulate(entry_price=100, side="LONG", sl_price=95, tp_price=110,
                 highs=h, lows=l, closes=c, timestamps=t, max_horizon=3)
    # Bar 3: H=101 L=99 → no hit. Timeout.
    assert r.exit_reason == "TIMEOUT"

def test_mfe_mae_long():
    """MFE/MAE for LONG: entry=100, bar with H=110 L=90."""
    h, l, c, t = ohlc([110], [90], [100])
    r = simulate(entry_price=100, side="LONG", sl_price=80, tp_price=150,
                 highs=h, lows=l, closes=c, timestamps=t, max_horizon=1)
    # MFE = (110-100)/20 = 0.5R; MAE = (100-90)/20 = 0.5R
    assert abs(r.mfe_R - 0.5) < 0.001
    assert abs(r.mae_R - 0.5) < 0.001

def test_mfe_mae_short():
    """MFE/MAE for SHORT: entry=100, bar with H=110 L=90."""
    h, l, c, t = ohlc([110], [90], [100])
    r = simulate(entry_price=100, side="SHORT", sl_price=120, tp_price=50,
                 highs=h, lows=l, closes=c, timestamps=t, max_horizon=1)
    # MFE = (100-90)/20 = 0.5R (price went DOWN, favorable for SHORT)
    # MAE = (110-100)/20 = 0.5R (price went UP, adverse for SHORT)
    assert abs(r.mfe_R - 0.5) < 0.001
    assert abs(r.mae_R - 0.5) < 0.001

def test_cost_applied():
    """Verify that fees and slippage reduce gross to net."""
    h, l, c, t = ohlc([120], [105], [115])
    r = simulate(entry_price=100, side="LONG", sl_price=95, tp_price=120,
                 highs=h, lows=l, closes=c, timestamps=t,
                 entry_fee_bps=5.5, exit_fee_bps=5.5, slippage_bps=1.0)
    # gross_R = (120-100)/(100-95) = 4.0R
    # fee_R = (11/10000) * 100 / 5 = 0.022
    # slip_R = (2/10000) * 100 / 5 = 0.004
    # net_R = 4.0 - 0.022 - 0.004 = 3.974
    assert r.gross_R == 4.0
    assert r.fee_R > 0
    assert r.slippage_R > 0
    assert r.net_R < r.gross_R
    assert abs(r.net_R - (4.0 - r.fee_R - r.slippage_R)) < 0.0001

def test_invalid_geometry_long():
    """LONG with SL above entry → INVALID."""
    h, l, c, t = ohlc([105], [95], [100])
    r = simulate(entry_price=100, side="LONG", sl_price=105, tp_price=120,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "INVALID_GEOMETRY"

def test_invalid_geometry_short():
    """SHORT with SL below entry → INVALID."""
    h, l, c, t = ohlc([105], [95], [100])
    r = simulate(entry_price=100, side="SHORT", sl_price=95, tp_price=85,
                 highs=h, lows=l, closes=c, timestamps=t)
    assert r.exit_reason == "INVALID_GEOMETRY"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])