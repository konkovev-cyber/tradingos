#!/usr/bin/env python3
"""T32.K — CF3 paired-shadow profile in exit_shadow_engine.

Add CF3 (continuation-first state machine) as a third exit model
in the shadow-engine. Each closed trade writes 4 profiles:
  - REAL (post-factum, baseline)
  - P3_BASE (current live-shadow model)
  - B_maker_cur (placeholder, not changed)
  - C_maker_p3 (placeholder)
  - **NEW: D_cf3_state_machine** (T32.H design)

CF3 model: state machine. MFE 0.75-1.0R: protect at failed continuation.
MFE 1.0+: no fixed trail, exit only on (structure break + no new high) OR (retrace > 0.5).

Implementation: add a new shadow-engine exit simulation, write a new ledger field
"CF3_state_machine" with exit, NET, R, holding. Then T32.J-style comparison
becomes possible on real-trade data.

Production R148 frozen. P3 live-shadow unchanged. This is a shadow-only
parallel run, no change to P3 live.
"""
import json
import os
import sys
from pathlib import Path
import pickle
import numpy as np
import pandas as pd
import time as _t
import logging
import httpx
from datetime import datetime, timezone

ROOT = Path("/root/tradingos")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(ROOT / "logs/exit_shadow.log")])
log = logging.getLogger("exit_shadow_cf3")

# Configuration for CF3 (T32.H design)
CF3_THRESHOLDS = {"sub1r": (0.75, 1.0), "mature": (1.0, 999.0)}
CF3_RETRACE_LIMIT_SUB1R = 0.30
CF3_RETRACE_LIMIT_MATURE = 0.50
CF3_CAT_R = 3.0

# Mirror P3 / Maker config keys for ledger
PROFILE_CF3_KEY = "D_cf3_state_machine"
LIVE_KEYS = ("A_p3_taker", "B_maker_cur", "C_maker_p3", "D_cf3_state_machine")


def simulate_cf3(side: str, entry: float, sl0: float, tp0: float, bars: list[tuple]):
    """CF3 state machine: protect at SUB-1R failed continuation, no fixed trail for MFE 1.0+."""
    if not bars:
        return None
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    risk = abs(entry - sl0)
    if risk <= 0:
        return None
    sl = sl0
    catp = entry - CF3_CAT_R * risk * sign
    p = pd.DataFrame(bars, columns=["ts", "o", "h", "l", "c"])
    p["close"] = p["c"]
    if sign == 1:
        fav = (p["h"] - entry).clip(lower=0)
        adv = (entry - p["l"]).clip(lower=0)
    else:
        fav = (entry - p["l"]).clip(lower=0)
        adv = (p["h"] - entry).clip(lower=0)
    p["fav"] = fav; p["adv"] = adv
    p["mfe"] = fav.cummax()
    p["retrace_pct"] = (p["mfe"] - p["fav"]) / p["mfe"].replace(0, np.nan)
    p["new_high_5"] = (p["h"] > p["h"].rolling(5, min_periods=1).max().shift(1)).astype(int)
    p["new_low_5"] = (p["l"] < p["l"].rolling(5, min_periods=1).min().shift(1)).astype(int)
    if sign == 1:
        p["no_new_high_5"] = (p["new_high_5"].rolling(5, min_periods=1).sum() == 0).astype(int)
    else:
        p["no_new_low_5"] = (p["new_low_5"].rolling(5, min_periods=1).sum() == 0).astype(int)
    exits = []
    sub1r_locked = False  # locked at SUB-1R
    mature_locked = False  # locked at 1R+
    for i in range(len(p)):
        ts, o, h, l, c = p.iloc[i]["ts"], p.iloc[i]["o"], p.iloc[i]["h"], p.iloc[i]["l"], p.iloc[i]["c"]
        # Catastrophic FIRST
        if (l <= catp) if sign == 1 else (h >= catp):
            exits.append((("CAT", catp), i))
            break
        # 0.75-1.0R: lock at failed continuation (no_new_hh + retrace > 0.30)
        # 1.0+: only exit on struct break + no_new_hh, or retrace > 0.50
        mfe = p["fav"].iloc[i]
        if mfe <= 0:
            continue
        mfe_R = mfe / risk
        retrace = p["retrace_pct"].iloc[i]
        no_new_hh = bool(p["no_new_high_5"].iloc[i]) if not pd.isna(p["no_new_high_5"].iloc[i]) else False
        # MFE 0.75-1.0R zone: lock at failed continuation
        if 0.75 <= mfe_R < 1.0:
            if no_new_hh and not pd.isna(retrace) and retrace > CF3_RETRACE_LIMIT_SUB1R:
                exits.append((("CF3_SUB1R_LOCK", c), i))
                break
        # MFE 1.0+: no fixed trail. Only exit on struct break + no_new_hh, or severe retrace
        if mfe_R >= 1.0:
            if no_new_hh and not pd.isna(retrace) and retrace > CF3_RETRACE_LIMIT_MATURE:
                exits.append((("CF3_MATURE", c), i))
                break
    if not exits:
        exits.append((("T72", p["c"].iloc[-1]), len(p) - 1))
    return exits


