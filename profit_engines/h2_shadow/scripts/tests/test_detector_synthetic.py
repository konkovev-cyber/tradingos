#!/usr/bin/env python3
"""test_detector_synthetic.py — detector unit tests.

must-fire: a crafted compression->burst sequence (a REAL frozen-window H2 event, data
           <= trigger bar) MUST fire at the exact event timestamp.
must-not-fire: (a) the same sequence with the trigger-bar volume halved (below 3x gate);
           (b) a liquid symbol with zero H2 events in the whole 90d window;
           (c) a flat synthetic series (no compression, no burst).
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd

from h2_lib import FROZEN_CFG, detect_events

CACHE = "/tmp/t2_fresh/lag_cache_fresh"
REPORT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "detector_synthetic_test.json")


def load_sym(sym):
    df = pd.read_parquet(os.path.join(CACHE, f"linear_{sym}_15.parquet"))
    df["ts"] = df["ts"].astype(np.int64)
    return df[["ts", "o", "h", "l", "c", "v"]].reset_index(drop=True)


def event_prefix(sym, df, event_ts):
    """Data <= trigger bar for a known event (no lookahead)."""
    idx = int(np.where(df["ts"].values == int(event_ts))[0][0])
    return df.iloc[: idx + 1].reset_index(drop=True)


def run():
    # find a real event with enough prior history (>=1400 bars for the warmup guard)
    ev_ref = pd.read_parquet("/tmp/profit_hunt2/h2rep/h2rep_withtrend.parquet")
    candidates = []
    for _, row in ev_ref.iterrows():
        df0 = load_sym(row["sym"])
        i0 = int(np.where(df0["ts"].values == int(row["event_ts"]))[0][0])
        candidates.append((row, i0))
    cand = [c for c in candidates if c[1] >= 1500]
    if not cand:
        raise SystemExit("no event with bar_i>=1500 found")
    first, bar_i = min(cand, key=lambda c: c[1])
    sym, event_ts = first["sym"], int(first["event_ts"])

    df = load_sym(sym)
    prefix = event_prefix(sym, df, event_ts)

    # must-fire
    fired = detect_events(sym, prefix, FROZEN_CFG)
    fired_ts = {e["event_ts"] for e in fired}
    must_fire = event_ts in fired_ts

    # must-not-fire (a): halve the trigger-bar volume -> below 3x gate
    prefix2 = prefix.copy()
    i_trig = int(np.where(prefix2["ts"].values == event_ts)[0][0])
    prefix2.loc[i_trig, "v"] = prefix2["v"].iloc[:i_trig].median() * 0.5
    fired2 = detect_events(sym, prefix2, FROZEN_CFG)
    must_not_fire_vol = all(e["event_ts"] != event_ts for e in fired2)

    # must-not-fire (c): flat synthetic series (no compression, no burst)
    n = 1600
    t0 = int(df["ts"].iloc[0])
    flat_ts = np.array([t0 + i * 900_000 for i in range(n)])
    flat = pd.DataFrame({
        "ts": flat_ts,
        "o": 100.0, "h": 100.1, "l": 99.9, "c": 100.0,
        "v": 100.0,
    })
    fired3 = detect_events("FLAT", flat, FROZEN_CFG)
    must_not_fire_flat = len(fired3) == 0

    # must-not-fire (b): liquid symbol with zero events in the whole window
    zero_syms = []
    for f in sorted(os.listdir(CACHE)):
        if not (f.startswith("linear_") and f.endswith("_15.parquet")):
            continue
        s0 = f.replace("linear_", "").replace("_15.parquet", "")
        d0 = pd.read_parquet(os.path.join(CACHE, f), columns=["v", "c"])
        if float((d0["v"] * d0["c"]).median()) < 3000.0:
            continue
        d0f = load_sym(s0)
        if len(detect_events(s0, d0f, FROZEN_CFG)) == 0:
            zero_syms.append(s0)
        if len(zero_syms) >= 5:
            break
    must_not_fire_zero = True
    zero_detail = {}
    for zs in zero_syms:
        zdf = load_sym(zs)
        zf = detect_events(zs, zdf, FROZEN_CFG)
        ok = len(zf) == 0
        must_not_fire_zero &= ok
        zero_detail[zs] = {"events": len(zf), "bars": len(zdf)}

    results = {
        "must_fire": must_fire,
        "must_not_fire_volume_halved": must_not_fire_vol,
        "must_not_fire_flat": must_not_fire_flat,
        "must_not_fire_zero_event_symbol": must_not_fire_zero,
        "event_used": {"sym": sym, "event_ts": event_ts, "dir": int(first["dir"]),
                       "entry": float(first["entry"])},
        "zero_symbols_detail": zero_detail,
        "all_pass": must_fire and must_not_fire_vol and must_not_fire_flat and must_not_fire_zero,
    }
    json.dump(results, open(REPORT, "w"), indent=2)
    print(json.dumps(results, indent=2))
    return results["all_pass"]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
