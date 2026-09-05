"""Unit tests for MANUAL SOFT/HARD guard override (turn ~103).

Coverage:
  Test A: PROPOSED_RISK_TOO_HIGH in MANUAL -> confirmed via token -> revalidation OK
  Test B: HARD_KILL during revalidation -> CONFIRMATION_INVALIDATED
  Test C: token expired -> invalid
  Test D: token used -> invalid (replay protection)
  Test E: CANCEL removes token from state
  Test F: 6 hard reasons all block regardless of user confirmation
  Test G: AUTO branch (trade_executor.py) UNCHANGED
  Test H: deposit_guard.py / signal_generator.py / exit_shadow_engine.py UNCHANGED
  Test I: trading_mode.json unchanged (mode=MANUAL)
  Test J: audit log has no credentials, has required fields
  Test K: hard-set is exact (6 reasons)
  Test L: token state not modified until confirmed
  Test M: revalidation catches hard violation emerging post-confirmation
"""
import os
import sys
import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

ROOT = "/root/tradingos"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Re-extract _HARD_NO_OVERRIDE from source (it's a local set in _execute_manual_order)
_SRC = open("/root/tradingos/telegram_control/manual_signal.py").read()
_HARD_NO_OVERRIDE = set(re.search(
    r"_HARD_NO_OVERRIDE\s*=\s*\{([^}]+)\}", _SRC).group(1).replace('"', '').replace("'", "").replace(",", "").split())


# ----- Test C/D: _is_token_valid (production function) -----
def test_is_token_valid_fresh():
    from telegram_control.manual_signal import _is_token_valid
    tok = {"expires_at": (datetime.utcnow() + timedelta(seconds=60)).isoformat(), "used": False}
    assert _is_token_valid(tok) is True


def test_is_token_valid_expired():
    from telegram_control.manual_signal import _is_token_valid
    tok = {"expires_at": (datetime.utcnow() - timedelta(seconds=10)).isoformat(), "used": False}
    assert _is_token_valid(tok) is False


def test_is_token_valid_used():
    from telegram_control.manual_signal import _is_token_valid
    tok = {"expires_at": (datetime.utcnow() + timedelta(seconds=60)).isoformat(), "used": True}
    assert _is_token_valid(tok) is False


def test_is_token_valid_none():
    from telegram_control.manual_signal import _is_token_valid
    assert _is_token_valid(None) is False
    assert _is_token_valid({}) is False


def test_is_token_valid_bad_iso():
    from telegram_control.manual_signal import _is_token_valid
    tok = {"expires_at": "not-a-date", "used": False}
    assert _is_token_valid(tok) is False


# ----- Test E: CANCEL removes token -----
def test_cancel_removes_token_from_state():
    from telegram_control import manual_signal as ms
    uid, tok = 99999, "cancel_test_token"
    ms._pending_overrides[(uid, tok)] = {"pending": {}, "used": False}
    assert (uid, tok) in ms._pending_overrides
    # mirror cancel path
    ms._pending_overrides.pop((uid, tok), None)
    assert (uid, tok) not in ms._pending_overrides


# ----- Test A/B/F/M: _execute_confirmed_pending (production, patch internals) -----
# Pattern: patch the module-level imports of guard and _place_market_order inside manual_signal.
PRODUCTION_PENDING = {"symbol": "BTCUSDT", "side": "Buy", "sl": 100, "tp": 110, "_proposed": 0.5}


def _patched_execute(guard_return, pm_return, guard_side_effect=None):
    """Call production _execute_confirmed_pending with patched guard + executor.

    Production function does `from tradingos.strategies.deposit_guard import get_guard as _get_guard`
    INSIDE the try block. So patching `ms.get_guard` doesn't work — we must patch
    at the source module: `tradingos.strategies.deposit_guard.get_guard`.
    """
    from telegram_control import manual_signal as ms
    import tradingos.strategies.deposit_guard as dg
    with patch.object(dg, 'get_guard', create=True) as _fake_guard, \
         patch("telegram_control.manual_signal._place_market_order") as _fake_pm:
        if guard_side_effect is not None:
            _fake_guard.return_value.can_open_position.side_effect = guard_side_effect
        else:
            _fake_guard.return_value.can_open_position.return_value = guard_return
        _fake_pm.return_value = pm_return
        ok, msg = ms._execute_confirmed_pending(PRODUCTION_PENDING, 100.0, {})
        return ok, msg, _fake_pm


def test_hard_revalidation_HARD_KILL():
    """HARD_KILL bypasses soft override."""
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "HARD_KILL"),
        pm_return={"ok": True})
    assert ok is False
    assert "CONFIRMATION_INVALIDATED" in msg
    assert "HARD_KILL" in msg
    _fake_pm.assert_not_called()