def net_for_cf3(t, exit_px, qty):
    risk_USD = abs(t["entry"] - t["sl"]) * qty
    side = t["side_sign"]
    if side == 1:
        R = (exit_px - t["entry"]) / abs(t["entry"] - t["sl"])
    else:
        R = (t["entry"] - exit_px) / abs(t["entry"] - t["sl"])
    c = t.get("costs") or {}
    fees = float(c.get("fees") or 0)
    slip = abs(float(c.get("slippage_cost") or 0))
    realized_dol = R * risk_USD - fees - slip
    return realized_dol, R


def fetch_klines(sym, start_ms, end_ms, max_bars=300, retries=2):
    out = []
    while start_ms < end_ms:
        ok = False
        for _ in range(retries):
            try:
                r = httpx.get("https://api.bybit.com/v5/market/kline",
                    params={"category": "linear", "symbol": sym, "interval": "15",
                            "start": int(start_ms), "end": int(end_ms), "limit": max_bars},
                    timeout=10)
                d = r.json()
                if d.get("retCode") == 0:
                    lst = d.get("result", {}).get("list", [])
                    if not lst: ok = True; break
                    for b in lst:
                        out.append((int(b[0])/1000, float(b[1]), float(b[2]), float(b[3]), float(b[4])))
                    start_ms = int(lst[-1][0]) + 1
                    ok = True; break
            except Exception:
                pass
        if not ok: break
    out.sort()
    return out


def load_state():
    s = ROOT / "research" / "exit_shadow" / "shadow_state.json"
    if s.exists():
        return json.loads(s.read_text())
    return {"engine_started_at": 0, "registered": {}, "finalized": []}


def save_state(s):
    s["registered"] = s.get("registered", {})
    s["finalized"] = s.get("finalized", [])
    p = ROOT / "research" / "exit_shadow" / "shadow_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=1))


# === Demo: write one CF3 record to ledger for verification ===
def write_demo_cf3():
    """For T32.K verification: synthesize 1 entry with CF3 and append to ledger."""
    import pickle
    with open("/tmp/exit_sim/dataset.pkl", "rb") as f:
        DS = pickle.load(f)
    # Pick a winner with MFE 1.0+ to test CF3
    for t in DS:
        if (t.get("mfe_peak_r") or 0) >= 1.0 and t.get("realized_R", 0) > 0:
            break
    if t.get("mfe_peak_r", 0) < 1.0:
        print("no winner with MFE>=1R found")
        return
    # Add to ledger
    c = t.get("costs") or {}
    size = float(c.get("size") or 0)
    fees = float(c.get("fees") or 0)
    slip = abs(float(c.get("slippage_cost") or 0))
    # Simulate CF3
    path = t.get("path", [])
    if not path:
        print("no path for trade")
        return
    sim = simulate_cf3(t.get("side"), t["entry"], t["sl"], t.get("tp", 0), path)
    if not sim:
        print("no CF3 exit")
        return
    exit_px = sim[0][0][1]
    if exit_px is None or exit_px == 0:
        exit_px = path[-1][4]
    R_cf3, _ = net_for_cf3(t, exit_px, size)
    # Load ledger
    ledger = ROOT / "logs" / "trades" / "exit_shadow_ledger.jsonl"
    # Find the existing record
    records = []
    if ledger.exists():
        records = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    found = False
    for r in records:
        if r.get("decision_id") == t.get("decision_id", "?") or \
           (r.get("symbol") == t["symbol"] and r.get("real", {}).get("net_pnl") == t.get("net_pnl", 0)):
            # add CF3 profile
            r["D_cf3_state_machine"] = {
                "exit_px": exit_px,
                "exit_type": sim[0][0][0],
                "realized_R": R_cf3,
                "net": R_cf3,
                "holding_bars": sim[0][1],
            }
            found = True
            break
    if found:
        # Write back
        with ledger.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        print(f"Added CF3 profile to existing record for {t['symbol']} MFE={t['mfe_peak_r']:.2f}")
    else:
        print("no matching existing record, skipping ledger write")
    print(f"CF3 simulation: exit_type={sim[0][0][0]} exit_px={exit_px:.4f} R={R_cf3:.3f}")


if __name__ == "__main__":
    write_demo_cf3()
