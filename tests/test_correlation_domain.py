"""
Regression tests: correlation filter is scoped per market domain.

Fix target: strategies/trade_executor.py — the same-side correlation counter
(_same_side_in_domain) now counts ONLY open positions whose market_domain
matches the proposal's. market_domain classifies from the EXCHANGE's real
symbolType (/v5/market/instruments-info, cached one-request-per-symbol per
process) fed into classify_instrument. A MANUAL tokenized-stock position
(NFLXUSDT) must NOT block an AUTO crypto proposal (BTCUSDT), while the crypto
same-side limit inside its own domain stays unchanged. Source (MANUAL/AUTO) is
NOT a criterion — position dicts carry no distinguishable source field.
Unknown symbol / API failure → OTHER, NEVER CRYPTO.

Run from /root/tradingos:  python3 -m pytest tests/test_correlation_domain.py -v
No real API calls: the symbolType fetch is replaced with a deterministic map
(_FAKED_TYPES); __httpx.get__ is mocked for the cache-semantics tests.
"""
import asyncio
import io
import json as _json
import sys

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root")

import pytest

import tradingos.strategies.trade_executor as te
from tradingos.strategies.trade_executor import (
    RealityTradeProposal,
    _execute_reality,
    _same_side_in_domain,
    market_domain,
)

# Original implementation of the symbolType fetch, captured before the autouse
# fixture replaces it — cache-semantics tests restore this and mock httpx.get.
_ORIG_SYMBOL_TYPE = te._symbol_type

# Correlation-relevant subset of trading_mode.json (kill_switch/sell_disabled
# must stay OFF so tests reach the correlation filter; blacklist stays empty).
_TEST_MODE_CFG = {
    "kill_switch": False,
    "sell_disabled": False,
    "risk_per_trade": 0.5,
    "max_same_side_positions": 1,
    "max_leverage": 5,
}

# Mocked symbolType map — the stand-in for /v5/market/instruments-info.
# Symbols NOT in the map are unknown to the exchange → fetch returns None.
_FAKED_TYPES: dict[str, str] = {}


def _fake_symbol_type(symbol: str):
    return _FAKED_TYPES.get(symbol)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    """No real network: every test replaces the symbolType fetch with a
    deterministic map (cleared per test)."""
    _FAKED_TYPES.clear()
    monkeypatch.setattr(te, "_symbol_type", _fake_symbol_type)


def _types(*stocks: str) -> None:
    """Set the fake symbolType map: BTC/ETH/SOL → '' (crypto perp), given
    tickers → 'stock'. Unlisted symbols → unknown (None)."""
    _FAKED_TYPES.clear()
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        _FAKED_TYPES[s] = ""
    for s in stocks:
        _FAKED_TYPES[s] = "stock"


def _proposal(symbol="BTCUSDT", side="BUY", entry=100.0, sl=100.0, tp=105.0):
    return RealityTradeProposal(
        symbol=symbol, side=side, entry=entry, stop_loss=sl, take_profit=tp,
        rr=2.0, confidence=0.9, strategy="REALITY_DISCOVERY",
        decision_id="t-corr", status="APPROVED",
    )


@pytest.fixture
def exec_path(monkeypatch):
    """Drive _execute_reality through hermetic local gates — no network, no
    live config. entry==stop_loss makes the post-correlation SL guard return
    "Invalid SL (zero risk distance)", a marker that execution PASSED the
    correlation filter (and kill_switch/guard/blacklist) without crashing."""
    import builtins

    import tradingos.strategies.deposit_guard as dg_mod

    real_open = builtins.open

    def _open(path, *a, **k):
        p = str(path)
        if p.endswith("trading_mode.json"):
            return io.StringIO(_json.dumps(_TEST_MODE_CFG))
        if p.endswith("symbol_blacklist.json"):
            return io.StringIO("{}")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", _open)

    class _FakeGuard:
        def can_open_position(self, *a, **k):
            return True, "OK"

        def on_position_opened(self, *a, **k):
            pass

    monkeypatch.setattr(dg_mod, "get_guard", lambda: _FakeGuard())
    monkeypatch.setattr(te._execute_reality, "_last_trade_time", 0, raising=False)
    return te


def _set_open_positions(monkeypatch, positions):
    import tradingos.strategies.bybit_position_check as bpc
    monkeypatch.setattr(bpc, "get_open_positions_with_side", lambda: positions)


# ─── required classification table ─────────────────────────────
@pytest.mark.parametrize("symbol,expected_domain", [
    ("BTCUSDT", "CRYPTO"),
    ("ETHUSDT", "CRYPTO"),
    ("NFLXUSDT", "TRADFI"),
    ("AAPLUSDT", "TRADFI"),
    ("NVDAUSDT", "TRADFI"),
    ("METAUSDT", "TRADFI"),
    ("SQQQUSDT", "TRADFI"),
    ("TQQQUSDT", "TRADFI"),
    ("SPCXUSDT", "TRADFI"),
])
def test_classification_table(symbol, expected_domain):
    _types("NFLXUSDT", "AAPLUSDT", "NVDAUSDT", "METAUSDT",
           "SQQQUSDT", "TQQQUSDT", "SPCXUSDT")
    assert market_domain(symbol) == expected_domain