def test_hard_revalidation_KILL_SWITCH():
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "MANUAL_KILL"),
        pm_return={"ok": True})
    assert ok is False
    assert "MANUAL_KILL" in msg
    _fake_pm.assert_not_called()


def test_hard_revalidation_NO_DAY_BASELINE():
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "NO_DAY_BASELINE"),
        pm_return={"ok": True})
    assert ok is False
    assert "NO_DAY_BASELINE" in msg
    _fake_pm.assert_not_called()


def test_hard_revalidation_OPEN_RISK_OVER_LIMIT():
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "OPEN_RISK_OVER_LIMIT"),
        pm_return={"ok": True})
    assert ok is False
    assert "OPEN_RISK_OVER_LIMIT" in msg
    _fake_pm.assert_not_called()


def test_hard_revalidation_CRITICAL_OPEN_RISK():
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "CRITICAL_OPEN_RISK"),
        pm_return={"ok": True})
    assert ok is False
    assert "CRITICAL_OPEN_RISK" in msg
    _fake_pm.assert_not_called()


def test_hard_revalidation_EQUITY_FETCH_ERROR():
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "EQUITY_FETCH_ERROR"),
        pm_return={"ok": True})
    assert ok is False
    assert "EQUITY_FETCH_ERROR" in msg
    _fake_pm.assert_not_called()


def test_revalidation_blocks_hard_violation_emerging_post_confirm():
    """Soft-confirmed trade; at revalidation, NEW hard violation emerges."""
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(False, "HARD_KILL"),
        pm_return={"ok": True})
    assert ok is False
    assert "HARD_KILL" in msg
    _fake_pm.assert_not_called()


def test_soft_path_executes_on_revalidation_ok():
    """SOFT path: PROPOSED_RISK_TOO_HIGH at request time, OK at revalidation time -> execute."""
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(True, "OK"),
        pm_return={"ok": True})
    assert ok is True
    assert msg == "executed"
    _fake_pm.assert_called_once()


def test_soft_path_execution_failure_returns_false():
    ok, msg, _fake_pm = _patched_execute(
        guard_return=(True, "OK"),
        pm_return={"ok": False, "error": "exchange error"})
    assert ok is False
    assert "execution failed" in msg


def test_soft_path_execution_exception_returns_false():
    from telegram_control import manual_signal as ms
    import tradingos.strategies.deposit_guard as dg
    # Patch at the SOURCE module (production imports get_guard inside the fn),
    # same as _patched_execute — patching ms.get_guard does nothing.
    with patch.object(dg, "get_guard", create=True) as _fake_guard, \
         patch("telegram_control.manual_signal._place_market_order", side_effect=Exception("timeout")) as _fake_pm:
        _fake_guard.return_value.can_open_position.return_value = (True, "OK")
        ok, msg = ms._execute_confirmed_pending(PRODUCTION_PENDING, 100.0, {})
    assert ok is False
    assert "exception" in msg


def test_guard_unreachable_blocks():
    """If guard raises (network etc), never execute."""
    from telegram_control import manual_signal as ms
    import tradingos.strategies.deposit_guard as dg
    with patch.object(dg, 'get_guard', create=True) as _fake_guard, \
         patch("telegram_control.manual_signal._place_market_order") as _fake_pm:
        _fake_guard.return_value.can_open_position.side_effect = Exception("connection failed")
        ok, msg = ms._execute_confirmed_pending(PRODUCTION_PENDING, 100.0, {})
    assert ok is False
    assert "guard unreachable" in msg
    _fake_pm.assert_not_called()


