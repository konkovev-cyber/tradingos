"""Tests for exit-shadow P3 simulation semantics (live-shadow, production untouched).

Pins the CONSERVATIVE intrabar contract documented in exit_shadow_engine.SIM_MODE:
  - SL check first against levels from PREVIOUS bars (SL-first on double touch)
  - partial @ +1R booked before TP (path dependency), but NOT when the same bar
    also touched the initial SL (worst case: full SL, no partial)
  - trail update happens AFTER checks → trail level acts from the NEXT bar (lag 1)
  - 72h timeout closes at last bar close
  - economics: qty-based gross − taker fees per leg − maker/taker entry fee − 2bps slip
  - re-simulation is deterministic (restart-safe idempotence)
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT / "research/exit_shadow"))

import pytest

from exit_shadow_engine import (
    FEE_MAKER, FEE_TAKER, P3_FRAC, SLIP_BPS,
    econ_p3, simulate_p3,
)

T0 = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc).timestamp()
BAR = 900


def bar(i, o, h, l, c):
    return (T0 + i * BAR, o, h, l, c)


# LONG: entry 100, SL 99 (risk=1), TP 102
E, SL, TP = 100.0, 99.0, 102.0


def test_partial_then_trail_exit():
    # LONG entry 100, SL 99 (risk=1), TP 105 (не достигается — не мешает трейлу)
    # bar0: +1R (high 101) → partial; trail после bar0 = 101-0.75 = 100.25
    # bar1: low 100.5 > 100.25 → живёт; extreme 102.2 → trail 101.45 (действует с bar2)
    # bar2: low 101.4 ≤ 101.45 → runner out at 101.45
    bars = [bar(0, 100, 101.0, 99.8, 100.8),
            bar(1, 100.8, 102.2, 100.5, 101.5),
            bar(2, 101.5, 101.8, 101.4, 101.5)]
    sim = simulate_p3("BUY", E, SL, 105.0, bars)
    assert sim["done"] and sim["partial_done"]
    assert sim["exits"][0]["type"] == "P" and sim["exits"][0]["px"] == 101.0
    assert sim["exits"][0]["frac"] == P3_FRAC
    assert sim["exits"][1]["type"] == "SL"
    assert sim["exits"][1]["px"] == pytest.approx(101.45)   # trail от extreme bar1
    assert sim["exits"][1]["frac"] == pytest.approx(1 - P3_FRAC)


def test_sl_first_no_partial_on_double_touch():
    # bar touches BOTH +1R (high 101.2) and initial SL (low 99) → worst case:
    # full exit at SL, no partial booked
    bars = [bar(0, 100, 101.2, 99.0, 100.0)]
    sim = simulate_p3("BUY", E, SL, TP, bars)
    assert sim["done"] and not sim["partial_done"]
    assert len(sim["exits"]) == 1
    assert sim["exits"][0]["type"] == "SL" and sim["exits"][0]["px"] == 99.0
    assert sim["exits"][0]["frac"] == 1.0


def test_partial_before_tp_same_bar():
    # bar touches +1R and TP(102) together → partial 33% @101 then 67% @TP
    bars = [bar(0, 100, 102.5, 100.2, 102.4)]
    sim = simulate_p3("BUY", E, SL, TP, bars)
    assert sim["done"] and sim["partial_done"]
    types = [e["type"] for e in sim["exits"]]
    assert types == ["P", "TP"]
    assert sim["exits"][1]["frac"] == pytest.approx(1 - P3_FRAC)


def test_tp_below_1r_never_partials():
    # TP at +0.5R: TP check fires before partial level reachable
    tp = 100.5
    bars = [bar(0, 100, 100.5, 99.9, 100.4)]
    sim = simulate_p3("BUY", E, SL, tp, bars)
    assert sim["done"] and not sim["partial_done"]
    assert sim["exits"][0]["type"] == "TP" and sim["exits"][0]["frac"] == 1.0


def test_initial_sl_without_partial():
    bars = [bar(0, 100, 100.4, 99.1, 99.3), bar(1, 99.3, 99.5, 99.0, 99.0)]
    sim = simulate_p3("BUY", E, SL, TP, bars)
    assert sim["done"] and not sim["partial_done"]
    assert sim["exits"][0]["type"] == "SL" and sim["exits"][0]["px"] == 99.0
    assert sim["exits"][0]["frac"] == 1.0


def test_trail_lags_one_bar():
    # TP 105 — не мешает. bar1 extreme 103 → trail 102.25 действует с bar2;
    # сам bar1 проверяется только против sl от bar0 (100.25): low 101.5 > 100.25 → живёт.
    # Если бы трейл применялся внутри того же бара, выход был бы на bar1 (ts bar1);
    # с lag-1 выход на bar2.
    bars = [bar(0, 100, 101.0, 99.8, 100.9),        # partial @101, sl→100.25
            bar(1, 101, 103.0, 101.5, 102.5),        # SL vs 100.25 pass; trail→102.25
            bar(2, 102.5, 102.8, 102.2, 102.6)]      # low 102.2 <= 102.25 → out
    sim = simulate_p3("BUY", E, SL, 105.0, bars)
    assert sim["done"]
    last = sim["exits"][-1]
    assert last["type"] == "SL" and last["px"] == pytest.approx(102.25)
    assert last["ts"] == T0 + 2 * BAR   # выход на bar2, не bar1 → lag подтверждён


def test_timeout_72h_closes_at_last_close():
    bars = []
    n = int(72 * 3600 / BAR) + 2
    px = 100.0
    for i in range(n):
        bars.append(bar(i, px, px + 0.05, px - 0.05, px))  # flat, no levels touched
    sim = simulate_p3("BUY", E, SL, TP, bars)
    assert sim["done"]
    last = sim["exits"][-1]
    assert last["type"] == "T72" and last["frac"] == 1.0


def test_short_symmetry():
    # SHORT: entry 100, SL 101 (risk 1), TP 95 (не достигается)
    # bar0: low 99.0 → partial @99; trail после bar0 = 99+0.75 = 99.75
    # bar1: high 99.6 < 99.75 → живёт; extreme low 97.8 → trail 98.55 (с bar2)
    # bar2: high 98.6 >= 98.55 → out at 98.55
    bars = [bar(0, 100, 100.2, 99.0, 99.5),
            bar(1, 99.5, 99.6, 97.8, 98.2),
            bar(2, 98.2, 98.6, 98.1, 98.3)]
    sim = simulate_p3("SELL", E, 101.0, 95.0, bars)
    assert sim["done"] and sim["partial_done"]
    assert sim["exits"][0]["type"] == "P" and sim["exits"][0]["px"] == 99.0
    assert sim["exits"][1]["type"] == "SL" and sim["exits"][1]["px"] == pytest.approx(98.55)


def test_econ_math():
    # qty=2, entry 100: partial 33% @101, runner @102
    exits = [{"type": "P", "ts": T0, "px": 101.0, "frac": P3_FRAC},
             {"type": "SL", "ts": T0 + BAR, "px": 102.0, "frac": 1 - P3_FRAC}]
    e = econ_p3("BUY", 100.0, 2.0, exits, entry_fee_rate=FEE_TAKER)
    gross = (101 - 100) * 2 * P3_FRAC + (102 - 100) * 2 * (1 - P3_FRAC)
    fees = FEE_TAKER * 200 * P3_FRAC + FEE_TAKER * 200 * (1 - P3_FRAC) + FEE_TAKER * 200
    slip = SLIP_BPS * 101.0 * 2 * P3_FRAC + SLIP_BPS * 102.0 * 2 * (1 - P3_FRAC)
    assert e["gross"] == pytest.approx(gross, abs=1e-6)
    assert e["fees"] == pytest.approx(fees, abs=1e-6)
    assert e["slippage"] == pytest.approx(slip, abs=1e-6)
    assert e["net"] == pytest.approx(gross - fees - slip, abs=1e-5)


def test_resimulation_deterministic():
    bars = [bar(0, 100, 101.2, 99.9, 100.9), bar(1, 100.9, 102.4, 100.5, 102.0),
            bar(2, 102.0, 102.6, 101.9, 102.1), bar(3, 102.1, 102.3, 101.8, 101.9)]
    s1 = simulate_p3("BUY", E, SL, TP, bars)
    s2 = simulate_p3("BUY", E, SL, TP, bars)
    assert s1 == s2


def test_maker_entry_cheaper_than_taker():
    exits = [{"type": "TP", "ts": T0, "px": 102.0, "frac": 1.0}]
    a = econ_p3("BUY", 100.0, 2.0, exits, FEE_TAKER)
    c = econ_p3("BUY", 100.0, 2.0, exits, FEE_MAKER)
    assert c["net"] > a["net"]
    assert c["net"] - a["net"] == pytest.approx((FEE_TAKER - FEE_MAKER) * 200, abs=1e-9)