def test_unknown_symbol_is_other():
    # No map entry → fetch returns None → OTHER, never CRYPTO
    assert market_domain("UNKNOWN123") == "OTHER"
    assert market_domain("NONEXISTENT") == "OTHER"
    assert market_domain("") == "OTHER"


# ─── market_domain classification ─────────────────────────────────
def test_market_domain_classification():
    _types("NFLXUSDT")
    assert market_domain("BTCUSDT") == "CRYPTO"
    assert market_domain("ETHUSDT") == "CRYPTO"
    # tokenized stock via REAL symbolType → own domain (≠ CRYPTO)
    assert market_domain("NFLXUSDT") == "TRADFI"
    assert market_domain("") == "OTHER"


def test_gold_instrument_api_unavailable_is_other():
    # XAUUSDT has no name-based fallback: with the API fetch unavailable (None),
    # it must NOT be assumed CRYPTO (which would burn the crypto budget) —
    # unlike the old name-fallback behaviour that put XAUUSDT into CRYPTO.
    _FAKED_TYPES["BTCUSDT"] = ""
    assert market_domain("XAUUSDT") == "OTHER"
    assert _same_side_in_domain(
        "BTCUSDT", "Buy", [{"symbol": "XAUUSDT", "side": "Buy", "size": 1.0}]) == 0


def test_gold_instrument_commodity_domain():
    _FAKED_TYPES["XAUUSDT"] = "commodity"
    _FAKED_TYPES["BTCUSDT"] = ""
    assert market_domain("XAUUSDT") == "COMMODITY"
    assert _same_side_in_domain(
        "BTCUSDT", "Buy", [{"symbol": "XAUUSDT", "side": "Buy", "size": 1.0}]) == 0


# ─── (a) MANUAL TRADFI Buy must NOT block AUTO CRYPTO Buy ─────────
def test_tradfi_position_not_counted_in_crypto_domain():
    _types("NFLXUSDT")
    open_pos = [{"symbol": "NFLXUSDT", "side": "Buy", "size": 1.0}]
    assert _same_side_in_domain("BTCUSDT", "Buy", open_pos) == 0


@pytest.mark.parametrize("stock", ["NFLXUSDT", "AAPLUSDT", "NVDAUSDT",
                                   "METAUSDT", "SQQQUSDT", "TQQQUSDT", "SPCXUSDT"])
def test_manual_tradfi_buy_does_not_block_auto_crypto_buy(exec_path, monkeypatch, stock):
    _types(stock)
    _set_open_positions(monkeypatch, [{"symbol": stock, "side": "Buy", "size": 1.0}])
    result = asyncio.run(_execute_reality(_proposal("BTCUSDT", "BUY")))
    # Reached the post-correlation SL guard ⇒ correlation ALLOWED the proposal
    assert result["status"] == "ERROR"
    assert result["error"] == "Invalid SL (zero risk distance)"


# ─── (b) AUTO CRYPTO ↔ AUTO CRYPTO same-side limit unchanged ─────
def test_auto_crypto_buy_still_blocked_within_domain(exec_path, monkeypatch):
    _types()
    _set_open_positions(monkeypatch, [
        {"symbol": "ETHUSDT", "side": "Buy", "size": 1.0},
        {"symbol": "SOLUSDT", "side": "Buy", "size": 1.0},
    ])
    result = asyncio.run(_execute_reality(_proposal("BTCUSDT", "BUY")))
    assert result["status"] == "BLOCKED"
    assert result["error"] == "Too many Buy positions (2/1)"


def test_crypto_buy_blocked_at_max_one(exec_path, monkeypatch):
    # max_same_side_positions=1: a single open CRYPTO Buy blocks a new one.
    _types()
    _set_open_positions(monkeypatch, [{"symbol": "ETHUSDT", "side": "Buy", "size": 1.0}])
    result = asyncio.run(_execute_reality(_proposal("BTCUSDT", "BUY")))
    assert result["status"] == "BLOCKED"
    assert result["error"] == "Too many Buy positions (1/1)"


# ─── (c) opposite side never counted ─────────────────────────────
def test_crypto_sell_not_blocked_by_crypto_buy():
    _types()
    open_pos = [{"symbol": "ETHUSDT", "side": "Buy", "size": 1.0}]
    assert _same_side_in_domain("BTCUSDT", "Sell", open_pos) == 0
    assert _same_side_in_domain("BTCUSDT", "Buy", open_pos) == 1


def test_same_side_counts_only_own_side():
    _types()
    open_pos = [
        {"symbol": "ETHUSDT", "side": "Buy", "size": 1.0},
        {"symbol": "SOLUSDT", "side": "Sell", "size": 1.0},
    ]
    assert _same_side_in_domain("BTCUSDT", "Sell", open_pos) == 1
    assert _same_side_in_domain("BTCUSDT", "Buy", open_pos) == 1


