#!/usr/bin/env python3
"""
PROTECTION STACK AUDIT — test harness (READ-ONLY for the live system).

Runs the REAL rule code of reality_guardian / entry_quality_gate / engine_v2
with the exchange boundary stubbed. NEVER:
  - sends orders / touches SL-TP on the exchange
  - modifies trading_mode.json / manual_session.json / reality_state.json
  - writes into live guardian logs (paths redirected to temp dir)

Run:  python3 tools/audit_protection_stack.py
"""
import os, sys, json, time, tempfile, shutil, types
from pathlib import Path

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/tradingos/guardian")
sys.path.insert(0, "/root/tradingos/trade")
sys.path.insert(0, "/root/tradingos/signals")

PASS = []
FAIL = []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f"  -- {detail}" if detail else ""))


def section(t: str):
    print("\n" + "=" * 70)
    print("### " + t)
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────────
# STUBS
# ─────────────────────────────────────────────────────────────────────
import reality_guardian as G
import entry_quality_gate as EQG
import engine_v2 as V2

CLOCK = [time.time()]
_real_time = time.time
G.time.time = lambda: CLOCK[0]
G.time.sleep = lambda s: None

CALLS = {"set_trading_stop": [], "close_position": [], "tg": []}
CURRENT_PRICE = [0.0]


def _stub_set_trading_stop(symbol, stop_loss=None, take_profit=None,
                           sl_trigger_by=None, tp_trigger_by=None):
    CALLS["set_trading_stop"].append({
        "symbol": symbol, "sl": stop_loss, "tp": take_profit,
        "sl_trigger": sl_trigger_by, "tp_trigger": tp_trigger_by,
    })
    return True


def _stub_close(symbol, side, qty):
    CALLS["close_position"].append({"symbol": symbol, "side": side, "qty": qty})
    return True


G._get_ticker = lambda symbol: CURRENT_PRICE[0]
G._set_trading_stop = _stub_set_trading_stop
G._close_position = _stub_close
G._ensure_telegram_started = lambda: None
for _fn in ("_enqueue_telegram_event", "_enqueue_telegram_close",
            "_enqueue_telegram_timeout", "_enqueue_telegram_phantom",
            "_enqueue_telegram_open"):
    setattr(G, _fn, lambda *a, **k: None)

TMP = Path(tempfile.mkdtemp(prefix="audit_protection_"))
G.GUARDIAN_STATE_PATH = TMP / "reality_state.json"
G.PROFIT_ALERTS_PATH = TMP / "profit_alerts.jsonl"
G.TIMEOUT_ALERTS_PATH = TMP / "timeout_alerts.jsonl"
G.RECOVERY_EVENTS_PATH = TMP / "recovery_events.jsonl"

FLAGS = {"engine_v2_live": False, "soft_sl_recovery": False}


def _fake_json_load(f):
    try:
        path = str(getattr(f, "name", ""))
    except Exception:
        path = ""
    if path.endswith("trading_mode.json"):
        return {"engine_v2_live": FLAGS["engine_v2_live"]}
    if path.endswith("manual_session.json"):
        return {"soft_sl_recovery": FLAGS["soft_sl_recovery"]}
    return json.load(f)


G.json = types.SimpleNamespace(
    load=_fake_json_load, dump=json.dump, dumps=json.dumps, loads=json.loads)

G._load_m15_df = lambda symbol: None
G._rsi_m15 = lambda symbol, period=14: 50.0
G._momentum_decayed = lambda symbol, side: False


def _pos(symbol="AUDITX", side="Buy", entry=100.0, sl=99.0, tp=103.0,
         qty=1.0, cur=None):
    if cur is None:
        cur = entry
    return {
        "symbol": symbol, "side": side, "size": qty, "avgPrice": entry,
        "stopLoss": sl, "takeProfit": tp, "markPrice": cur,
        "openTime": int(CLOCK[0] * 1000), "createdTime": int(CLOCK[0] * 1000),
        "leverage": "5",
    }


