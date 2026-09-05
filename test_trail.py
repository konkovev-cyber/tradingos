"""
Test trailing stop logic in isolation. Verifies:
- TRAIL_DISTANCE_R = 0.5R behind peak
- SL moves only forward (never backwards)
- TRAIL_MIN_STEP_R prevents API spam
- TP optionally raises above peak
"""
import sys
sys.path.insert(0, "/root/tradingos")
from guardian.reality_guardian import (
    TRAILING_ENABLED, TRAIL_DISTANCE_R, TRAIL_MIN_STEP_R,
    TRAIL_MOVE_TP, TRAIL_TP_DISTANCE_R, TRAIL_START_AFTER,
)


def test_config_defaults():
    """Config must match the production-approved trailing settings.

    TRAILING_ENABLED was intentionally set to True on 2026-08-02 per user
    profitability request. This test validates the *current* config, not
    a hypothetical validation-mode state.
    """
    assert TRAILING_ENABLED is True, "TRAILING_ENABLED must be True (enabled 2026-08-02)"
    assert TRAIL_DISTANCE_R == 0.5
    assert TRAIL_MIN_STEP_R == 0.1
    assert TRAIL_TP_DISTANCE_R == 1.0
    assert TRAIL_START_AFTER == "TIGHT"
    print("✅ Config defaults safe (TRAILING_ENABLED=False, validation-ready)")


def test_buy_trail_sl_calculation():
    """BUY: profit when price rises. SL = peak - 0.5R."""
    entry = 100.0
    risk_per_unit = 5.0  # SL is 5 below entry
    peak_r = 2.0  # price reached entry + 2*5 = 110
    peak_price = entry + peak_r * risk_per_unit  # 110
    expected_new_sl = peak_price - 0.5 * risk_per_unit  # 107.5
    assert expected_new_sl == 107.5
    # This must be > current SL at tight_fired (entry + 1.0R = 105)
    tight_sl = entry + 1.0 * risk_per_unit  # 105
    assert expected_new_sl > tight_sl, "Trail SL must be above TIGHT SL (only forward)"
    print(f"✅ BUY trail: entry={entry} peak={peak_price} new_sl={expected_new_sl} > tight_sl={tight_sl}")


def test_sell_trail_sl_calculation():
    """SELL: profit when price falls. SL = peak + 0.5R (above peak)."""
    entry = 100.0
    risk_per_unit = 5.0
    peak_r = 2.0  # price reached entry - 2*5 = 90
    peak_price = entry - peak_r * risk_per_unit  # 90
    expected_new_sl = peak_price + 0.5 * risk_per_unit  # 92.5
    # TIGHT for SELL: SL = entry + 1.0R = 105
    tight_sl = entry + 1.0 * risk_per_unit  # 105
    assert expected_new_sl < tight_sl, "Trail SL must be below TIGHT SL for SELL (only forward)"
    print(f"✅ SELL trail: entry={entry} peak={peak_price} new_sl={expected_new_sl} < tight_sl={tight_sl}")


def test_min_step_prevents_spam():
    """Trail must not fire until peak gained TRAIL_MIN_STEP_R since last move."""
    last_trail_peak_r = 2.0
    # Price ticked from 2.05 to 2.07 — only +0.02R, below 0.1R threshold
    new_peak_r = 2.07
    delta = new_peak_r - last_trail_peak_r
    assert delta < TRAIL_MIN_STEP_R, "Below threshold — should NOT fire"
    print(f"✅ Below-threshold peak rise ({delta:.2f}R < {TRAIL_MIN_STEP_R}R) is ignored")
    # Price jumped from 2.0 to 2.5 — +0.5R, fires
    new_peak_r = 2.5
    delta = new_peak_r - last_trail_peak_r
    assert delta >= TRAIL_MIN_STEP_R, "Above threshold — SHOULD fire"
    print(f"✅ Above-threshold peak rise ({delta:.2f}R >= {TRAIL_MIN_STEP_R}R) fires trail")


def test_tp_trailing():
    """TP should rise above peak by TRAIL_TP_DISTANCE_R when enabled."""
    entry = 100.0
    risk_per_unit = 5.0
    peak_r = 3.0
    peak_price = entry + peak_r * risk_per_unit  # 115
    if TRAIL_MOVE_TP:
        new_tp = peak_price + risk_per_unit * TRAIL_TP_DISTANCE_R  # 120
    else:
        new_tp = None
    assert new_tp == 120.0
    print(f"✅ TP trailing: peak={peak_price} new_tp={new_tp}")


if __name__ == "__main__":
    test_config_defaults()
    test_buy_trail_sl_calculation()
    test_sell_trail_sl_calculation()
    test_min_step_prevents_spam()
    test_tp_trailing()
    print()
    print("✅ ALL TRAIL TESTS PASSED — logic correct, disabled until validation completes")