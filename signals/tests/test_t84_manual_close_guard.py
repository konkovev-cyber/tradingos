"""Regression tests for T84 manual-close guard (AUTO ladder protection).

T58 proved: the three biggest 'MFE losses' (TQQQ/TSLA/META 08-13) were caused by
operator manual closes of TIGHT-armed AUTO positions near-flat, not by Guardian.
Rule: once BE/PARTIAL/TIGHT fired on an AUTO position, manual close is BLOCKED
except explicit panic/emergency reasons. MANUAL positions are always closable.

These tests pin the invariant so the guard cannot regress.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))

import pytest

from guardian.reality_guardian import manual_close_allowed

STATE = ROOT / "guardian/reality_state.json"
BACKUP = Path("/tmp/reality_state_backup_t84.json")


@pytest.fixture()
def fake_state():
    if STATE.exists():
        shutil.copy(STATE, BACKUP)
    test_state = {
        "TESTAUTO_BE": {"source": "AUTO", "be_fired": True,
                        "partial_fired": False, "tight_fired": False},
        "TESTAUTO_FULL": {"source": "AUTO", "be_fired": True,
                          "partial_fired": True, "tight_fired": True},
        "TESTAUTO_RAW": {"source": "AUTO", "be_fired": False,
                         "partial_fired": False, "tight_fired": False},
        "TESTMAN": {"source": "MANUAL", "be_fired": True,
                    "partial_fired": True, "tight_fired": True},
        "TESTSRC_UNKNOWN": {"be_fired": True, "partial_fired": False,
                            "tight_fired": False},
    }
    STATE.write_text(json.dumps(test_state))
    yield
    if BACKUP.exists():
        shutil.copy(BACKUP, STATE)


def test_auto_with_be_armed_blocked(fake_state):
    ok, msg = manual_close_allowed("TESTAUTO_BE", "MANUAL")
    assert ok is False
    assert "MANUAL_CLOSE_BLOCKED" in msg


def test_auto_fully_armed_blocked(fake_state):
    ok, msg = manual_close_allowed("TESTAUTO_FULL", "MANUAL")
    assert ok is False
    assert "MANUAL_CLOSE_BLOCKED" in msg


def test_auto_unarmed_allowed(fake_state):
    ok, _ = manual_close_allowed("TESTAUTO_RAW", "MANUAL")
    assert ok is True


def test_panic_allowed_even_when_armed(fake_state):
    for reason in ("PANIC", "EXCHANGE_FAILURE", "EMERGENCY", "TECHNICAL", "panic", "emergency"):
        ok, _ = manual_close_allowed("TESTAUTO_FULL", reason)
        assert ok is True, f"{reason} should be allowed"


def test_manual_position_always_allowed(fake_state):
    ok, _ = manual_close_allowed("TESTMAN", "MANUAL")
    assert ok is True


def test_unknown_source_defaults_to_blocked_when_armed(fake_state):
    """No source field + ladder armed → treat as AUTO (block), fail-closed."""
    ok, _ = manual_close_allowed("TESTSRC_UNKNOWN", "MANUAL")
    assert ok is False


def test_guard_present_in_production_close_path():
    """bot.py close_yes_ path must call manual_close_allowed before _close_position."""
    src = (ROOT / "telegram_control/bot.py").read_text()
    assert "manual_close_allowed" in src
    assert "_close_position" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
