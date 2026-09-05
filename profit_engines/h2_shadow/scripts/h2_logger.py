#!/usr/bin/env python3
"""h2_logger.py — kill-switch state (fail-closed) + append-only JSONL ledger for H2 shadow.

Kill-switch semantics (lab rule, copied from telegram_control/manual_signal.py):
  state.json is read FRESH before EVERY live-order path. Missing/corrupt file -> PAUSED.
  Nothing is ever cached in memory.
"""
import os, json, time
from datetime import datetime, timezone
from h2_lib import py, jsonl_append

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state.json")
SIGNALS = os.path.join(ROOT, "signals.jsonl")
LEDGER = os.path.join(ROOT, "pilot_ledger.jsonl")
RUNTIME = os.path.join(ROOT, "h2_runtime.json")


def pause_state():
    """Fresh read of the kill-switch. FAIL CLOSED: missing/corrupt -> paused True."""
    try:
        st = json.load(open(STATE))
        if not isinstance(st, dict):
            return {"paused": True, "reason": "corrupt_state_not_dict"}
        return st
    except FileNotFoundError:
        return {"paused": True, "reason": "missing_state_file"}
    except Exception:
        return {"paused": True, "reason": "corrupt_state_file"}


def is_paused():
    """Actual fresh read before EVERY order path. No caching."""
    return bool(pause_state().get("paused", False))


def set_paused(paused, reason=""):
    st = pause_state()
    st["paused"] = bool(paused)
    st["reason"] = reason
    st["updated_at"] = datetime.now(timezone.utc).isoformat()
    st["updated_by"] = "h2_shadow"
    with open(STATE, "w") as f:
        json.dump(py(st), f, indent=2, ensure_ascii=False)
    return st


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ms():
    return int(time.time() * 1000)


def new_attempt_id(sig):
    return f"H2-{sig.get('sym', '?')}-{int(sig.get('event_ts', 0)) // 1000}"


def log_signal(sig):
    """Every candidate signal the detector fires (observation, independent of pause)."""
    rec = {"evt": "signal", "ts": now_ms(), "ts_iso": now_iso(), **py(sig)}
    jsonl_append(SIGNALS, rec)


def ledger(evt):
    """Append one immutable fact to the order ledger."""
    rec = {"ts": now_ms(), "ts_iso": now_iso(), **py(evt)}
    jsonl_append(LEDGER, rec)


def load_ledger():
    out = []
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def read_runtime():
    try:
        st = json.load(open(RUNTIME))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def write_runtime(st):
    with open(RUNTIME, "w") as f:
        json.dump(py(st), f, indent=2, ensure_ascii=False)


def clear_runtime():
    with open(RUNTIME, "w") as f:
        f.write("{}\n")
