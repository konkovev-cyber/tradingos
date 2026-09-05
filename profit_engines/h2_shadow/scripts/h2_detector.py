#!/usr/bin/env python3
"""h2_detector.py — real-time M15 scanner for the H2 squeeze->expansion mechanism.

Modes:
  --watch   service loop: warm-up history, then poll every ~60s the live M15 tail,
            detect on freshly-closed bars, log every candidate to signals.jsonl and
            hand confirmed signals to the executor (fail-closed, PostOnly only).
  --once    single scan over the current closed bars (diagnostics / OAT).
  --replay  replay-validation on the frozen 90d cache vs the replication's event count.

Anti-lookahead: detection runs only on fully-closed bars; all features use data
<= close of the decision bar i-1; the trigger uses closed bar i high/low.
Per-symbol API errors never kill the loop.
"""
import os, sys, json, time, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

from h2_lib import FROZEN_CFG, detect_events, py
from h2_logger import ROOT, log_signal, ledger, is_paused, now_ms
from bybit_client import BybitClient, BybitError

BAR_MS = 15 * 60 * 1000
PROCESSED_FILE = os.path.join(ROOT, "processed_events.json")
REFERENCE_EVENT_COUNT = 474  # h2rep_squeeze.detect_events frozen CFG, 97 liquid syms, 90d window


def load_cfg():
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    return cfg


def load_processed():
    try:
        st = json.load(open(PROCESSED_FILE))
        return set(st.get("fired", []))
    except Exception:
        return set()


def save_processed(fired):
    tmp = PROCESSED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"fired": sorted(fired)}, f)
    os.replace(tmp, PROCESSED_FILE)


