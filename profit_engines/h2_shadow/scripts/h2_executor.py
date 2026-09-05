#!/usr/bin/env python3
"""h2_executor.py — H2 shadow order lifecycle.

On a confirmed H2 signal: fail-closed kill-switch (fresh state.json read at 4 levels,
final fail-safe immediately before the API call) -> frozen notional -> POST-ONLY limit at
the crossed boundary -> poll -> fill/cancel/reject -> exit at next M15 boundary via
market reduce-only close. NO market/taker ENTRY ever.

Frozen rules enforced here (do not change):
  risk_per_trade = $5 (0.5% of $1,000 model), notional = $5 / (range_bps/10000),
  HARD MAX_NOTIONAL = $150 cap, max 1 concurrent position, fill window = 5 M15 bars.
"""
import os, sys, time, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from h2_lib import py, range_bps, jsonl_append
from h2_logger import (is_paused, pause_state, new_attempt_id, ledger, log_signal,
                       read_runtime, write_runtime, clear_runtime, now_ms, now_iso,
                       ROOT)
from bybit_client import BybitClient, BybitError

BAR_MS = 15 * 60 * 1000
SIDE_TO_BYBIT = {"BUY": "Buy", "SELL": "Sell"}


def load_config():
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    return cfg


class H2Executor:
    def __init__(self, cfg=None, client=None, dry_run=False, bar_ms=BAR_MS):
        self.cfg = cfg or load_config()
        self.risk = self.cfg["risk"]
        self.exec_cfg = self.cfg["execution"]
        self.dry_run = dry_run
        self.bar_ms = bar_ms
        self.client = client if client is not None else BybitClient()
        self._leverage_set = set()

    # ---------------- crash recovery ----------------
    def reconcile(self):
        """Startup safety: resume/finish any in-flight order or open position from the
        runtime journal after a crash/restart. Never silently leaves exposure live."""
        rt = read_runtime()
        actions = []
        if rt.get("in_flight") and rt.get("order_id"):
            sym, oid = rt["sym"], rt["order_id"]
            try:
                o = self.client.get_order(sym, oid)
            except BybitError:
                o = None
            if o:
                st = o.get("orderStatus", "")
                cum = float(o.get("cumExecQty", 0) or 0)
                if st == "Filled" or cum > 0:
                    ledger({"evt": "reconciled_fill", "attempt_id": rt["attempt_id"],
                            "sym": sym, "order_id": oid, "cum_qty": cum,
                            "avg_px": o.get("avgPrice")})
                    rt["position_open"] = True
                    rt["in_flight"] = False
                    rt["position_qty"] = cum
                    rt["fill_px"] = float(o.get("avgPrice", 0) or 0)
                else:
                    try:
                        self.client.cancel_order(sym, oid)
                        ledger({"evt": "reconciled_cancel", "attempt_id": rt["attempt_id"],
                                "sym": sym, "order_id": oid})
                    except BybitError as e:
                        ledger({"evt": "reconciled_cancel_error", "sym": sym,
                                "ret_code": e.ret_code, "ret_msg": str(e.ret_msg)})
                    rt = {}
        if rt.get("position_open") and rt.get("sym") and rt.get("position_qty"):
            sym = rt["sym"]
            qty = float(rt["position_qty"])
            try:
                pos = self.client.positions(sym)
            except BybitError:
                pos = []
            close_qty = pos[0]["size"] if pos else qty
            pos_side = rt.get("side")
            if not pos_side and pos:
                pos_side = "BUY" if pos[0]["side"] == "Buy" else "SELL"
            side = "Sell" if pos_side == "BUY" else "Buy"
            ledger({"evt": "reconciled_close", "attempt_id": rt["attempt_id"],
                    "sym": sym, "qty": close_qty, "side": side})
            try:
                res = self.client.create_order(symbol=sym, side=side,
                                               order_type="Market", qty=close_qty,
                                               reduce_only=True,
                                               client_order_id=f"H2R-{rt['attempt_id'][-10:]}")
                ledger({"evt": "reconciled_close_result", "sym": sym,
                        "avg_px": res.get("avgPrice"), "order_status": res.get("orderStatus")})
            except BybitError as e:
                ledger({"evt": "reconciled_close_error", "sym": sym,
                        "ret_code": e.ret_code, "ret_msg": str(e.ret_msg)})
        write_runtime({})
        # orphan protection: an unknown position on a universe symbol with no journal
        # record means we do NOT auto-close (could be another engine) -> pause + alert.
        if not rt:
            try:
                pos_all = self.client.positions()
            except BybitError:
                pos_all = []
            if pos_all:
                hit = [p["symbol"] for p in pos_all if p["symbol"] in self.cfg["universe"]]
                if hit:
                    ledger({"evt": "orphan_position_alert", "symbols": hit})
                    set_paused(True, "orphan_position_alert_auto_pause")
        return actions

    # ---------------- kill-switch (4 levels, fresh read each) ----------------
    def _pause_check(self, level):
        """Fresh state.json read. Returns (ok, reason). level=4 is the final fail-safe."""
        st = pause_state()
        if st.get("paused"):
            return False, st.get("reason", f"paused_level{level}")
        return True, ""

    def _log_no_trade(self, attempt_id, sig, reason):
        ledger({
            "evt": "no_trade", "attempt_id": attempt_id,
            "sym": sig.get("sym"), "signal_ts": sig.get("event_ts"),
            "side": sig.get("side"), "reason": reason,
        })
        ledger({
            "evt": "outcome", "attempt_id": attempt_id, "sym": sig.get("sym"),
            "signal_ts": sig.get("event_ts"), "side": sig.get("side"),
            "outcome": "NO_TRADE", "reason": reason,
        })

    # ---------------- sizing ----------------
    def _sizing(self, sig):
        """Frozen notional: $5 / (range_bps/1e4), capped at MAX_NOTIONAL."""
        limit = float(sig["entry"])
        rb = range_bps(sig, limit)
        if not rb or rb <= 0 or not math.isfinite(rb):
            return None, "range_bps_invalid"
        raw = self.risk["risk_per_trade_usd"] / (rb / 10000.0)
        capped = raw > self.risk["max_notional_usd"]
        notional = min(raw, self.risk["max_notional_usd"])
        return {
            "range_bps": rb,
            "notional_raw": raw,
            "notional": notional,
            "notional_capped": capped,
            "limit_px": limit,
        }, None

    # ---------------- lifecycle ----------------
    def process_signal(self, sig, dry_run=None):
        dry_run = self.dry_run if dry_run is None else dry_run
        attempt_id = new_attempt_id(sig)
        cfg = self.cfg

        # L1 — before any computation / API
        ok, why = self._pause_check(1)
        if not ok:
            self._log_no_trade(attempt_id, sig, f"PAUSED_L1:{why}")
            return {"attempt_id": attempt_id, "outcome": "NO_TRADE", "reason": f"paused:{why}"}

        sz, why = self._sizing(sig)
        if sz is None:
            self._log_no_trade(attempt_id, sig, why)
            return {"attempt_id": attempt_id, "outcome": "NO_TRADE", "reason": why}
        sz["risk_usd"] = self.risk["risk_per_trade_usd"]
        sz["max_notional_usd"] = self.risk["max_notional_usd"]

        # market snapshot for the ledger row
        tk = None
        try:
            tk = self.client.ticker(sig["sym"])
        except BybitError:
            tk = None
        mid_px = tk["mid"] if tk and tk.get("mid") else sig.get("c_trig")
        spread_bps = None
        if tk and tk.get("bid") and tk.get("ask"):
            m = (tk["bid"] + tk["ask"]) / 2
            if m > 0:
                spread_bps = (tk["ask"] - tk["bid"]) / m * 1e4

        attempt = {
            "evt": "attempt", "attempt_id": attempt_id,
            "signal_ts": int(sig["event_ts"]), "sym": sig["sym"],
            "side": sig["side"], "dir": sig["dir"],
            "range_bps": sz["range_bps"],
            "range_hi": sig.get("range_hi"), "range_lo": sig.get("range_lo"),
            "limit_px": sz["limit_px"], "mid_px": mid_px, "spread_bps": spread_bps,
            "notional": sz["notional"], "notional_raw": sz["notional_raw"],
            "notional_capped": sz["notional_capped"], "risk_usd": sz["risk_usd"],
            "bar_i": sig.get("bar_i"), "c_trig": sig.get("c_trig"),
            "vratio": sig.get("vratio"), "atr_pctl": sig.get("atr_pctl"),
            "bbw_pctl": sig.get("bbw_pctl"), "rcomp": sig.get("rcomp"),
        }
        ledger(attempt)

        # L2 — before order computation
        ok, why = self._pause_check(2)
        if not ok:
            self._log_no_trade(attempt_id, sig, f"PAUSED_L2:{why}")
            return {"attempt_id": attempt_id, "outcome": "NO_TRADE", "reason": f"paused:{why}"}

        pre = self._pretrade(sig, sz, attempt_id)
        if not pre["ok"]:
            self._log_no_trade(attempt_id, sig, pre["reason"])
            return {"attempt_id": attempt_id, "outcome": "NO_TRADE", "reason": pre["reason"]}

        # L3 — before placement decision
        ok, why = self._pause_check(3)
        if not ok:
            self._log_no_trade(attempt_id, sig, f"PAUSED_L3:{why}")
            return {"attempt_id": attempt_id, "outcome": "NO_TRADE", "reason": f"paused:{why}"}

        if dry_run:
            ledger({"evt": "would_place", "attempt_id": attempt_id,
                    "sym": sig["sym"], "side": sig["side"],
                    "qty": pre["qty"], "limit_px": sz["limit_px"],
                    "notional": sz["notional"], "post_only": True,
                    "note": "DRY-RUN: would place PostOnly limit, no API call"})
            return {"attempt_id": attempt_id, "outcome": "DRY_RUN",
                    "reason": "would_place", "qty": pre["qty"],
                    "limit_px": sz["limit_px"], "notional": sz["notional"]}

        return self._place_and_watch(sig, attempt_id, pre, sz)

    # ---------------- pre-trade gates ----------------
    def _pretrade(self, sig, sz, attempt_id):
        sym = sig["sym"]
        # 1) lot filter
        try:
            inst = self.client.instrument(sym)
        except BybitError as e:
            return {"ok": False, "reason": f"instrument_api:{e.ret_code}:{e.ret_msg}"}
        if not inst or inst.get("status") != "Trading":
            return {"ok": False, "reason": "instrument_not_trading"}
        step = inst.get("qty_step") or 1e-8
        qty = math.floor(sz["notional"] / sz["limit_px"] / step) * step
        qty = round(qty, 10)
        if qty < (inst.get("min_qty") or 0):
            return {"ok": False, "reason": f"below_min_qty:{qty}<{inst.get('min_qty')}"}
        if qty * sz["limit_px"] < (inst.get("min_notional") or 0):
            return {"ok": False, "reason": f"below_min_notional:{qty*sz['limit_px']}<{inst.get('min_notional')}"}
        # 2) margin / equity
        try:
            w = self.client.wallet_usdt()
        except BybitError as e:
            return {"ok": False, "reason": f"wallet_api:{e.ret_code}:{e.ret_msg}"}
        if not w or w.get("equity", 0) <= 0:
            return {"ok": False, "reason": "wallet_unavailable"}
        margin_needed = qty * sz["limit_px"] / self.risk["leverage"] * self.risk["margin_buffer_x"]
        if w["equity"] < margin_needed:
            return {"ok": False,
                    "reason": f"insufficient_margin:{w['equity']:.2f}<{margin_needed:.2f}"}
        # 3) concurrency: max 1 live order OR 1 open position
        rt = read_runtime()
        if rt.get("in_flight") or rt.get("position_open"):
            return {"ok": False, "reason": "concurrency_runtime_busy"}
        try:
            pos = self.client.positions(sym)
        except BybitError:
            pos = []
        if pos:
            return {"ok": False, "reason": f"conflict_position_on_symbol:{sym}"}
        try:
            oo = self.client.open_orders(sym)
        except BybitError:
            oo = []
        if oo:
            return {"ok": False, "reason": f"conflict_open_orders_on_symbol:{sym}"}
        # 4) leverage (set once per symbol)
        if sym not in self._leverage_set:
            if not self.client.set_leverage(sym, self.risk["leverage"]):
                return {"ok": False, "reason": f"leverage_set_failed:{sym}@{self.risk['leverage']}x"}
            self._leverage_set.add(sym)
        return {"ok": True, "qty": qty, "min_qty": inst.get("min_qty"),
                "qty_step": step, "tick": inst.get("tick_size"),
                "equity": w["equity"], "margin_needed": margin_needed}

    # ---------------- place + watch + exit ----------------
    def _place_and_watch(self, sig, attempt_id, pre, sz):
        sym = sig["sym"]
        # F2 hardening: SIDE_TO_BYBIT.get + явный reject — неизвестный side = NO TRADE,
        # никогда не дефолтим в Sell (внешний --signal-json может принести что угодно).
        bybit_side_full = SIDE_TO_BYBIT.get(sig.get("side"))
        if bybit_side_full is None:
            return {"ok": False, "outcome": "NO_TRADE", "reason": f"invalid_side:{sig.get('side')!r}"}
        limit = sz["limit_px"]
        # round limit to tick (tick protects PostOnly crossing only marginally)
        tick = pre.get("tick") or 0
        if tick and tick > 0:
            limit = round(limit / tick) * tick
        qty = pre["qty"]

        # L4 — FINAL fail-safe, immediately before the API call
        ok, why = self._pause_check(4)
        if not ok:
            self._log_no_trade(attempt_id, sig, f"PAUSED_L4:{why}")
            return {"attempt_id": attempt_id, "outcome": "NO_TRADE", "reason": f"paused_l4:{why}"}

        try:
            res = self.client.create_order(
                symbol=sym, side=bybit_side_full, order_type="Limit", qty=qty,
                price=limit, post_only=True, client_order_id=f"H2-{sym}-{attempt_id[-10:]}",
            )
        except BybitError as e:
            reason = f"post_only_reject:retCode={e.ret_code}:{e.ret_msg}"
            if "postonly" in str(e.ret_msg).lower() or "post only" in str(e.ret_msg).lower():
                reason = f"POST_ONLY_CROSS_REJECT:retCode={e.ret_code}:{e.ret_msg}"
            ledger({"evt": "rejected", "attempt_id": attempt_id, "sym": sym,
                    "reject_ts": now_ms(), "ret_code": e.ret_code,
                    "ret_msg": str(e.ret_msg), "reject_reason": reason})
            ledger({"evt": "outcome", "attempt_id": attempt_id, "sym": sym,
                    "signal_ts": sig["event_ts"], "side": sig["side"],
                    "outcome": "REJECTED", "reject_reason": reason})
            return {"attempt_id": attempt_id, "outcome": "REJECTED", "reason": reason}

        order_id = res.get("orderId", "")
        order_status = res.get("orderStatus", "Unknown")
        ledger({"evt": "placed", "attempt_id": attempt_id, "sym": sym, "side": sig["side"],
                "order_id": order_id, "placed_ts": now_ms(),
                "order_status": order_status, "qty": qty, "limit_px": limit,
                "ret": py(res)})
        write_runtime({
            "in_flight": True, "position_open": False, "attempt_id": attempt_id,
            "sym": sym, "side": sig["side"], "order_id": order_id, "qty": qty,
            "limit_px": limit, "placed_ms": now_ms(),
            "deadline_ms": int(sig["event_ts"]) + self.exec_cfg["fill_window_bars"] * self.bar_ms,
            "fill_px": None, "position_qty": None, "phase": "waiting_fill",
        })

        return self._watch(sig, attempt_id, sym, order_id, qty, limit)

    def _watch(self, sig, attempt_id, sym, order_id, qty, limit):
        deadline = int(sig["event_ts"]) + self.exec_cfg["fill_window_bars"] * self.bar_ms
        cum_qty = 0.0
        while True:
            if now_ms() >= deadline:
                break
            try:
                o = self.client.get_order(sym, order_id)
            except BybitError as e:
                ledger({"evt": "poll_error", "attempt_id": attempt_id, "sym": sym,
                        "ret_code": e.ret_code, "ret_msg": str(e.ret_msg)})
                time.sleep(self.exec_cfg["poll_sec"])
                continue
            if not o:
                time.sleep(self.exec_cfg["poll_sec"])
                continue
            st = o.get("orderStatus", "")
            cum = float(o.get("cumExecQty", 0) or 0)
            if st == "Filled":
                return self._on_fill(sig, attempt_id, sym, o, order_id, qty)
            if st in ("Cancelled", "Rejected", "PartiallyFilledCanceled", "Untriggered"):
                ledger({"evt": "unexpected_terminal", "attempt_id": attempt_id, "sym": sym,
                        "order_status": st, "ret": py(o)})
                if cum > 0:
                    return self._on_fill(sig, attempt_id, sym, o, order_id, qty, partial=True)
                return self._finish_no_position(attempt_id, sig, "EXPIRED/CANCELLED",
                                                f"terminal:{st}")
            # PartiallyFilled keeps resting until the deadline (frozen fill window)
            time.sleep(self.exec_cfg["poll_sec"])

        # deadline reached without fill -> CANCEL
        try:
            self.client.cancel_order(sym, order_id)
            cancelled = True
        except BybitError as e:
            cancelled = False
            ledger({"evt": "cancel_error", "attempt_id": attempt_id, "sym": sym,
                    "ret_code": e.ret_code, "ret_msg": str(e.ret_msg)})
        try:
            o = self.client.get_order(sym, order_id)
        except BybitError:
            o = None
        cum = float((o or {}).get("cumExecQty", 0) or 0)
        st = (o or {}).get("orderStatus", "?")
        ledger({"evt": "cancelled", "attempt_id": attempt_id, "sym": sym,
                "cancelled_ts": now_ms(), "cancel_ok": cancelled,
                "final_status": st, "cum_exec_qty": cum})
        if cum > 0:
            # partial position still open -> exit it
            return self._on_fill(sig, attempt_id, sym, o, order_id, qty, partial=True)
        return self._finish_no_position(attempt_id, sig, "EXPIRED/CANCELLED", "deadline_no_fill")

    def _finish_no_position(self, attempt_id, sig, outcome, reason):
        ledger({"evt": "outcome", "attempt_id": attempt_id, "sym": sig["sym"],
                "signal_ts": sig["event_ts"], "side": sig["side"],
                "outcome": outcome, "reason": reason})
        write_runtime({})
        return {"attempt_id": attempt_id, "outcome": outcome, "reason": reason}

    def _on_fill(self, sig, attempt_id, sym, o, order_id, qty, partial=False):
        fill_px = float(o.get("avgPrice", 0) or o.get("price", 0) or sig["entry"])
        fill_qty = float(o.get("cumExecQty", 0) or 0)
        fee = float(o.get("cumExecFee", 0) or 0)
        if fill_qty <= 0:
            return self._finish_no_position(attempt_id, sig, "NO_TRADE", "fill_qty_zero")
        outcome = "PARTIAL" if (partial or fill_qty < qty - 1e-12) else "FILLED"
        ledger({"evt": "filled", "attempt_id": attempt_id, "sym": sym, "side": sig["side"],
                "order_id": order_id, "filled_ts": now_ms(), "fill_px": fill_px,
                "fill_qty": fill_qty, "partial_flag": outcome == "PARTIAL",
                "requested_qty": qty, "entry_fee": fee, "order_status": o.get("orderStatus")})
        write_runtime({
            "in_flight": False, "position_open": True, "attempt_id": attempt_id,
            "sym": sym, "side": sig["side"], "order_id": order_id,
            "fill_px": fill_px, "position_qty": fill_qty, "phase": "exit_pending",
            "filled_ms": now_ms(),
        })
        return self._post_fill(sig, attempt_id, sym, fill_px, fill_qty, outcome)

    def _post_fill(self, sig, attempt_id, sym, fill_px, fill_qty, outcome):
        """AS snapshots at +5/30/60/300s + exit at next M15 boundary + MFE/MAE."""
        filled_ms = now_ms()
        exit_at = math.ceil(filled_ms / self.bar_ms) * self.bar_ms
        mfe = mae = 0.0
        side_sgn = 1.0 if sig["side"] == "BUY" else -1.0
        poll_every = 15
        snapshots = {5000: None, 30000: None, 60000: None, 300000: None}
        while True:
            nowt = now_ms()
            # exit boundary reached -> stop sampling, close at the boundary
            if nowt >= exit_at:
                break
            # AS snapshots (only while holding)
            for off in list(snapshots):
                if snapshots[off] is None and nowt >= filled_ms + off:
                    m = self._mid(sym)
                    snapshots[off] = m
                    if m:
                        ledger({"evt": "as_snapshot", "attempt_id": attempt_id,
                                "sym": sym, "offset_s": off // 1000, "mid_px": m,
                                "snap_ts": nowt})
            # MFE/MAE
            m = self._mid(sym)
            if m:
                d = (m - fill_px) / fill_px * 1e4 * side_sgn
                mfe = max(mfe, d)
                mae = min(mae, d)
            time.sleep(poll_every)
        # exit: market reduce-only close at the next M15 boundary
        exit_px, exit_fee, exit_qty = self._market_close(sym, fill_qty, attempt_id)
        if exit_px is None:
            ledger({"evt": "exit_error", "attempt_id": attempt_id, "sym": sym})
            ledger({"evt": "outcome", "attempt_id": attempt_id, "sym": sym,
                    "signal_ts": sig["event_ts"], "side": sig["side"],
                    "outcome": "EXIT_ERROR", "fill_px": fill_px, "fill_qty": fill_qty})
            write_runtime({})
            return {"attempt_id": attempt_id, "outcome": "EXIT_ERROR", "sym": sym}
        pnl = (exit_px - fill_px) * fill_qty * side_sgn
        gross_bps = (exit_px / fill_px - 1) * 1e4 * side_sgn
        ledger({"evt": "exit", "attempt_id": attempt_id, "sym": sym,
                "exit_ts": now_ms(), "exit_px": exit_px, "exit_qty": exit_qty,
                "exit_fee": exit_fee, "realized_pnl_usd": pnl - entry_fee_of(attempt_id),
                "gross_bps": gross_bps, "mfe_bps": mfe, "mae_bps": mae,
                "hold_sec": (now_ms() - filled_ms) / 1000.0})
        ledger({"evt": "outcome", "attempt_id": attempt_id, "sym": sym,
                "signal_ts": sig["event_ts"], "side": sig["side"],
                "outcome": outcome, "fill_px": fill_px, "fill_qty": fill_qty,
                "exit_px": exit_px, "exit_ts": now_ms(),
                "realized_pnl_usd": pnl - entry_fee_of(attempt_id),
                "gross_bps": gross_bps, "mfe_bps": mfe, "mae_bps": mae,
                "as_5s": snapshots[5000], "as_30s": snapshots[30000],
                "as_60s": snapshots[60000], "as_5m": snapshots[300000]})
        write_runtime({})
        return {"attempt_id": attempt_id, "outcome": outcome, "sym": sym,
                "fill_px": fill_px, "exit_px": exit_px, "gross_bps": gross_bps}

    def _mid(self, sym):
        try:
            tk = self.client.ticker(sym)
            return tk["mid"] if tk else None
        except BybitError:
            return None

    def _market_close(self, sym, qty, attempt_id):
        """Real MARKET reduce-only close (allowed: the NO-TAKER rule covers ENTRY only)."""
        # L4 gate applies to every live-order path: fresh read before the API call
        st = pause_state()
        paused_at_exit = bool(st.get("paused"))
        try:
            pos = self.client.positions(sym)
        except BybitError:
            pos = []
        close_qty = qty
        if pos:
            close_qty = pos[0]["size"]
        # Close side = opposite of the position side (one-way mode).
        rt = read_runtime()
        pos_side = rt.get("side")
        if not pos_side and pos:
            pos_side = "BUY" if pos[0]["side"] == "Buy" else "SELL"
        side = "Sell" if pos_side == "BUY" else "Buy"
        try:
            res = self.client.create_order(symbol=sym, side=side, order_type="Market",
                                           qty=close_qty, reduce_only=True,
                                           client_order_id=f"H2X-{attempt_id[-10:]}")
            exit_px = float(res.get("avgPrice", 0) or 0)
            fee = float(res.get("cumExecFee", 0) or 0)
            ledger({"evt": "exit_order", "attempt_id": attempt_id, "sym": sym,
                    "side": side, "qty": close_qty, "exit_px": exit_px,
                    "exit_fee": fee, "paused_at_exit": paused_at_exit,
                    "order_status": res.get("orderStatus")})
            return exit_px, fee, close_qty
        except BybitError as e:
            ledger({"evt": "exit_reject", "attempt_id": attempt_id, "sym": sym,
                    "ret_code": e.ret_code, "ret_msg": str(e.ret_msg),
                    "paused_at_exit": paused_at_exit})
            return None, None, None


def entry_fee_of(attempt_id):
    """Entry fee recorded in the filled event (side table, no API)."""
    from h2_logger import load_ledger
    for r in reversed(load_ledger()):
        if r.get("evt") == "filled" and r.get("attempt_id") == attempt_id:
            return float(r.get("entry_fee", 0) or 0)
    return 0.0


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--signal-json", default="")
    a = ap.parse_args()
    ex = H2Executor(dry_run=a.dry_run)
    if a.signal_json:
        sig = json.load(open(a.signal_json))
        out = ex.process_signal(sig)
        print(json.dumps(py(out), indent=2))
        return
    print("no --signal-json given; use --dry-run with a signal file to test placement path")


if __name__ == "__main__":
    main()