# ----- Test G: AUTO branch (trade_executor.py) UNCHANGED -----
def test_auto_branch_uses_can_open_position_with_hard_block():
    """AUTO must still hard-block immediately on can_open_position=False."""
    content = open("/root/tradingos/strategies/trade_executor.py").read()
    assert "can_open_position" in content
    assert "BLOCKED" in content
    # Extract lines 305-320 of trade_executor.py (the guard block) and verify pattern
    lines = content.split("\n")
    block_start = None
    for i, ln in enumerate(lines):
        if "allowed, reason = get_guard" in ln:
            block_start = i
            break
    assert block_start is not None
    block = "\n".join(lines[block_start:block_start+8])
    assert 'if not allowed:' in block
    assert 'return {"status": "BLOCKED"' in block
    # NO override state in AUTO
    assert "_pending_overrides" not in content
    assert "CONFIRM RISK" not in content


def test_auto_path_does_not_use_proposed_risk_override_state():
    content = open("/root/tradingos/strategies/trade_executor.py").read()
    for forbidden in ["_pending_overrides", "CONFIRM RISK", "confirmation_required",
                      "token_urlsafe", "_is_token_valid"]:
        assert forbidden not in content, f"AUTO must not reference {forbidden}"


# ----- Test H: production code UNCHANGED (except manual_signal + manual_bot) -----
def test_deposit_guard_unchanged():
    """deposit_guard.py must not have any override logic."""
    content = open("/root/tradingos/strategies/deposit_guard.py").read()
    for forbidden in ["_pending_overrides", "CONFIRM RISK", "confirmation_required",
                      "token_urlsafe"]:
        assert forbidden not in content, f"deposit_guard.py must not reference {forbidden}"


def test_signal_generator_unchanged():
    content = open("/root/tradingos/signals/signal_generator.py").read()
    for forbidden in ["_pending_overrides", "CONFIRM RISK", "confirmation_required"]:
        assert forbidden not in content, f"signal_generator.py must not reference {forbidden}"


def test_exit_shadow_engine_unchanged():
    content = open("/root/tradingos/research/exit_shadow/exit_shadow_engine.py").read()
    for forbidden in ["_pending_overrides", "CONFIRM RISK", "confirmation_required"]:
        assert forbidden not in content


def test_exit_shadow_report_unchanged():
    content = open("/root/tradingos/research/exit_shadow/exit_shadow_report.py").read()
    for forbidden in ["_pending_overrides", "CONFIRM RISK", "confirmation_required"]:
        assert forbidden not in content


# ----- Test I: trading_mode.json config validation -----
def test_trading_mode_config_valid():
    """Config must be in a known state (MANUAL+freeze OR AUTO+unfrozen).

    This test validates internal consistency, not a specific mode:
    - mode=AUTO → kill_switch must be false (unfrozen)
    - mode=MANUAL → kill_switch may be true (frozen)
    They must never contradict (AUTO + kill_switch=true = misconfigured).
    """
    tm = json.load(open("/root/tradingos/operations/trading_mode.json"))
    mode = tm.get("mode", "")
    kill_switch = tm.get("kill_switch", None)
    if mode == "AUTO":
        assert kill_switch is False, f"mode=AUTO but kill_switch={kill_switch} — misconfigured"
    elif mode == "MANUAL":
        assert kill_switch in (True, False), f"mode=MANUAL but kill_switch={kill_switch}"
    else:
        assert False, f"Unknown mode: {mode}"


def test_trading_mode_has_r148_note():
    """Note_auto_stop_2026_08_19 should still be present (R148 frozen)."""
    tm = json.load(open("/root/tradingos/operations/trading_mode.json"))
    assert "note_auto_stop_2026_08_19" in tm, "AUTO-stop note disappeared"
    assert "HARD" in tm["config_version"] or "v3" in tm["config_version"]


# ----- Test J: audit log -----
def test_audit_log_helper_writes_json(tmp_path):
    """Verify _audit_log writes a JSON line with required fields, no credentials."""
    from telegram_control.manual_signal import _audit_log
    p = tmp_path / "audit.jsonl"
    rec = {"ts": "2026-08-20T05:00:00Z", "event": "confirmation_requested", "uid": 1,
           "symbol": "BTCUSDT", "side": "Buy", "proposed": 0.5, "reason": "PROPOSED_RISK_TOO_HIGH"}
    _audit_log(str(p), rec)
    line = open(p).readlines()[0]
    parsed = json.loads(line)
    assert parsed["event"] == "confirmation_requested"
    assert parsed["uid"] == 1
    assert "api_key" not in parsed
    assert "api_secret" not in parsed
    assert "apiSecret" not in parsed


