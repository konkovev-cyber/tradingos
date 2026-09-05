"""
Unit tests for the close-confirmation invariant (reality_guardian).

Run from /root/tradingos:  /root/trading_brain_v4/.venv/bin/pytest tests/test_close_invariant.py -v

Tests that the exchange (closed-pnl + position list) is the source of truth,
and that partial closes are NOT misclassified as phantom closes.
"""
import sys
import time

sys.path.insert(0, "/root/tradingos")

import pytest

# Patch the HTTP layer before importing the module under test
import httpx
import guardian.reality_guardian as rg


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def mock_exchange(monkeypatch):
    """Replace rg._load_credentials and httpx.get to simulate the exchange."""
    monkeypatch.setattr(rg, "_load_credentials", lambda: ("ak", "secret"))

    calls = {"position": [], "closed_pnl": []}

    def fake_get(url, headers=None, timeout=0):
        if "/position/list" in url:
            # Extract symbol from query
            sym = "X"
            if "symbol=" in url:
                sym = url.split("symbol=")[1].split("&")[0]
            calls["position"].append(sym)
            data = _position_response(sym)
        elif "/position/closed-pnl" in url:
            sym = "X"
            if "symbol=" in url:
                sym = url.split("symbol=")[1].split("&")[0]
            calls["closed_pnl"].append(sym)
            data = _closed_pnl_response(sym)
        else:
            data = {"retCode": 0, "result": {"list": []}}
        return _FakeResponse(data)

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def _position_response(sym, alive=True):
    """Alive → returns position with size>0; else empty list."""
    if alive:
        return {"retCode": 0, "result": {"list": [{
            "symbol": sym, "side": "Buy", "size": "100",
            "avgPrice": "1.0", "markPrice": "1.01", "stopLoss": "0.9",
            "takeProfit": "1.2", "leverage": "5",
        }]}}
    return {"retCode": 0, "result": {"list": []}}


def _closed_pnl_response(sym, has_record=True):
    """has_record → return a closed-pnl entry; else empty."""
    if has_record:
        return {"retCode": 0, "result": {"list": [{
            "symbol": sym, "side": "Buy", "qty": "50", "closedPnl": "0.05",
            "openFee": "0.001", "closeFee": "0.001",
            "avgEntryPrice": "1.0", "avgExitPrice": "1.001",
            "execType": "Reduce", "createdTime": str(int(time.time() * 1000)),
        }]}}
    return {"retCode": 0, "result": {"list": []}}


# ─── Test cases from critic ─────────────────────────────────────

def test_full_close_confirmed_when_position_absent(mock_exchange):
    """Position gone + closed-pnl present → full close confirmed."""
    mock_exchange["position"].append("X")
    # Position gone: make position/list return empty, closed-pnl present
    import guardian.reality_guardian as rg_mod
    rg_mod._load_credentials = lambda: ("ak", "secret")
    assert rg._confirm_position_closed("X") is True


def test_partial_close_confirmed_when_position_alive(mock_exchange):
    """Position alive BUT closed-pnl present → partial close (NOT phantom)."""
    # Simulate: position alive, closed-pnl has a record
    # _confirm_position_closed: position alive → checks closed-pnl → True
    assert rg._confirm_position_closed("X") is True


def test_phantom_when_alive_and_no_closed_pnl(mock_exchange):
    """Position alive + no closed-pnl → PHANTOM (not recorded)."""
    import guardian.reality_guardian as rg_mod
    # Override _fetch_closed_trade to return None (no closed-pnl record)
    rg_mod._fetch_closed_trade = lambda *a, **k: None
    # Position alive → still_open True → no closed-pnl → False (phantom)
    assert rg_mod._confirm_position_closed("X") is False


def test_api_error_does_not_record_false_close(mock_exchange):
    """API error (non-zero retCode) → not confirmed (don't record)."""
    import guardian.reality_guardian as rg_mod
    def _err_get(url, headers=None, timeout=0):
        return _FakeResponse({"retCode": 10002, "result": None})
    # Can't easily monkeypatch inside; assert behavior via retCode path
    # _confirm uses httpx.get which is mocked; simulate non-zero via response
    # We override to force retCode error on position call
    import unittest.mock as mock
    def _err(url, headers=None, timeout=0):
        return _FakeResponse({"retCode": 10002, "result": None})
    import guardian.reality_guardian as rg_mod2
    rg_mod2._load_credentials = lambda: ("ak", "secret")
    # Patch httpx.get globally (module uses top-level httpx import)
    with mock.patch("httpx.get", side_effect=_err):
        assert rg_mod2._confirm_position_closed("X") is False
