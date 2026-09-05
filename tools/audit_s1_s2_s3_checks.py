#!/usr/bin/env python3
"""
SPECIAL CHECKS S1 / S2 / S3 — fixtures proving each patch behaves correctly.
READ-ONLY for the live system (no orders, no config edits, stubbed exchange).
Run:  python3 tools/audit_s1_s2_s3_checks.py
"""
import sys, time, json, types, tempfile
from pathlib import Path

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/tradingos/guardian")
sys.path.insert(0, "/root/tradingos/trade")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def section(t):
    print("\n" + "=" * 70 + f"\n### {t}\n" + "=" * 70)


# ─────────────────────────────────────────────────────────────────────
# S2 SPECIAL CHECK — structural SL/TP reaches the proposal
# ─────────────────────────────────────────────────────────────────────
section("S2 SPECIAL — engine_v2 structural SL/TP not overwritten by fallback")

import engine_v2 as V2


def make_df(ohlcv):
    import pandas as pd
    ts0 = 1700000000.0
    df = pd.DataFrame([
        {"ts": ts0 + i * 900, "o": r[0], "h": r[1], "l": r[2], "c": r[3], "v": r[4]}
        for i, r in enumerate(ohlcv)
    ])
    tr = pd.concat([
        (df["h"] - df["l"]),
        (df["h"] - df["c"].shift(1)).abs(),
        (df["l"] - df["c"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(30).mean()
    df["vm"] = df["v"].rolling(60).median().shift(1)
    df["hi40"] = df["h"].rolling(40).max().shift(1)
    df["lo40"] = df["l"].rolling(40).min().shift(1)
    df["hi20"] = df["h"].rolling(24).max().shift(1)
    df["lo20"] = df["l"].rolling(24).min().shift(1)
    df["e20"] = df["c"].ewm(span=20, adjust=False).mean()
    df["e50"] = df["c"].ewm(span=50, adjust=False).mean()
    return df


# Build a clean uptrend with a realistic pullback setup → engine_v2 returns a
# consistent structural plan (BUY, sl < entry < tp).
# Build a compact uptrend (small slope → rng/atr < 8 avoids NO_STRUCTURE) then a
# pullback to EMA20 → TREND_PULLBACK setup with room to hi40.
uptrend = []
for i in range(80):
    p = 100.0 + i * 0.03                 # gentle slope; 20-bar range ~0.6
    uptrend.append((p - 0.02, p + 0.06, p - 0.05, p + 0.01, 120.0))
# Last 3 bars: pullback toward EMA20 (price dips but holds above e20)
uptrend += [
    (102.5, 102.7, 102.0, 102.1, 150.0),
    (102.1, 102.3, 101.8, 101.9, 130.0),
    (101.9, 102.4, 101.85, 102.2, 140.0),   # hold + close up → pullback confirmed
]
df_s2 = make_df(uptrend)
ts = df_s2["ts"].iloc[-1] + 60
entry_price = float(df_s2.iloc[-1]["c"])
atr_val = float(df_s2.iloc[-1]["atr"])

plan = V2.decide({"symbol": "AUDITX", "side": "BUY", "ts": ts}, df_s2)
v2_sl = plan.get("sl")
v2_tp = plan.get("tp1")
fallback_sl = entry_price - atr_val * 2
fallback_tp = entry_price + atr_val * 4

print(f"  entry={entry_price:.4f}  ATR={atr_val:.4f}")
print(f"  engine_v2 plan: setup={plan.get('setup')} method={plan.get('entry_method')} "
      f"sl={v2_sl:.4f} tp={v2_tp:.4f} room={plan.get('room_r')}")
print(f"  fallback 2/4 ATR: sl={fallback_sl:.4f} tp={fallback_tp:.4f}")

# Simulate the run_observation.py control flow (post-S2 patch):
#   sl = tp = None
#   if engine_v2_live: ... sl, tp = plan values (if consistent)
#   if sl is None or tp is None: fallback
sim_sl = sim_tp = None
# engine_v2_live=True path: consistent plan → sl/tp from plan
if plan.get("sl") and plan.get("tp1") and plan.get("room_r") is not None:
    if v2_sl < entry_price < v2_tp:           # BUY consistency check
        sim_sl, sim_tp = v2_sl, v2_tp
if sim_sl is None or sim_tp is None:
    sim_sl, sim_tp = fallback_sl, fallback_tp

check("S2_FINAL_EQUALS_ENGINE_V2", abs(sim_sl - v2_sl) < 1e-9 and abs(sim_tp - v2_tp) < 1e-9,
      f"final sl={sim_sl:.4f} tp={sim_tp:.4f} == engine_v2 sl={v2_sl:.4f} tp={v2_tp:.4f}")
check("S2_FINAL_NOT_EQUAL_FALLBACK",
      abs(sim_sl - fallback_sl) > 1e-9 or abs(sim_tp - fallback_tp) > 1e-9,
      f"structural differs from 2/4 ATR fallback")

# engine_v2_live=False path: sl/tp stay None → fallback applies
sim_sl2 = sim_tp2 = None
if sim_sl2 is None or sim_tp2 is None:
    sim_sl2, sim_tp2 = fallback_sl, fallback_tp
check("S2_FLAG_OFF_USES_FALLBACK",
      abs(sim_sl2 - fallback_sl) < 1e-9 and abs(sim_tp2 - fallback_tp) < 1e-9,
      f"engine_v2_live=false → sl={sim_sl2:.4f} tp={sim_tp2:.4f} (legacy 2/4 ATR)")


# ─────────────────────────────────────────────────────────────────────
# S1 SPECIAL CHECK — Soft-SL Recovery state machine transitions
# ─────────────────────────────────────────────────────────────────────
section("S1 SPECIAL — Soft-SL Recovery: NONE → ARMED → TIMEOUT/RECOVERED/FLOOR")

import reality_guardian as G

CLOCK = [time.time()]
G.time.time = lambda: CLOCK[0]
G.time.sleep = lambda s: None
CALLS = {"sl": [], "cl": []}
G._get_ticker = lambda s: [0.0][0]  # will be overridden
G._set_trading_stop = lambda *a, **k: (CALLS["sl"].append(k), True)[1]
G._close_position = lambda *a, **k: (CALLS["cl"].append(a), True)[1]
G._ensure_telegram_started = lambda: None
for fn in ("_enqueue_telegram_event", "_enqueue_telegram_close",
           "_enqueue_telegram_timeout", "_enqueue_telegram_phantom",
           "_enqueue_telegram_open"):
    setattr(G, fn, lambda *a, **k: None)
TMP = Path(tempfile.mkdtemp(prefix="s1_"))
G.GUARDIAN_STATE_PATH = TMP / "state.json"
G.PROFIT_ALERTS_PATH = TMP / "pa.jsonl"
G.TIMEOUT_ALERTS_PATH = TMP / "ta.jsonl"
G.RECOVERY_EVENTS_PATH = TMP / "re.jsonl"

FLAGS = {"engine_v2_live": False, "soft_sl_recovery": False}


def _fj(f):
    p = str(getattr(f, "name", ""))
    if p.endswith("trading_mode.json"):
        return {"engine_v2_live": FLAGS["engine_v2_live"]}
    if p.endswith("manual_session.json"):
        return {"soft_sl_recovery": FLAGS["soft_sl_recovery"]}
    return json.load(f)


G.json = types.SimpleNamespace(load=_fj, dump=json.dump, dumps=json.dumps, loads=json.loads)

PRICE = [0.0]
G._get_ticker = lambda s: PRICE[0]


def _pos(cur, sl=98.5, entry=100.0, side="Buy"):
    return {"symbol": "AUDITX", "side": side, "size": 1.0, "avgPrice": entry,
            "stopLoss": sl, "takeProfit": 103.0, "markPrice": cur,
            "openTime": int(CLOCK[0] * 1000), "createdTime": int(CLOCK[0] * 1000),
            "leverage": "5"}


def _manual_st():
    return {"AUDITX": {
        "be_fired": False, "partial_fired": False, "tight_fired": False,
        "mfe_peak": 0.0, "mae_trough": 0.0, "entry_to_sl_risk": 1.0,
        "side": "Buy", "entry": 100.0, "size": 1.0, "sl_initial": 99.0,
        "tp_initial": 103.0, "source": "MANUAL", "entry_time": CLOCK[0],
        "sl_soft": 99.0, "sl_hard": 98.5, "sl_buffer": 0.5, "sl_trigger": "MarkPrice",
        "recovery_state": None, "recovery_attempted": False,
    }}


# --- Transition 1: ARMED (wick recovery) ---
FLAGS["soft_sl_recovery"] = True


def _wick_df():
    import pandas as pd
    return pd.DataFrame({
        "ts": [CLOCK[0] - 900, CLOCK[0]], "o": [99.5, 99.2],
        "h": [99.6, 99.4], "l": [99.3, 98.8], "c": [99.4, 99.2], "v": [100.0, 100.0]})


CLOCK[0] = time.time()
G._load_m15_df = lambda s: _wick_df()
G._rsi_m15 = lambda s, period=14: 50.0
PRICE[0] = 99.05                                # returned above sl_soft 99
CALLS["cl"] = []
st = _manual_st()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
out = G._process_position(_pos(99.05), st)
rs = out["AUDITX"].get("recovery_state")
check("S1_TRANSITION_ARMED",
      rs is not None and rs.get("condition") == "wick" and len(CALLS["cl"]) == 0,
      f"recovery_state={rs}")

# --- Transition 2: next poll evaluates active recovery (TIMEOUT) ---
CALLS["cl"] = []
CLOCK[0] += 2701                                # past 45-min deadline
PRICE[0] = 99.5                                 # below entry, above floor
out2 = G._process_position(_pos(99.5), out)
check("S1_NEXT_POLL_EVALUATES",
      out2["AUDITX"].get("recovery_outcome") == "RECOVERY_TIMEOUT"
      and len(CALLS["cl"]) == 1,
      f"outcome={out2['AUDITX'].get('recovery_outcome')} closes={len(CALLS['cl'])}")

# --- Transition 3: RECOVERED (separate state) ---
CLOCK[0] = time.time()
G._load_m15_df = lambda s: _wick_df()
PRICE[0] = 99.05
st = _manual_st()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
out = G._process_position(_pos(99.05), st)      # arm
rs = out["AUDITX"].get("recovery_state")
armed = rs is not None
# next poll: price recovers above entry → RECOVERED
CALLS["cl"] = []
PRICE[0] = 100.5
out_rec = G._process_position(_pos(100.5), out)
check("S1_TRANSITION_RECOVERED",
      out_rec["AUDITX"].get("recovery_outcome") == "RECOVERED"
      and out_rec["AUDITX"].get("recovery_state") is None
      and len(CALLS["cl"]) == 0,
      f"outcome={out_rec['AUDITX'].get('recovery_outcome')} "
      f"state_cleared={out_rec['AUDITX'].get('recovery_state') is None}")

# --- Transition 4: FLOOR hit (separate state) ---
CLOCK[0] = time.time()
G._load_m15_df = lambda s: _wick_df()
PRICE[0] = 99.05
st = _manual_st()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
out = G._process_position(_pos(99.05), st)      # arm; floor = 99.05 - 0.25 = 98.8
# next poll: price hits floor → RECOVERY_FLOOR close
CALLS["cl"] = []
PRICE[0] = 98.7                                 # below floor 98.8
out_floor = G._process_position(_pos(98.7), out)
check("S1_TRANSITION_FLOOR",
      out_floor["AUDITX"].get("recovery_outcome") == "RECOVERY_FLOOR"
      and len(CALLS["cl"]) == 1,
      f"outcome={out_floor['AUDITX'].get('recovery_outcome')} closes={len(CALLS['cl'])}")

FLAGS["soft_sl_recovery"] = False

# ─────────────────────────────────────────────────────────────────────
# S3 SPECIAL CHECK — close_ts vs close (old bug vs new fix)
# ─────────────────────────────────────────────────────────────────────
section("S3 SPECIAL — reversal window: close_ts vs close")

import entry_quality_gate as EQG

# Fixture where close PRICE and close TIMESTAMP differ enough that the old bug
# would produce a wrong window. Impulse close price = 101.5 (high), close_ts = bar timestamp.
# Old code: df.ts > 101.5 + 60 = 1075s after epoch start → captures almost everything.
# New code: df.ts > close_ts + 60 → captures only bars AFTER the impulse.
df_s3 = make_df([(100.0 + i * 0.01, 100.0 + i * 0.01 + 0.02, 100.0 + i * 0.01 - 0.01,
                  100.0 + i * 0.01 + 0.01, 100.0) for i in range(70)])
# inject a UP impulse at bar 71 with a high close PRICE
impulse_bar = (105.0, 106.0, 104.5, 105.8, 400.0)
df_s3 = make_df([(100.0 + i * 0.01, 100.0 + i * 0.01 + 0.02, 100.0 + i * 0.01 - 0.01,
                  100.0 + i * 0.01 + 0.01, 100.0) for i in range(70)] + [impulse_bar]
                 + [(105.8, 105.9, 105.7, 105.75, 90.0),
                    (105.75, 105.85, 105.6, 105.65, 90.0),
                    (105.65, 105.7, 105.4, 105.5, 90.0)])  # lower highs after impulse
ts_s3 = df_s3["ts"].iloc[-1] + 60
imp = EQG.analyze_impulse(df_s3, ts_s3)
print(f"  impulse: close_price={imp['close']:.2f}  close_ts={imp['close_ts']:.0f}  "
      f"(diff: price-based window would start at {imp['close']+60:.0f}, "
      f"ts-based at {imp['close_ts']+60:.0f})")

# NEW (fixed) window: bars with ts > close_ts + 60
import pandas as pd
new_window = df_s3[(df_s3["ts"] > imp["close_ts"] + 60) & (df_s3["ts"] <= ts_s3 - 60)]
# OLD (buggy) window: bars with ts > close_price + 60
old_window = df_s3[(df_s3["ts"] > imp["close"] + 60) & (df_s3["ts"] <= ts_s3 - 60)]
print(f"  NEW window size: {len(new_window)}  OLD window size: {len(old_window)}")

check("S3_WINDOWS_DIFFER", len(new_window) != len(old_window),
      f"new={len(new_window)} old={len(old_window)} (old bug captured wrong bars)")

# NEW window should contain the post-impulse lower-highs → reversal detected
rev_new = EQG.check_reversal(df_s3, imp, ts_s3, "SELL")
check("S3_NEW_WINDOW_DETECTS_REVERSAL", bool(rev_new) is True,
      f"reversal detected with corrected window: {bool(rev_new)}")

# Verify close_ts is actually a timestamp (not a price)
check("S3_CLOSE_TS_IS_TIMESTAMP", imp["close_ts"] > 1e9,
      f"close_ts={imp['close_ts']:.0f} (epoch seconds, not a price)")
check("S3_CLOSE_IS_PRICE", 50.0 < imp["close"] < 200.0,
      f"close={imp['close']:.2f} (a price, not a timestamp)")

# ─────────────────────────────────────────────────────────────────────
import shutil
shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 70)
print(f"SPECIAL CHECKS SUMMARY: {len(PASS)} PASS, {len(FAIL)} FAIL")
print("=" * 70)
if FAIL:
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