# ─── (d) source is not a criterion inside the domain ─────────────
def test_crypto_positions_count_regardless_of_source():
    _types()
    open_pos = [
        {"symbol": "ETHUSDT", "side": "Buy", "size": 1.0},              # no source field
        {"symbol": "SOLUSDT", "side": "Buy", "size": 1.0, "source": "MANUAL"},
    ]
    # Same domain (CRYPTO) + same side → counted; source plays no role.
    assert _same_side_in_domain("BTCUSDT", "Buy", open_pos) == 2


# ─── (e) execution-path robustness ───────────────────────────────
def test_empty_position_list_allows():
    _types()
    assert _same_side_in_domain("BTCUSDT", "Buy", []) == 0


def test_import_error_does_not_crash_execution(exec_path, monkeypatch):
    _types("NFLXUSDT")
    class _Raiser:
        @property
        def get_open_positions_with_side(self):
            raise ImportError("simulated bybit import failure")

    monkeypatch.setitem(sys.modules, "tradingos.strategies.bybit_position_check", _Raiser())
    result = asyncio.run(_execute_reality(_proposal("BTCUSDT", "BUY")))
    # Correlation skipped (existing except ImportError) — path proceeds past it
    assert result["status"] == "ERROR"
    assert result["error"] == "Invalid SL (zero risk distance)"


def test_universe_classify_failure_falls_back_safely(monkeypatch):
    _types("NFLXUSDT")
    class _Raiser:
        @property
        def classify_instrument(self):
            raise ImportError("simulated universe import failure")

    monkeypatch.setitem(sys.modules, "tradingos.data.reality_universe", _Raiser())
    assert market_domain("BTCUSDT") == "OTHER"
    # No crash on the full counting path (both sides fall back to OTHER)
    assert _same_side_in_domain(
        "BTCUSDT", "Buy", [{"symbol": "NFLXUSDT", "side": "Buy", "size": 1.0}]) == 1


# ─── API / classifier failure never crashes execution, never → CRYPTO ──
def test_api_error_does_not_crash_execution(exec_path, monkeypatch):
    _types("NFLXUSDT")

    def _boom(symbol):
        # exchange fetch for the open-position symbol fails at the HTTP layer
        if symbol == "NFLXUSDT":
            raise RuntimeError("simulated network failure")
        return _FAKED_TYPES.get(symbol)

    monkeypatch.setattr(te, "_symbol_type", _boom)
    _set_open_positions(monkeypatch, [{"symbol": "NFLXUSDT", "side": "Buy", "size": 1.0}])
    result = asyncio.run(_execute_reality(_proposal("BTCUSDT", "BUY")))
    # Classification exception on the open position is swallowed (→ OTHER), so
    # the TRADFI position does NOT consume the CRYPTO budget and execution
    # proceeds through correlation to the SL guard without crashing.
    assert result["status"] == "ERROR"
    assert result["error"] == "Invalid SL (zero risk distance)"


def test_failed_fetch_returns_other_not_crypto():
    # Fetch returns None (API error) for a NEW symbol not in cache → OTHER.
    assert market_domain("NVDAUSDT") == "OTHER"


# ─── symbolType cache semantics (real implementation, mocked httpx) ──
def test_symbol_type_caches_successful_response(monkeypatch):
    calls = []

    class _Resp:
        def json(self):
            return {"retCode": 0, "result": {"list": [{"symbolType": "stock"}]}}

    def fake_get(url, params=None, **kw):
        calls.append(params.get("symbol"))
        return _Resp()

    monkeypatch.setattr(te, "_symbol_type", _ORIG_SYMBOL_TYPE)
    monkeypatch.setattr("httpx.get", fake_get)
    te._SYMBOL_TYPES.clear()
    assert te._symbol_type("NFLXUSDT") == "stock"
    assert te._symbol_type("NFLXUSDT") == "stock"
    assert calls == ["NFLXUSDT"]  # exactly one request per symbol


def test_symbol_type_error_not_cached_and_returns_none(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(1)
        raise RuntimeError("network down")

    monkeypatch.setattr(te, "_symbol_type", _ORIG_SYMBOL_TYPE)
    monkeypatch.setattr("httpx.get", fake_get)
    te._SYMBOL_TYPES.clear()
    assert te._symbol_type("BTCUSDT") is None
    assert "BTCUSDT" not in te._SYMBOL_TYPES  # NOT cached as crypto on failure
    assert te._symbol_type("BTCUSDT") is None  # re-requested, still None
    assert len(calls) == 2


def test_symbol_type_unknown_not_cached_as_crypto(monkeypatch):
    class _Empty:
        def json(self):
            return {"retCode": 0, "result": {"list": []}}

    monkeypatch.setattr(te, "_symbol_type", _ORIG_SYMBOL_TYPE)
    monkeypatch.setattr("httpx.get", lambda **kw: _Empty())
    te._SYMBOL_TYPES.clear()
    assert te._symbol_type("UNKNOWNXX") is None
    # cached as definitively-unknown (None), never as a crypto default
    assert te._SYMBOL_TYPES.get("UNKNOWNXX") is None