"""Tests for escalation shadow-audit in the MANUAL contour.

FAIL_DAYS_VS_ENTRIES.md (2026-08-16) found: 80% of MANUAL loss (-$3.03 of -$3.78)
came from escalation — increasing position size on a symbol whose previous
entry closed with a loss (UNI $5->$10->$50, NEAR $10->$50).

The audit is SHADOW/audit-only by design (user decision 16.08): it must NEVER
block or modify trading behaviour, only record observations. Same-or-smaller
re-entry must NOT be flagged (repeated entries are not proven bad).

These tests pin:
- escalation (amount up after a loss on the same symbol) -> flagged, journaled
- same-size re-entry after loss -> NOT flagged
- smaller re-entry after loss -> NOT flagged
- amount up but no loss on the symbol -> NOT flagged
- nothing is blocked (return value is a record or None, no exception)
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))

import pytest

from telegram_control import manual_signal as ms

TODAY = datetime.now(timezone.utc).date().isoformat()


def _ts(hhmm: str) -> str:
    return f"{TODAY}T{hhmm}:00+00:00"


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    """A temp journal containing: $5 entry -> loss close -> then nothing more."""
    jp = tmp_path / "manual_signals.jsonl"
    lines = [
        {"event": "EXECUTED", "ts": _ts("10:00"), "symbol": "UNIUSDT", "side": "SELL",
         "note": "order_id=x amount=$5.00 notional=$5.00"},
        {"event": "POSITION_CLOSED", "ts": _ts("12:00"), "symbol": "UNIUSDT",
         "side": "Sell", "entry": 4.0, "exit_px": 4.1, "pnl_usd_est": -0.25},
    ]
    jp.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return jp


def _run_audit(journal: Path, audit_path: Path, symbol: str, amount: float):
    """Run the real audit function with journal/audit paths pointed at temp files."""
    old_j, old_a = ms.JOURNAL, ms.ESCALATION_AUDIT
    ms.JOURNAL, ms.ESCALATION_AUDIT = journal, audit_path
    try:
        return ms._audit_escalation(symbol, amount)
    finally:
        ms.JOURNAL, ms.ESCALATION_AUDIT = old_j, old_a


def test_escalation_after_loss_is_flagged(tmp_path: Path, journal: Path):
    audit = tmp_path / "escalation_audit.jsonl"
    rec = _run_audit(journal, audit, "UNIUSDT", 10.0)
    assert rec is not None
    assert rec["symbol"] == "UNIUSDT"
    assert rec["prev_amount_usd"] == 5.0
    assert rec["new_amount_usd"] == 10.0
    assert rec["mode"] == "shadow"
    # journaled
    assert audit.exists()
    written = json.loads(audit.read_text().strip().splitlines()[-1])
    assert written["event"] == "ESCALATION_AUDIT"


def test_same_size_reenrty_not_flagged(tmp_path: Path, journal: Path):
    audit = tmp_path / "escalation_audit.jsonl"
    assert _run_audit(journal, audit, "UNIUSDT", 5.0) is None
    assert not audit.exists()


def test_smaller_reenrty_not_flagged(tmp_path: Path, journal: Path):
    audit = tmp_path / "escalation_audit.jsonl"
    assert _run_audit(journal, audit, "UNIUSDT", 2.0) is None
    assert not audit.exists()


def test_amount_up_without_loss_not_flagged(tmp_path: Path):
    jp = tmp_path / "manual_signals.jsonl"
    jp.write_text(json.dumps({
        "event": "EXECUTED", "ts": _ts("10:00"), "symbol": "UNIUSDT",
        "note": "order_id=x amount=$5.00 notional=$5.00"}) + "\n")
    audit = tmp_path / "escalation_audit.jsonl"
    assert _run_audit(jp, audit, "UNIUSDT", 10.0) is None
    assert not audit.exists()


def test_loss_but_no_prior_entry_not_flagged(tmp_path: Path):
    jp = tmp_path / "manual_signals.jsonl"
    jp.write_text(json.dumps({
        "event": "POSITION_CLOSED", "ts": _ts("12:00"), "symbol": "UNIUSDT",
        "pnl_usd_est": -0.25}) + "\n")
    audit = tmp_path / "escalation_audit.jsonl"
    assert _run_audit(jp, audit, "UNIUSDT", 10.0) is None


def test_missing_journal_returns_none(tmp_path: Path):
    audit = tmp_path / "escalation_audit.jsonl"
    assert _run_audit(tmp_path / "nonexistent.jsonl", audit, "UNIUSDT", 10.0) is None