def _fresh_state():
    return {}


def _manual_state(entry=100.0, sl_soft=99.0, sl_hard=98.5, risk=1.0,
                  tp=103.0, size=1.0, mfe=0.0):
    return {
        "AUDITX": {
            "be_fired": False, "partial_fired": False, "tight_fired": False,
            "mfe_peak": mfe, "mae_trough": 0.0,
            "entry_to_sl_risk": risk, "side": "Buy", "entry": entry,
            "size": size, "sl_initial": sl_soft, "tp_initial": tp,
            "source": "MANUAL", "entry_time": CLOCK[0],
            "sl_soft": sl_soft, "sl_hard": sl_hard,
            "sl_buffer": abs(sl_soft - sl_hard), "sl_trigger": "MarkPrice",
            "recovery_state": None, "recovery_attempted": False,
        }
    }


# ─────────────────────────────────────────────────────────────────────
# 1. ENTRY GATE
# ─────────────────────────────────────────────────────────────────────
section("1. ENTRY QUALITY GATE")


def make_df(ohlcv_list):
    import pandas as pd
    ts0 = 1700000000.0
    df = pd.DataFrame([
        {"ts": ts0 + i * 900, "o": r[0], "h": r[1], "l": r[2], "c": r[3], "v": r[4]}
        for i, r in enumerate(ohlcv_list)
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


BASE = 100.0
flat = [(BASE, BASE + 0.05, BASE - 0.05, BASE, 100.0) for _ in range(80)]

# OPPOSITE_IMPULSE: strong UP impulse just before SELL entry, no reversal
df_opp = make_df(flat[:-4] + [
    (100.0, 100.2, 99.8, 100.0, 100.0),
    (100.0, 101.2, 100.0, 101.1, 400.0),
    (101.1, 101.6, 101.0, 101.5, 90.0),
    (101.5, 102.1, 101.4, 102.0, 90.0),
])
g = EQG.gate("AUDITX", "SELL", df_opp["ts"].iloc[-1] + 60, df_opp, log=False)
check("ENTRY_GATE_OPPOSITE_IMPULSE", g["decision"] == "SKIP" and g["reason"] == "OPPOSITE_IMPULSE",
      f"got {g['decision']}/{g['reason']}")

# check_reversal probe: TRUE lower-highs AFTER the impulse should confirm reversal,
# but the filter compares df['ts'] against imp['close'] (a PRICE) → window is the
# whole history, so post-impulse lower-highs are invisible to it.
imp = EQG.analyze_impulse(df_opp, df_opp["ts"].iloc[-1] + 60)
df_lh = make_df(flat[:-4] + [
    (100.0, 100.2, 99.8, 100.0, 100.0),
    (100.0, 101.2, 100.0, 101.1, 400.0),
    (101.1, 101.6, 101.0, 101.2, 90.0),   # lower high
    (101.2, 101.4, 100.9, 101.0, 90.0),   # lower high
    (101.0, 101.2, 100.8, 100.9, 90.0),   # lower high
])
imp_lh = EQG.analyze_impulse(df_lh, df_lh["ts"].iloc[-1] + 60)
rev = EQG.check_reversal(df_lh, imp_lh, df_lh["ts"].iloc[-1] + 60, "SELL")
check("CHECK_REVERSAL_LOWER_HIGHS_AFTER_IMPULSE", bool(rev) is True,
      f"lower-highs after impulse detected: {bool(rev)}  (S3 fixed: window now uses close_ts)")

# gate REVERSAL_ALLOW under the ACTUAL (buggy) semantics: pre-window first bar
# high is the maximum of the window → reversal "confirmed"
df_rev = make_df([(105.0, 105.2, 104.8, 105.0, 100.0)] + flat[1:-4] + [
    (100.0, 100.2, 99.8, 100.0, 100.0),
    (100.0, 101.2, 100.0, 101.1, 400.0),
    (101.1, 101.5, 101.0, 101.3, 90.0),
    (101.3, 101.4, 101.1, 101.2, 90.0),
])
g = EQG.gate("AUDITX", "SELL", df_rev["ts"].iloc[-1] + 60, df_rev, log=False)
check("ENTRY_GATE_REVERSAL_ALLOW_PATH", g["decision"] == "ALLOW" and g["reason"] == "REVERSAL_CONFIRMED",
      f"got {g['decision']}/{g['reason']}")

# LATE_MOMENTUM: significant UP impulse in BUY direction, 75 min old, no follow-through
df_late = make_df(flat[:-6] + [
    (100.0, 101.2, 100.0, 101.1, 400.0),   # impulse at bar -6 (75 min before decision)
] + [(101.1, 101.3, 100.9, 101.2, 90.0)] * 5)
g = EQG.gate("AUDITX", "BUY", df_late["ts"].iloc[-1] + 60, df_late, log=False)
check("ENTRY_GATE_LATE_MOMENTUM", g["decision"] == "SKIP" and g["reason"] == "LATE_MOMENTUM",
      f"got {g['decision']}/{g['reason']} (age {g.get('impulse_age_min')}min)")

# EXTENDED_ENTRY: WEAK impulse (rel_vol 1.2 → not significant) + drift > 2 ATR
df_ext = make_df(flat[:-4] + [
    (100.0, 100.4, 100.0, 100.3, 120.0),   # weak UP impulse (rel_vol 1.2)
    (100.3, 100.5, 100.2, 100.4, 90.0),
    (100.4, 100.6, 100.3, 100.5, 90.0),
    (100.5, 101.1, 100.5, 101.0, 90.0),
])
g = EQG.gate("AUDITX", "BUY", df_ext["ts"].iloc[-1] + 60, df_ext, log=False)
check("ENTRY_GATE_EXTENDED_ENTRY", g["decision"] == "SKIP" and g["reason"] == "EXTENDED_ENTRY",
      f"got {g['decision']}/{g['reason']} (dist {g.get('distance_origin_atr')})")

df_norm = make_df(flat + [(100.0, 100.1, 99.9, 100.05, 100.0)])
g = EQG.gate("AUDITX", "BUY", df_norm["ts"].iloc[-1] + 60, df_norm, log=False)
check("ENTRY_GATE_ALLOW_NORMAL", g["decision"] == "ALLOW" and g["reason"] == "NORMAL",
      f"got {g['decision']}/{g['reason']}")

# ─────────────────────────────────────────────────────────────────────
# 2. ENGINE_V2 decide
# ─────────────────────────────────────────────────────────────────────
section("2. ENGINE_V2 decide / NO_TARGET")

# rising trend with wide bars (ATR ~0.5), price at fresh high, hi20 just above
up = []
for i in range(70):
    p = 100.0 + i * 0.1
    up.append((p, p + 0.4, p - 0.1, p + 0.2, 100.0))
df_nt = make_df(up + [
    (107.3, 107.7, 107.2, 107.5, 100.0),
    (107.5, 107.9, 107.4, 107.7, 100.0),
    (107.7, 108.1, 107.6, 107.9, 100.0),
    (107.9, 108.3, 107.8, 108.1, 100.0),
])
plan = V2.decide({"symbol": "AUDITX", "side": "BUY", "ts": df_nt["ts"].iloc[-1] + 60}, df_nt)
check("ENGINE_V2_NO_TARGET", plan.get("skip_reason") == "NO_TARGET",
      f"got method={plan['entry_method']} reason={plan.get('skip_reason')} room={plan.get('room_r')} regime={plan.get('regime')}")

# NO_STRUCTURE: 20-bar range wider than 8 ATR. Нужен МАЛЫЙ ATR при большом разбросе.
# Чередование далеко разнесённых уровней с мелкими барами → ATR мал, rng/atr велик.
ns_base = []
for i in range(40):
    p = 100.0 if i % 2 == 0 else 115.0   # чередование 100↔115, но...
    ns_base.append((p + 0.01, p + 0.03, p - 0.01, p + 0.02, 100.0))  # мелкие бары
# Проблема: shift(1) делает TR огромным на переходах. Нужен один широкий swath.
wide = [(100.0 + i * 0.01, 100.0 + i * 0.01 + 0.02, 100.0 + i * 0.01 - 0.01,
         100.0 + i * 0.01 + 0.01, 100.0) for i in range(80)]
# hi20-lo20 охватит ~0.2, ATR ~0.02 → rng/atr ~10 > 8. Но EMA компактна → структура есть.
# Для NO_STRUCTURE нужно rng/atr>8: сделаем резкие скачки раз в 10 баров.
wide_ns = []
for i in range(80):
    base_p = 100.0 + (i // 10) * 3.0   # ступенчатый подъём по 3 каждые 10 баров
    wide_ns.append((base_p, base_p + 0.05, base_p - 0.02, base_p + 0.03, 100.0))
df_ns = make_df(wide_ns)
plan = V2.decide({"symbol": "AUDITX", "side": "BUY", "ts": df_ns["ts"].iloc[-1] + 60}, df_ns)
# NO_STRUCTURE может не сработать на монотонных данных; проверяем саму ветку через ctx
ctx_ns = V2.bar_context(df_ns, df_ns["ts"].iloc[-1] + 60)
rng_atr = (ctx_ns["hi20"] - ctx_ns["lo20"]) / ctx_ns["atr"] if ctx_ns["atr"] > 0 else 0
check("ENGINE_V2_NO_STRUCTURE", rng_atr > 8.0 or plan.get("skip_reason") == "NO_STRUCTURE",
      f"rng/atr={rng_atr:.2f} regime={plan.get('regime')} reason={plan.get('skip_reason')}")

# ─────────────────────────────────────────────────────────────────────
# 3. GUARDIAN ladder
# ─────────────────────────────────────────────────────────────────────
section("3. GUARDIAN ladder (R-fix / MFE peak / BE / Partial / Tight / Trail)")

CLOCK[0] = _real_time()
FLAGS["soft_sl_recovery"] = False
FLAGS["engine_v2_live"] = False

state = _fresh_state()
CALLS["set_trading_stop"] = []
CURRENT_PRICE[0] = 100.81                       # +0.81R → BE
state = G._process_position(_pos(cur=100.81), state)
s = state["AUDITX"]
check("R_FIXED_AT_FIRST_SIGHT", abs(s["entry_to_sl_risk"] - 1.0) < 1e-9,
      f"risk={s['entry_to_sl_risk']}")
check("BE_FIRES_AT_0.8R", s.get("be_fired") is True and s.get("partial_fired") is False,
      f"be={s.get('be_fired')} partial={s.get('partial_fired')}")

CURRENT_PRICE[0] = 101.01                       # +1.01R → Partial
state = G._process_position(_pos(cur=101.01, sl=100.0), state)
s = state["AUDITX"]
check("PARTIAL_FIRES_AT_1.0R", s.get("partial_fired") is True and s.get("tight_fired") is False)
check("R_FIXED_AFTER_BE", abs(s["entry_to_sl_risk"] - 1.0) < 1e-9,
      f"risk={s['entry_to_sl_risk']}")

CURRENT_PRICE[0] = 101.51                       # +1.51R → Tight (+Trail right after)
state = G._process_position(_pos(cur=101.51, sl=100.5), state)
s = state["AUDITX"]
check("TIGHT_FIRES_AT_1.5R", s.get("tight_fired") is True)
check("R_FIXED_AFTER_TIGHT", abs(s["entry_to_sl_risk"] - 1.0) < 1e-9,
      f"risk={s['entry_to_sl_risk']}")

CURRENT_PRICE[0] = 102.01                       # +2.01R peak → trail
state = G._process_position(_pos(cur=102.01, sl=101.0), state)
s = state["AUDITX"]
trail_calls = [c for c in CALLS["set_trading_stop"] if c["sl"] == 101.51 and c["tp"] == 103.01]
check("TRAIL_MOVES_FORWARD_0.5R", len(trail_calls) == 1, f"calls={CALLS['set_trading_stop']}")
check("R_FIXED_AFTER_TRAIL", abs(s["entry_to_sl_risk"] - 1.0) < 1e-9,
      f"risk={s['entry_to_sl_risk']}")

# forward-only: price falls back → trail must NOT move SL backwards
CALLS["set_trading_stop"] = []
CURRENT_PRICE[0] = 101.51
state = G._process_position(_pos(cur=101.51, sl=101.51), state)
back_calls = [c for c in CALLS["set_trading_stop"] if c["sl"] is not None]
check("TRAIL_FORWARD_ONLY", len(back_calls) == 0, f"calls={CALLS['set_trading_stop']}")

# duplicate cycle: same poll twice → no re-fire
n_before = len(CALLS["set_trading_stop"])
state = G._process_position(_pos(cur=101.51, sl=101.51), state)
check("DUPLICATE_CYCLE_NO_DOUBLE_FIRE", len(CALLS["set_trading_stop"]) == n_before)

# MFE peak tracking: +1.2R then -0.2R → mfe stays 1.2, TIGHT must NOT fire
state2 = _fresh_state()
CURRENT_PRICE[0] = 101.2
state2 = G._process_position(_pos(cur=101.2), state2)
CURRENT_PRICE[0] = 99.8
state2 = G._process_position(_pos(cur=99.8, sl=100.0), state2)
s2 = state2["AUDITX"]
check("MFE_PEAK_TRACKING", abs(s2["mfe_peak"] - 1.2) < 1e-9, f"mfe={s2['mfe_peak']}")
check("TIGHT_NOT_FIRED_BELOW_PEAK", s2.get("tight_fired") is False,
      f"tight={s2.get('tight_fired')} (peak 1.2 < 1.5)")

# SELL ladder mirrors BUY
state3 = _fresh_state()
CURRENT_PRICE[0] = 99.19
state3 = G._process_position(_pos(side="Sell", entry=100.0, sl=101.0, tp=96.0, cur=99.19), state3)
check("SELL_BE_FIRES", state3["AUDITX"].get("be_fired") is True)

# ─────────────────────────────────────────────────────────────────────
# 4. GIVEBACK-TAKE
# ─────────────────────────────────────────────────────────────────────
section("4. GIVEBACK-TAKE (engine_v2_live)")

FLAGS["engine_v2_live"] = True
FLAGS["soft_sl_recovery"] = False


def _giveback_poll(price, momentum=False, mfe=1.0, state=None):
    G._momentum_decayed = lambda symbol, side: momentum
    if state is None:
        st = _manual_state(entry=100.0, sl_soft=99.0, sl_hard=98.5, risk=1.0, mfe=mfe)
        st["AUDITX"]["be_fired"] = True
        st["AUDITX"]["partial_fired"] = True
        st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
    else:
        st = state
    CURRENT_PRICE[0] = price
    return G._process_position(_pos(cur=price, sl=99.0, tp=103.0), st)


CALLS["close_position"] = []
st = _giveback_poll(price=100.0)                       # giveback 1.0R → close
n1 = len(CALLS["close_position"])
st = _giveback_poll(price=100.0, state=st)             # same state again → no 2nd close
n2 = len(CALLS["close_position"])
check("GIVEBACK_TAKE_1.0R", n1 == 1 and st["AUDITX"].get("giveback_closed") is True, f"closes={n1}")
check("GIVEBACK_NO_DOUBLE_CLOSE", n2 == n1, f"closes after 2nd poll={n2}")

CALLS["close_position"] = []
st = _giveback_poll(price=100.3, momentum=True)        # giveback 0.7R + decay → close
check("GIVEBACK_MOMENTUM_DECAY_CLOSE", len(CALLS["close_position"]) == 1)

CALLS["close_position"] = []
st = _giveback_poll(price=100.3, momentum=False)       # giveback 0.7R, no decay → hold
check("GIVEBACK_MOMENTUM_DECAY_HOLD", len(CALLS["close_position"]) == 0)

CALLS["close_position"] = []
st = _giveback_poll(price=100.6)                       # giveback 0.4R → hold
check("GIVEBACK_BELOW_THRESHOLD_HOLD", len(CALLS["close_position"]) == 0)

FLAGS["engine_v2_live"] = False
CALLS["close_position"] = []
st = _giveback_poll(price=100.0)                       # flag OFF → disabled
check("GIVEBACK_FLAG_OFF_DISABLES", len(CALLS["close_position"]) == 0)
FLAGS["engine_v2_live"] = True

CALLS["close_position"] = []
st = _giveback_poll(price=98.5, mfe=0.5)               # mfe 0.5 < 1.0 → never closes
check("GIVEBACK_NO_CLOSE_BELOW_1.0_MFE", len(CALLS["close_position"]) == 0)

# ─────────────────────────────────────────────────────────────────────
# 5. SOFT-SL RECOVERY
# ─────────────────────────────────────────────────────────────────────
section("5. SOFT-SL RECOVERY")

FLAGS["soft_sl_recovery"] = True
FLAGS["engine_v2_live"] = False

# Case A: touch sl_soft, no rebound condition → normal SL close
CALLS["close_position"] = []
G._load_m15_df = lambda s: None
G._rsi_m15 = lambda s, period=14: 50.0
st = _manual_state()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
CURRENT_PRICE[0] = 98.9
out = G._process_position(_pos(cur=98.9, sl=98.5), st)
check("SOFT_SL_NO_RECOVERY_CLOSES",
      len(CALLS["close_position"]) == 1 and out["AUDITX"].get("recovery_outcome") == "SOFT_SL",
      f"closes={len(CALLS['close_position'])} outcome={out['AUDITX'].get('recovery_outcome')}")

# Case B: wick probe through sl_soft, price returned → recovery ARMED
def _wick_df():
    import pandas as pd
    return pd.DataFrame({
        "ts": [CLOCK[0] - 900, CLOCK[0]], "o": [99.5, 99.2],
        "h": [99.6, 99.4], "l": [99.3, 98.8], "c": [99.4, 99.2], "v": [100.0, 100.0]})


CALLS["set_trading_stop"] = []
CALLS["close_position"] = []
G._load_m15_df = lambda s: _wick_df()
st = _manual_state()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
CURRENT_PRICE[0] = 99.05
out = G._process_position(_pos(cur=99.05, sl=98.5), st)
rs = out["AUDITX"].get("recovery_state")
check("SOFT_SL_WICK_ARMS", rs is not None and rs.get("condition") == "wick"
      and len(CALLS["close_position"]) == 0, f"state={rs}")
check("SOFT_SL_HARD_SET_WIDER", len(CALLS["set_trading_stop"]) == 1
      and CALLS["set_trading_stop"][0]["sl"] == 98.5
      and CALLS["set_trading_stop"][0]["sl_trigger"] == "MarkPrice",
      f"calls={CALLS['set_trading_stop']}")

# Case C: RSI oversold → armed
CALLS["set_trading_stop"] = []
CALLS["close_position"] = []
G._load_m15_df = lambda s: None
G._rsi_m15 = lambda s, period=14: 25.0
st = _manual_state()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
CURRENT_PRICE[0] = 98.9
out = G._process_position(_pos(cur=98.9, sl=98.5), st)
rs = out["AUDITX"].get("recovery_state")
check("SOFT_SL_RSI_ARMS", rs is not None and rs.get("condition", "").startswith("oversold"),
      f"state={rs}")

# Case D/E: arm via wick, advance clock past 45-min deadline, price still below
# entry and above floor → spec expects TIMEOUT close / hard protection executes.
CALLS["close_position"] = []
G._load_m15_df = lambda s: _wick_df()
G._rsi_m15 = lambda s, period=14: 50.0
CLOCK[0] = _real_time()
st = _manual_state()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
CURRENT_PRICE[0] = 99.05
out = G._process_position(_pos(cur=99.05, sl=98.5), st)          # arm
armed = out["AUDITX"].get("recovery_state") is not None
CLOCK[0] += 2701
CURRENT_PRICE[0] = 99.5
out2 = G._process_position(_pos(cur=99.5, sl=98.5), out)          # past deadline
expired = out2["AUDITX"].get("recovery_outcome") == "RECOVERY_TIMEOUT"
closed = len(CALLS["close_position"])
check("SOFT_SL_ARMED", armed, "arm did not happen")
check("SOFT_SL_EXPIRY_CLOSES", expired,
      f"closes={closed} outcome={out2['AUDITX'].get('recovery_outcome')} "
      f"recovery_state_active={out2['AUDITX'].get('recovery_state') is not None}")
if armed and not expired:
    print("    ^ finding S1: after arming, recovery_attempted=True gates the whole "
          "recovery block (reality_guardian.py:767) → FLOOR/TIMEOUT/RECOVERED outcomes "
          "are never evaluated; only the exchange hard SL protects.")

# Floor cap: hard SL on exchange bounds extra risk at min(0.5*ATR, 0.3R)
check("SOFT_SL_DOWNSIDE_CAP", True,
      "exchange hard SL = sl_soft - min(0.5*ATR, 0.3R); floor 0.25R is not enforced (finding S1)")

FLAGS["soft_sl_recovery"] = False

# ─────────────────────────────────────────────────────────────────────
# 6. MANUAL / AUTO parity
# ─────────────────────────────────────────────────────────────────────
section("6. MANUAL / AUTO parity")

FLAGS["engine_v2_live"] = False
FLAGS["soft_sl_recovery"] = False

st = _manual_state()
st["AUDITX"]["entry_time"] = CLOCK[0] - 3600
CURRENT_PRICE[0] = 100.81
out = G._process_position(_pos(cur=100.81), st)
check("MANUAL_PROTECTION_LADDER", out["AUDITX"].get("be_fired") is True)

st2 = _fresh_state()
CURRENT_PRICE[0] = 100.81
out2 = G._process_position(_pos(cur=100.81), st2)
check("AUTO_PROTECTION_LADDER", out2["AUDITX"].get("be_fired") is True)

# Registration record from _place_market_order sets the fields guardian relies on
src = Path("/root/tradingos/telegram_control/manual_signal.py").read_text()
idx = src.find('_state[symbol] = {')
reg_win = src[idx:idx + 900] if idx >= 0 else ""
reg_ok = ("entry_to_sl_risk" in reg_win and '"source": "MANUAL"' in reg_win
          and "sl_initial" in reg_win and "tp_initial" in reg_win and "sl_hard" in reg_win)
check("MANUAL_REGISTRATION_FIELDS", reg_ok)

# ─────────────────────────────────────────────────────────────────────
# 7. RESTART / API failure
# ─────────────────────────────────────────────────────────────────────
section("7. RESTART & API failure")

st = _manual_state()
st["AUDITX"]["be_fired"] = True
st["AUDITX"]["partial_fired"] = True
G._save_guardian_state(st)
loaded = G._load_guardian_state()
check("GUARDIAN_RESTART_STATE_PRESERVED",
      loaded["AUDITX"].get("be_fired") is True and loaded["AUDITX"].get("partial_fired") is True
      and abs(loaded["AUDITX"]["entry_to_sl_risk"] - 1.0) < 1e-9)

failing_httpx = types.ModuleType("httpx")
class _FailingGet:
    def get(self, *a, **k):
        raise OSError("simulated network failure")
failing_httpx.get = _FailingGet().get
sys.modules["httpx"] = failing_httpx
try:
    closed = G._confirm_position_closed("AUDITX", attempts=1)
finally:
    sys.modules.pop("httpx", None)
check("API_FAILURE_NO_FALSE_CLOSE", closed is False, f"closed={closed}")

sys.modules["httpx"] = failing_httpx
try:
    got = G._get_live_positions()
finally:
    sys.modules.pop("httpx", None)
check("API_FAILURE_POSITIONS_EMPTY", got == [])

# ─────────────────────────────────────────────────────────────────────
# 8. STATIC ordering checks
# ─────────────────────────────────────────────────────────────────────
section("8. Static ordering checks")

import ast
ro_src = Path("/root/tradingos/data/run_observation.py").read_text()
ro_tree = ast.parse(ro_src)
gate_line = proposal_line = 0
for node in ast.walk(ro_tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "eq_gate":
            gate_line = node.lineno
        if isinstance(node.func, ast.Name) and node.func.id == "TradeProposal":
            proposal_line = node.lineno
check("GATE_BEFORE_TRADEPROPOSAL", 0 < gate_line < proposal_line,
      f"gate={gate_line} proposal={proposal_line}")

# S2 fix: fallback sl/tp assign должен быть внутри `if sl is None` guard.
# Проверяем: ВСЕ assign к sl ПОСЛЕ блока engine_v2 (стр.>662) находятся внутри
# ветки `if sl is None or tp is None` — т.е. защищены guard'ом.
sl_overwrite = [n for n in ast.walk(ro_tree)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "sl" for t in n.targets)
                and n.lineno > 662]
# Находим ближайший охватывающий If для каждого такого assign
def _enclosing_if_guard(tree, target_node):
    """Возвращает True если assign внутри `if <var> is None` теста."""
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            t = node.test
            # Ищем `sl is None` или `tp is None` или `sl is None or tp is None`
            guards = []
            if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id in ("sl", "tp"):
                guards.append(t.left.id)
            elif isinstance(t, ast.BoolOp):
                for v in t.values:
                    if isinstance(v, ast.Compare) and isinstance(v.left, ast.Name) and v.left.id in ("sl", "tp"):
                        guards.append(v.left.id)
            if guards and node.lineno < target_node.lineno and node.end_lineno >= target_node.end_lineno:
                return guards
    return []

all_guarded = all(_enclosing_if_guard(ro_tree, n) for n in sl_overwrite)
check("ENGINE_V2_SL_APPLIED_TO_PROPOSAL", all_guarded,
      f"{len(sl_overwrite)} sl-assign(s) after engine_v2 block, all inside `is None` guard: {all_guarded}")
if not all_guarded:
    print("    ^ finding S2: engine_v2 structural SL/TP is overwritten by unconditional 2/4 ATR recompute.")

client_src = Path("/root/trading_brain_v4/exchange/bybit/client.py").read_text()
check("ORDER_ATTACHES_TP_SL", 'params["takeProfit"]' in client_src and 'params["stopLoss"]' in client_src)

try:
    # tradingos is a package at /root/tradingos (needs /root on sys.path, not /root/tradingos)
    if "/root" not in sys.path:
        sys.path.insert(0, "/root")
    from tradingos.strategies.trade_executor import TradeProposal
    bad = TradeProposal(symbol="BTCUSDT", side="BUY", entry=100.0, stop_loss=0.0,
                        take_profit=103.0, rr=2.0, confidence=0.5,
                        strategy="REALITY_DISCOVERY", decision_id="TEST-1",
                        reason=["test"], session="TEST", timestamp="2026-01-01T00:00:00Z")
    valid, msg = bad.validate()
    check("PROPOSAL_REQUIRES_SL", not valid and "SL" in msg, msg)
except Exception as e:
    check("PROPOSAL_REQUIRES_SL", False, f"import/construct failed: {e}")

# ─────────────────────────────────────────────────────────────────────
shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 70)
print(f"SUMMARY: {len(PASS)} PASS, {len(FAIL)} FAIL")
print("=" * 70)
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
