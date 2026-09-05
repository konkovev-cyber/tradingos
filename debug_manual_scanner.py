#!/usr/bin/env python3
"""Debug script: run manual_scanner diagnostics per symbol."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/tradingos")

import httpx
from tradingos.signals.manual_scanner import (
    _klines, _ema, _rsi_wilder, _funding_oi, _stock_symbol_type, load_config
)

CONFIG = Path("/root/tradingos/operations/manual_session.json")
cfg = json.loads(CONFIG.read_text())
symbols = cfg.get("symbols", [])
min_score = int(cfg.get("min_signal_score", 80))
min_notional = float(cfg.get("min_h1_notional_usd", 200_000) or 200_000)
min_h1_move_pct = float(cfg.get("min_h1_move_pct", 0.25) or 0.25)

print(f"Config: min_score={min_score}, min_notional=${min_notional:,.0f}, min_move={min_h1_move_pct}%")
print(f"Symbols to check: {len(symbols)}")
print("="*80)

def check_symbol(client, sym):
    h1 = _klines(client, sym, "60", 96)
    m15 = _klines(client, sym, "15", 200)
    if not h1:
        return {"sym": sym, "status": "NO_H1_DATA"}
    if not m15:
        return {"sym": sym, "status": "NO_M15_DATA"}

    h1_vols = [(b["close"] * b["volume"]) for b in h1]
    h1_notional_med = sorted(h1_vols)[len(h1_vols) // 2]
    last_ts = h1[-1]["ts"]
    last_age_h = (time.time() * 1000 - last_ts) / 3_600_000
    range_3h = max(b["high"] for b in h1[-3:]) - min(b["low"] for b in h1[-3:])
    move_pct = range_3h / h1[-1]["close"] * 100 if h1[-1]["close"] else 0

    h1c = [b["close"] for b in h1]
    closed_last = h1c[-2]
    e20, e50 = _ema(h1c, 20)[-2], _ema(h1c, 50)[-2]
    h1_up = closed_last > e20 > e50
    h1_down = closed_last < e20 < e50

    m15c = [b["close"] for b in m15]
    me20 = _ema(m15c, 20)[-2]
    m15_up = m15c[-2] > me20

    rsi = _rsi_wilder(h1c)
    vols = [b["volume"] for b in h1]
    vol_ratio = vols[-2] / (sum(vols[-22:-2]) / 20) if len(vols) > 22 and sum(vols[-22:-2]) > 0 else 1.0

    dist_to_e20 = (closed_last - e20) / e20 * 100
    entry_q = 15 if abs(dist_to_e20) < 0.15 else (10 if abs(dist_to_e20) < 0.35 else 5)

    fo = _funding_oi(client, sym)
    funding = fo.get("funding", 0.0)
    fund_score = 10 if abs(funding) < 0.0001 else (6 if funding < 0.0003 else 3)

    parts = {}
    if h1_up:
        parts["h1_trend"] = 25
    elif h1_down:
        parts["h1_trend"] = 25
    elif closed_last > e20:
        parts["h1_trend"] = 15
    elif closed_last < e20:
        parts["h1_trend"] = 15
    else:
        parts["h1_trend"] = 0

    parts["m15_structure"] = 20 if m15_up else 5
    if h1_up and rsi > 50:
        parts["momentum"] = min(15, int(rsi / 100 * 15))
    elif h1_down and rsi < 50:
        parts["momentum"] = min(15, int((100 - rsi) / 100 * 15))
    else:
        parts["momentum"] = 3
    parts["volume"] = min(15, int(vol_ratio * 10)) if vol_ratio >= 1.0 else 0
    parts["funding_oi"] = fund_score
    parts["entry_quality"] = entry_q
    total = sum(parts.values())

    # check quality gates
    reasons = []
    if h1_notional_med < min_notional:
        reasons.append(f"LIQ:{h1_notional_med/1000:.0f}k<{min_notional/1000:.0f}k")
    if last_age_h > 3:
        reasons.append(f"STALE:{last_age_h:.1f}h")
    if move_pct < min_h1_move_pct:
        reasons.append(f"FLAT:{move_pct:.2f}%<{min_h1_move_pct}%")

    return {
        "sym": sym,
        "status": "PASS" if not reasons and total >= min_score else "FAIL",
        "score": total,
        "parts": parts,
        "h1_notional_med": h1_notional_med,
        "last_age_h": last_age_h,
        "move_pct": move_pct,
        "h1_up": h1_up,
        "h1_down": h1_down,
        "m15_up": m15_up,
        "rsi": rsi,
        "vol_ratio": vol_ratio,
        "dist_e20_pct": dist_to_e20,
        "funding": funding,
        "reasons": reasons,
    }

results = []
with httpx.Client() as client:
    for sym in symbols:
        try:
            r = check_symbol(client, sym)
            results.append(r)
        except Exception as e:
            results.append({"sym": sym, "status": "ERROR", "error": str(e)})
        time.sleep(0.05)  # rate limit protection

# Sort by score descending
passed = [r for r in results if r["status"] == "PASS"]
failed = [r for r in results if r["status"] != "PASS"]

print(f"\n=== PASSED ({len(passed)}) ===")
for r in sorted(passed, key=lambda x: -x["score"])[:20]:
    print(f"  {r['sym']:12s} score={r['score']:3d}  vol={r['vol_ratio']:.2f}  rsi={r['rsi']:.1f}  move={r['move_pct']:.2f}%  notional=${r['h1_notional_med']/1000:.0f}k")

print(f"\n=== FAILED ({len(failed)}) ===")
for r in sorted(failed, key=lambda x: -x.get("score", 0))[:30]:
    reasons = ", ".join(r.get("reasons", []))
    print(f"  {r['sym']:12s} score={r.get('score', 0):3d}  {reasons}")

print(f"\n=== FAIL BREAKDOWN ===")
liq = [r for r in failed if any("LIQ" in s for s in r.get("reasons", []))]
flat = [r for r in failed if any("FLAT" in s for s in r.get("reasons", []))]
stale = [r for r in failed if any("STALE" in s for s in r.get("reasons", []))]
low_score = [r for r in failed if not any(k in ", ".join(r.get("reasons", [])) for k in ["LIQ", "FLAT", "STALE"])]
print(f"  Liquidity (notional < {min_notional}): {len(liq)}")
print(f"  Flat (move < {min_h1_move_pct}%): {len(flat)}")
print(f"  Stale (age > 3h): {len(stale)}")
print(f"  Low score (< {min_score}) after gates: {len(low_score)}")

# Show specific big names
for name in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "AAPLUSDT", "TSLAUSDT", "NVDAUSDT"]:
    for r in results:
        if r["sym"] == name:
            print(f"\n  {name}: score={r.get('score', 0)}  reasons={r.get('reasons', [])}  vol={r.get('vol_ratio')}  rsi={r.get('rsi')}  move={r.get('move_pct')}  notional=${r.get('h1_notional_med', 0)/1000:.0f}k")
            break