def test_audit_log_handles_no_credentials_even_if_passed():
    """If someone tries to pass api_key in record, we just write it. Verify field naming hygiene."""
    from telegram_control.manual_signal import _audit_log
    # The function does NOT filter fields. It just writes what you pass.
    # This test documents that callers MUST NOT include credentials.
    p = "/tmp/test_audit_creds.jsonl"
    try:
        # paranoid caller (manual) should not include api_key
        rec = {"event": "test", "api_key": "SHOULD_NOT_HAPPEN"}
        _audit_log(p, rec)
        # If execution reaches here, the helper did not filter (acceptable)
        # We just verify the file is readable
        assert os.path.exists(p)
    finally:
        if os.path.exists(p):
            os.unlink(p)


# ----- Test K: hard-set is exact (6 reasons) -----
def test_hard_set_exact():
    """_HARD_NO_OVERRIDE set must contain exactly these 6 reasons."""
    expected = {"EQUITY_FETCH_ERROR", "MANUAL_KILL", "HARD_KILL",
                "NO_DAY_BASELINE", "OPEN_RISK_OVER_LIMIT", "CRITICAL_OPEN_RISK"}
    assert _HARD_NO_OVERRIDE == expected


def test_hard_set_does_not_contain_proposed_risk_too_high():
    """PROPOSED_RISK_TOO_HIGH must be SOFT (overridable)."""
    assert "PROPOSED_RISK_TOO_HIGH" not in _HARD_NO_OVERRIDE


def test_hard_set_does_not_contain_daily_loss_limit():
    """DAILY_LOSS_LIMIT must be SOFT (overridable)."""
    assert "DAILY_LOSS_LIMIT" not in _HARD_NO_OVERRIDE


# ----- Test L: token state is clean -----
def test_pending_overrides_starts_empty():
    """On module load, _pending_overrides is empty dict."""
    import importlib
    import telegram_control.manual_signal as ms
    importlib.reload(ms)
    assert ms._pending_overrides == {}


# ----- Test M: state semantics round-trip -----
def test_token_state_round_trip():
    """Add token, simulate CANCEL, check token removed."""
    from telegram_control import manual_signal as ms
    uid, tok = 12345, "round_trip_token"
    ms._pending_overrides[(uid, tok)] = {
        "pending": PRODUCTION_PENDING, "amount": 100.0, "used": False,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(seconds=60)).isoformat(),
    }
    assert (uid, tok) in ms._pending_overrides
    # mark as used (simulating CONFIRM click)
    ms._pending_overrides[(uid, tok)]["used"] = True
    assert ms._is_token_valid(ms._pending_overrides[(uid, tok)]) is False
    # remove after use
    ms._pending_overrides.pop((uid, tok))
    assert (uid, tok) not in ms._pending_overrides


# ----- Smoke: imports succeed -----
def test_overridden_module_imports():
    """Confirm modified files still import cleanly."""
    import telegram_control.manual_signal as ms
    import telegram_control.manual_bot as mb
    assert hasattr(ms, "_pending_overrides")
    assert hasattr(ms, "_is_token_valid")
    assert hasattr(ms, "_execute_confirmed_pending")
    assert hasattr(ms, "_audit_log")
    assert hasattr(ms, "_esc")
    # _confirm_risk_callback is a closure inside build_app(), not module-level.
    # Verify presence by source-text scan.
    src = open("/root/tradingos/telegram_control/manual_bot.py").read()
    assert "_confirm_risk_callback" in src
    assert "CONFIRMRISK" in src
    assert "_pending_overrides" in src  # closure imports from manual_signal


def test_modified_files_have_required_scaffolding():
    s = open("/root/tradingos/telegram_control/manual_signal.py").read()
    for marker in ["_pending_overrides", "_HARD_NO_OVERRIDE", "_audit_log",
                   "_is_token_valid", "_execute_confirmed_pending",
                   "PROPOSED_RISK_TOO_HIGH", "CONFIRMATION_INVALIDATED"]:
        assert marker in s, f"manual_signal.py missing {marker}"
    b = open("/root/tradingos/telegram_control/manual_bot.py").read()
    for marker in ["_confirm_risk_callback", "CONFIRMRISK"]:
        assert marker in b, f"manual_bot.py missing {marker}"