class H2Detector:
    def __init__(self, cfg=None, client=None):
        self.cfg = cfg or load_cfg()
        self.det_cfg = self.cfg["detector"]
        self.universe = list(self.cfg["universe"])
        self.client = client or BybitClient()
        self.series = {}          # sym -> DataFrame[ts,o,h,l,c,v] (ms ts, sorted, deduped)
        self.last_bar_ts = {}     # sym -> last processed bar open ts (ms)
        self.fired = load_processed()
        self.active = []          # verified live symbols

    # ---------------- data ----------------
    def fetch_history(self, sym, nbars):
        """Fetch ~nbars of M15 history (paginated, newest last)."""
        rows = {}
        end = now_ms()
        got = 0
        while got < nbars:
            want = min(self.det_cfg["kline_req_limit"], nbars - got)
            chunk = self.client.kline(sym, interval=self.det_cfg["interval"],
                                      limit=want, end=end)
            if not chunk:
                break
            for b in chunk:
                rows[b["ts"]] = b
            got += len(chunk)
            end = chunk[0]["ts"] - 1
            if len(chunk) < want:
                break
            time.sleep(0.12)
        return sorted(rows.values(), key=lambda x: x["ts"])

    def _df(self, bars):
        return pd.DataFrame(bars)[["ts", "o", "h", "l", "c", "v"]]

    def warmup(self):
        """Fetch history for each active symbol; drop symbols with insufficient data."""
        self.active = []
        for sym in self.universe:
            try:
                bars = self.fetch_history(sym, self.det_cfg["warmup_bars"])
            except BybitError as e:
                ledger({"evt": "warmup_error", "sym": sym, "ret_code": e.ret_code,
                        "ret_msg": str(e.ret_msg)})
                continue
            if len(bars) < 1400:
                ledger({"evt": "warmup_skip", "sym": sym, "reason": f"only_{len(bars)}_bars"})
                continue
            self.series[sym] = self._df(bars)
            self.active.append(sym)
            ledger({"evt": "warmup_symbol", "sym": sym, "bars": len(bars),
                    "n_active": len(self.active)})
            time.sleep(0.12)
        ledger({"evt": "warmup_done", "n_active": len(self.active),
                "symbols": self.active})

    def poll_tail(self):
        """Fetch the last poll_bars for each symbol and merge new CLOSED bars only."""
        closed_cut = now_ms() - BAR_MS
        for sym in self.active:
            try:
                bars = self.client.kline(sym, interval=self.det_cfg["interval"],
                                         limit=self.det_cfg["poll_bars"])
            except BybitError as e:
                ledger({"evt": "poll_error", "sym": sym, "ret_code": e.ret_code,
                        "ret_msg": str(e.ret_msg)})
                continue
            if not bars:
                continue
            new = [b for b in bars if b["ts"] > self.last_bar_ts.get(sym, 0)
                   and b["ts"] <= closed_cut]
            if not new:
                continue
            df = self.series[sym]
            merge = pd.concat([df, pd.DataFrame(new)[["ts", "o", "h", "l", "c", "v"]]])
            merge = merge.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
            # keep a rolling window of ~1700 bars to bound memory (>= atr_win+20 + margin)
            if len(merge) > 1700:
                merge = merge.tail(1700).reset_index(drop=True)
            self.series[sym] = merge
            self.last_bar_ts[sym] = new[-1]["ts"]
            time.sleep(0.08)

    def scan(self):
        """Detect on fully-closed bars; returns events never fired before and
        strictly AFTER the pinned scan frontier (no stale historical fires)."""
        out = []
        for sym in self.active:
            frontier = self.last_bar_ts.get(sym, 0)
            df = self.series[sym]
            try:
                evs = detect_events(sym, df, FROZEN_CFG)
            except Exception as e:
                ledger({"evt": "scan_error", "sym": sym, "err": str(e)})
                continue
            for e in evs:
                key = f"{sym}:{e['event_ts']}"
                if key in self.fired:
                    continue
                self.fired.add(key)
                if e["event_ts"] <= frontier:
                    continue  # historical warmup event: consumed, not fired
                out.append(e)
        if out:
            save_processed(self.fired)
        return out

    def run_once(self, handoff):
        self.warmup()
        if not self.active:
            print("no active symbols")
            return 0
        evs = self.scan()
        for e in sorted(evs, key=lambda x: x["event_ts"]):
            log_signal(e)
            if handoff:
                handoff(e)
        print(f"scan done: {len(evs)} new candidate signals")
        return len(evs)

    def run_watch(self, handoff):
        """Main service loop. handoff(sig) is called for every confirmed signal.

        Only bars CLOSED after service start are eligible as triggers: after warmup we
        pin last_bar_ts to the newest closed bar so historical warmup bars can never
        fire a (stale) signal.
        """
        self.warmup()
        if not self.active:
            ledger({"evt": "fatal", "reason": "no_active_symbols"})
            return
        # pin scan frontier to the newest closed bar at startup
        closed_cut = now_ms() - BAR_MS
        for sym in self.active:
            df = self.series[sym]
            tail = df[df["ts"] <= closed_cut]
            if len(tail):
                self.last_bar_ts[sym] = int(tail["ts"].iloc[-1])
            else:
                self.last_bar_ts[sym] = int(df["ts"].iloc[-1])
        ledger({"evt": "service_start", "n_active": len(self.active),
                "mode": "watch", "frontier_pinned": True})
        # crash recovery: finish any in-flight order / open position before scanning
        try:
            from h2_executor import H2Executor
            H2Executor().reconcile()
            ledger({"evt": "reconcile_done"})
        except Exception as e:
            ledger({"evt": "reconcile_error", "err": str(e)})
        while True:
            t0 = time.time()
            try:
                self.poll_tail()
                evs = self.scan()
            except Exception as e:
                ledger({"evt": "loop_error", "err": str(e)})
                time.sleep(self.det_cfg["poll_sec"])
                continue
            dt = time.time() - t0
            if evs:
                for e in sorted(evs, key=lambda x: x["event_ts"]):
                    log_signal(e)
                    if handoff:
                        try:
                            handoff(e)
                        except Exception as ex:
                            ledger({"evt": "handoff_error", "sym": e.get("sym"),
                                    "err": str(ex)})
            if len(evs) == 0:
                ledger({"evt": "poll_heartbeat", "cycle_ms": int(dt * 1000),
                        "n_active": len(self.active)})
            sleep = max(5.0, self.det_cfg["poll_sec"] - dt)
            time.sleep(sleep)

    # ---------------- replay ----------------
    def replay(self, cache_dir):
        """Replay frozen CFG over the 90d cache; compare to reference event count."""
        n = 0
        per_sym = {}
        for f in sorted(glob.glob(os.path.join(cache_dir, "linear_*_15.parquet"))):
            sym = os.path.basename(f).replace("linear_", "").replace("_15.parquet", "")
            df = pd.read_parquet(f)[["ts", "o", "h", "l", "c", "v"]]
            df["ts"] = df["ts"].astype("int64")
            liq = float((df["v"] * df["c"]).median())
            if liq < FROZEN_CFG["liq_thr"]:
                continue
            evs = detect_events(sym, df, FROZEN_CFG)
            n += len(evs)
            per_sym[sym] = len(evs)
        return n, per_sym


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="once", choices=["once", "watch", "replay"])
    ap.add_argument("--cache", default="/tmp/t2_fresh/lag_cache_fresh")
    a = ap.parse_args()

    # executor handoff (imported lazily to keep detector standalone for replay/tests)
    def make_handoff():
        from h2_executor import H2Executor
        ex = H2Executor()
        def handoff(sig):
            res = ex.process_signal(sig)
            ledger({"evt": "handoff_result", "attempt_id": res.get("attempt_id"),
                    "outcome": res.get("outcome"), "reason": res.get("reason")})
        return handoff

    if a.mode == "replay":
        det = H2Detector()
        n, per_sym = det.replay(a.cache)
        print(json.dumps({
            "replay_event_count": n,
            "reference_event_count": REFERENCE_EVENT_COUNT,
            "match": n == REFERENCE_EVENT_COUNT,
            "top_syms": sorted(per_sym.items(), key=lambda x: -x[1])[:10],
        }, indent=2))
        return

    det = H2Detector()
    handoff = make_handoff()
    if a.mode == "once":
        det.run_once(handoff)
    else:
        det.run_watch(handoff)


if __name__ == "__main__":
    main()
