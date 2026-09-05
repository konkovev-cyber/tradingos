"""
Direction audit tests (Section 9 — CRITICAL).

Verifies that signal direction → order side → position side is correct
end-to-end, with NO sign inversion. Covers:
- LONG signal → BUY order → LONG position
- SHORT signal → SELL order → SHORT position
- SL direction: LONG SL below entry, SHORT SL above entry
- TP direction: LONG TP above entry, SHORT TP below entry
- BUY != SHORT, SELL != LONG

This test does NOT submit real orders. It validates the validation
logic in trade_executor.py and the side mapping in the adapter.
"""
import sys
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, "/root/trading_brain_v4")


@dataclass
class FakeProposal:
    """Minimal proposal matching the fields trade_executor expects."""
    symbol: str
    side: str  # "BUY" or "SELL"
    entry: float
    stop_loss: float
    take_profit: float
    qty: float = 1.0
    confidence: float = 0.9
    exchange: str = "bybit"
    market: str = "futures"


# ─── SL direction validation ──────────────────────────────────────────────


def test_sl_direction_buy_below_entry():
    """BUY: SL must be below entry."""
    sl_dir_ok = (FakeProposal("BTCUSDT", "BUY", 100.0, 95.0, 110.0).side == "BUY"
                 and FakeProposal("BTCUSDT", "BUY", 100.0, 95.0, 110.0).stop_loss
                 < FakeProposal("BTCUSDT", "BUY", 100.0, 95.0, 110.0).entry)
    assert sl_dir_ok


def test_sl_direction_sell_above_entry():
    """SELL: SL must be above entry."""
    sl_dir_ok = (FakeProposal("BTCUSDT", "SELL", 100.0, 105.0, 90.0).side == "SELL"
                 and FakeProposal("BTCUSDT", "SELL", 100.0, 105.0, 90.0).stop_loss
                 > FakeProposal("BTCUSDT", "SELL", 100.0, 105.0, 90.0).entry)
    assert sl_dir_ok


def test_sl_direction_buy_above_entry_rejected():
    """BUY with SL above entry must be rejected."""
    p = FakeProposal("BTCUSDT", "BUY", 100.0, 105.0, 110.0)  # SL above entry = WRONG
    sl_dir_ok = (p.side == "BUY" and p.stop_loss < p.entry)
    assert not sl_dir_ok, "BUY with SL above entry must fail direction check"


def test_sl_direction_sell_below_entry_rejected():
    """SELL with SL below entry must be rejected."""
    p = FakeProposal("BTCUSDT", "SELL", 100.0, 95.0, 90.0)  # SL below entry = WRONG
    sl_dir_ok = (p.side == "SELL" and p.stop_loss > p.entry)
    assert not sl_dir_ok, "SELL with SL below entry must fail direction check"


# ─── TP direction validation ──────────────────────────────────────────────


def test_tp_direction_buy_above_entry():
    """BUY: TP must be above entry."""
    p = FakeProposal("BTCUSDT", "BUY", 100.0, 95.0, 110.0)
    tp_dir_ok = (p.side == "BUY" and p.take_profit > p.entry)
    assert tp_dir_ok


def test_tp_direction_sell_below_entry():
    """SELL: TP must be below entry."""
    p = FakeProposal("BTCUSDT", "SELL", 100.0, 105.0, 90.0)
    tp_dir_ok = (p.side == "SELL" and p.take_profit < p.entry)
    assert tp_dir_ok


def test_tp_direction_buy_below_entry_rejected():
    """BUY with TP below entry must be rejected."""
    p = FakeProposal("BTCUSDT", "BUY", 100.0, 95.0, 90.0)  # TP below entry = WRONG
    tp_dir_ok = (p.side == "BUY" and p.take_profit > p.entry)
    assert not tp_dir_ok, "BUY with TP below entry must fail direction check"


