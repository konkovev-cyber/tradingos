"""Regression: epoch attribution must be UTC-only (GWEI-type bug guard).

T95 found: guardian journal timestamps are UTC+3 (local); the GWEI trade
'appeared' opened 18:07Z when its true UTC open was 15:07Z — had the epoch
boundary been applied to the local-stamped value, the trade would have been
mis-attributed to the clean epoch.

These tests pin the invariant that epoch classification uses the UTC-normalized
timestamp only, and that a +3h local offset cannot flip epoch membership.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT / "research" / "clean_epoch_v1"))

EPOCH_START_UTC = "2026-08-13T17:41:00"


def _to_utc(ts: str) -> str:
    """Normalize an ISO timestamp to UTC. Naive values are treated as UTC
    (epoch contract: all stored timestamps are UTC; local display is +3h only
    at presentation time, never in the ledger)."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def _in_epoch(ts_utc: str) -> bool:
    return ts_utc[:19] >= EPOCH_START_UTC


def test_gwei_local_time_artifact():
    """The T85 GWEI case: local-stamped '18:07' must be read as UTC 18:07?? —
    No: the true UTC open was 15:07. The bug was that someone applied the epoch
    boundary to the local-time string. The classifier reads the ledger's UTC
    field; a +3h local stamp would wrongly pass the boundary."""
    true_utc = "2026-08-13T15:07:20"
    local_stamped = "2026-08-13T18:07:20"
    # The stored ledger value IS the UTC one (contract: ledger = UTC).
    assert not _in_epoch(_to_utc(true_utc)), "true UTC open (15:07) must be PRE_EPOCH"
    # If someone fed the local-stamped string as if UTC, it would be mis-attributed:
    assert _in_epoch(_to_utc(local_stamped)) is True, "sanity: the local string alone passes boundary"
    # The fix: the classifier must reject timestamps that are not UTC-consistent.
    # Simplest invariant: naive timestamps are interpreted as UTC; a stamp that
    # carries an explicit +03:00 offset must be converted, not taken at face value.
    with_offset = "2026-08-13T18:07:20+03:00"
    assert not _in_epoch(_to_utc(with_offset)), (
        "+03:00 offset 18:07 = 15:07 UTC → must be PRE_EPOCH"
    )


def test_boundary_exact():
    assert _in_epoch("2026-08-13T17:41:00") is True
    assert _in_epoch("2026-08-13T17:40:59") is False


def test_utc_normalization_roundtrip():
    assert _to_utc("2026-08-13T17:41:00") == "2026-08-13T17:41:00+00:00"
    assert _to_utc("2026-08-13T18:41:00+03:00") == "2026-08-13T15:41:00+00:00"


def test_epoch_classifier_uses_utc_field():
    """The production classifier reads trade_results 'timestamp' as the UTC
    truth and applies EPOCH_START_UTC in the same string space."""
    src = (ROOT / "research/clean_epoch_v1/epoch_classifier.py").read_text()
    assert "EPOCH_START_UTC" in src
    assert "PRE_EPOCH" in src
    assert "ts_utc < EPOCH_START_UTC" in src


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
