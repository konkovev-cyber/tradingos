#!/usr/bin/env python3
"""test_killswitch.py — OAT dry-run: the kill-switch must block every live-order path.

Proves:
  1) paused=true        -> NO_TRADE at L1, ZERO API calls
  2) corrupt state.json -> fail-closed NO_TRADE, zero API calls
  3) missing state.json -> fail-closed NO_TRADE, zero API calls
  4) resume (paused=false) + dry-run -> placement path reachable ('would place'),
     create_order NEVER called; 0 API create calls in every state
  5) LIVE lifecycle simulation with fake client: place -> fill -> exit -> outcome FILLED,
     complete end-to-end ledger trail (placed/filled/exit/outcome).
"""
import os, sys, json, time, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import h2_logger
from h2_executor import H2Executor
from h2_logger import is_paused, set_paused, pause_state

ROOT = h2_logger.ROOT
STATE = h2_logger.STATE


class FakeClient:
    """Deterministic stub. create_order/order paths never hit the network."""
    def __init__(self):
        self.calls = {}
        self.fill_on_get = True

    def _c(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1

    def ticker(self, sym):
        self._c("ticker")
        return {"bid": 100.0, "ask": 100.02, "mid": 100.01, "last": 100.01}

    def instrument(self, sym):
        self._c("instrument")
        return {"symbol": sym, "status": "Trading", "min_qty": 0.001, "qty_step": 0.001,
                "min_notional": 5.0, "tick_size": 0.01}

    def wallet_usdt(self):
        self._c("wallet_usdt")
        return {"equity": 500.0, "wallet": 500.0, "available": 450.0}

    def positions(self, symbol=None):
        self._c("positions")
        return []

    def open_orders(self, symbol=None, order_id=None):
        self._c("open_orders")
        return []

    def set_leverage(self, symbol, leverage):
        self._c("set_leverage")
        return True

    def create_order(self, **kw):
        self._c("create_order")
        self._last_qty = kw.get("qty", 0.1)
        return {"orderId": "FAKE-ORDER-1", "orderStatus": "New", "avgPrice": 100.0,
                "cumExecQty": self._last_qty, "cumExecFee": 0.0042}

    def get_order(self, symbol, order_id):
        self._c("get_order")
        return {"orderId": order_id, "orderStatus": "Filled", "avgPrice": 100.0,
                "cumExecQty": getattr(self, "_last_qty", 0.1), "cumExecFee": 0.0042}

    def cancel_order(self, symbol, order_id):
        self._c("cancel_order")
        return {"orderId": order_id, "orderStatus": "Cancelled"}


def sig():
    now = int(time.time() * 1000)
    return {"sym": "BTCUSDT", "event_ts": now - 2000, "dir": 1, "side": "BUY",
            "entry": 100.0, "range_hi": 100.0, "range_lo": 99.0, "c_trig": 100.5,
            "bar_i": 1000, "vratio": 3.5, "atr_pctl": 0.02, "bbw_pctl": 0.04,
            "rcomp": 0.5}


def run():
    # backup real state + ledger, use a temp ledger
    state_backup = open(STATE).read()
    ledger_tmp = os.path.join(ROOT, ".oat_test_ledger.jsonl")
    h2_logger.LEDGER = ledger_tmp
    for f in (ledger_tmp,):
        if os.path.exists(f):
            os.remove(f)
    results = {}

    try:
        # ---- 1) paused=true: NO_TRADE at L1, zero API calls ----
        set_paused(True, reason="oat test")
        fc = FakeClient()
        ex = H2Executor(client=fc, dry_run=True)
        r = ex.process_signal(sig())
        results["paused_blocks"] = {
            "outcome": r.get("outcome"), "expected": "NO_TRADE",
            "reason": r.get("reason"),
            "api_calls": dict(fc.calls),
            "pass": r.get("outcome") == "NO_TRADE" and not fc.calls,
        }

        # ---- 2) corrupt state.json: fail-closed ----
        with open(STATE, "w") as f:
            f.write("{not json!!!")
        fc = FakeClient()
        ex = H2Executor(client=fc, dry_run=True)
        r = ex.process_signal(sig())
        results["corrupt_fail_closed"] = {
            "outcome": r.get("outcome"), "reason": r.get("reason"),
            "api_calls": dict(fc.calls),
            "pass": r.get("outcome") == "NO_TRADE" and not fc.calls,
        }

        # ---- 3) missing state.json: fail-closed ----
        os.remove(STATE)
        fc = FakeClient()
        ex = H2Executor(client=fc, dry_run=True)
        r = ex.process_signal(sig())
        results["missing_fail_closed"] = {
            "outcome": r.get("outcome"), "reason": r.get("reason"),
            "api_calls": dict(fc.calls),
            "pass": r.get("outcome") == "NO_TRADE" and not fc.calls,
        }

        # ---- 4) resume + dry-run: placement path reachable, create_order NEVER called ----
        set_paused(False, reason="oat test resume")
        fc = FakeClient()
        ex = H2Executor(client=fc, dry_run=True)
        r = ex.process_signal(sig())
        results["resume_reachable"] = {
            "outcome": r.get("outcome"), "reason": r.get("reason"),
            "expected": "DRY_RUN", "limit_px": r.get("limit_px"),
            "notional": r.get("notional"),
            "api_calls": dict(fc.calls),
            "create_order_calls": fc.calls.get("create_order", 0),
            "pass": r.get("outcome") == "DRY_RUN" and fc.calls.get("create_order", 0) == 0,
        }

        # ---- 5) LIVE lifecycle simulation (fake client): place->fill->exit->FILLED ----
        fc = FakeClient()
        ex = H2Executor(client=fc, dry_run=False, bar_ms=500)
        r = ex.process_signal(sig())
        results["live_lifecycle_sim"] = {
            "outcome": r.get("outcome"),
            "create_order_calls": fc.calls.get("create_order", 0),
            "get_order_calls": fc.calls.get("get_order", 0),
            "expected": "FILLED",
            "pass": r.get("outcome") == "FILLED",
        }

        # ---- ledger trail for the lifecycle attempt ----
        trail = [x for x in h2_logger.load_ledger()
                 if x.get("attempt_id") == r.get("attempt_id")]
        evts = [x.get("evt") for x in trail]
        results["lifecycle_ledger_trail"] = {
            "events": evts,
            "pass": all(k in evts for k in ("attempt", "placed", "filled", "exit", "outcome")),
        }

        all_pass = all(v.get("pass") for v in results.values() if isinstance(v, dict))
        results["all_pass"] = all_pass
    finally:
        # restore real state + ledger path
        with open(STATE, "w") as f:
            f.write(state_backup)
        h2_logger.LEDGER = os.path.join(ROOT, "pilot_ledger.jsonl")
        if os.path.exists(ledger_tmp):
            os.remove(ledger_tmp)

    print(json.dumps(results, indent=2))
    return all_pass


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