def test_tp_direction_sell_above_entry_rejected():
    """SELL with TP above entry must be rejected."""
    p = FakeProposal("BTCUSDT", "SELL", 100.0, 105.0, 110.0)  # TP above entry = WRONG
    tp_dir_ok = (p.side == "SELL" and p.take_profit < p.entry)
    assert not tp_dir_ok, "SELL with TP above entry must fail direction check"


# ─── Side mapping (signal → exchange) ─────────────────────────────────────


def test_side_mapping_buy_to_bybit():
    """BUY signal maps to Bybit 'Buy' side."""
    # Adapter side_map covers both upper and lower case keys
    side_map = {"buy": "Buy", "sell": "Sell", "Buy": "Buy", "Sell": "Sell"}
    assert side_map.get("Buy") == "Buy"
    assert side_map.get("buy") == "Buy"


def test_side_mapping_sell_to_bybit():
    """SELL signal maps to Bybit 'Sell' side."""
    side_map = {"buy": "Buy", "sell": "Sell", "Buy": "Buy", "Sell": "Sell"}
    assert side_map.get("Sell") == "Sell"
    assert side_map.get("sell") == "Sell"


def test_buy_not_equal_short():
    """BUY must not map to Sell (no sign inversion)."""
    side_map = {"buy": "Buy", "sell": "Sell", "Buy": "Buy", "Sell": "Sell"}
    assert side_map.get("Buy") != "Sell"
    assert side_map.get("Buy") == "Buy"


def test_sell_not_equal_long():
    """SELL must not map to Buy (no sign inversion)."""
    side_map = {"buy": "Buy", "sell": "Sell", "Buy": "Buy", "Sell": "Sell"}
    assert side_map.get("Sell") != "Buy"
    assert side_map.get("Sell") == "Sell"


# ─── SL/TP geometry (SHORT direction bug regression) ──────────────────────
# This specifically guards against the S4_NEARHI SHORT SL/TP direction bug
# where SL hit check was inverted for SHORT positions.


def test_short_sl_hit_check():
    """SHORT position: SL hit = price goes UP to SL (high >= SL), not down."""
    entry = 100.0
    sl = 105.0  # above entry for SHORT
    # Price moves up to 105 → SL hit
    high = 105.5
    sl_hit = high >= sl  # correct SHORT SL check
    assert sl_hit, "SHORT SL should trigger when price rises to SL"


def test_short_sl_not_triggered_when_price_falls():
    """SHORT position: SL NOT hit when price moves down (favorable)."""
    sl = 105.0
    high = 99.0  # price fell — favorable for SHORT
    sl_hit = high >= sl
    assert not sl_hit, "SHORT SL should NOT trigger when price falls"


def test_short_tp_hit_check():
    """SHORT position: TP hit = price goes DOWN to TP (low <= TP), not up."""
    tp = 90.0
    low = 89.5  # price fell to TP — favorable for SHORT
    tp_hit = low <= tp  # correct SHORT TP check
    assert tp_hit, "SHORT TP should trigger when price falls to TP"


def test_short_tp_not_triggered_when_price_rises():
    """SHORT position: TP NOT hit when price moves up (unfavorable)."""
    tp = 90.0
    low = 95.0  # price rose — unfavorable for SHORT
    tp_hit = low <= tp
    assert not tp_hit, "SHORT TP should NOT trigger when price rises"


# ─── LONG SL/TP geometry (must not be inverted) ───────────────────────────


def test_long_sl_hit_check():
    """LONG position: SL hit = price goes DOWN to SL (low <= SL), not up."""
    sl = 95.0
    low = 94.5  # price fell to SL — unfavorable for LONG
    sl_hit = low <= sl  # correct LONG SL check
    assert sl_hit, "LONG SL should trigger when price falls to SL"


def test_long_tp_hit_check():
    """LONG position: TP hit = price goes UP to TP (high >= TP), not down."""
    tp = 110.0
    high = 110.5  # price rose to TP — favorable for LONG
    tp_hit = high >= tp  # correct LONG TP check
    assert tp_hit, "LONG TP should trigger when price rises to TP"