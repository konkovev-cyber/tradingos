"""
core/order_dedup.py — persistent signal-level dedup for order execution.

Prevents the same signal (decision_id) from producing two real orders
across service restarts, network retries, or loop re-entries.

Bug (found 2026-08-24): _execute_reality had only a 120s throttle —
if a signal re-entered after 121s (restart, retry), it would produce
a second real order. No idempotency key, no dedup store.

Fix: a JSONL append-only log of executed decision_ids. Before each
order, check if the decision_id was already executed. The log survives
restarts (persisted to disk).

Usage in trade_executor:
    from core.order_dedup import is_already_executed, mark_executed
    if is_already_executed(proposal.decision_id):
        return {"status": "BLOCKED", "error": "duplicate signal"}
    # ... submit order ...
    mark_executed(proposal.decision_id, symbol, side, ticket)
"""
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("tradingos.order_dedup")

_DEDUP_LOG = Path("/root/tradingos/logs/executed_decisions.jsonl")
_RETENTION_HOURS = 48  # dedup window: 48h is enough to cover restarts/retries


def _ensure_log():
    _DEDUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not _DEDUP_LOG.exists():
        _DEDUP_LOG.touch()


def is_already_executed(decision_id: str) -> bool:
    """Check if this decision_id was already executed within the retention window.

    Returns True if a matching entry exists in the dedup log that is
    younger than _RETENTION_HOURS. False otherwise (including missing
    or empty log, or unreadable entries — fail-open for availability,
    the kill_switch and throttle provide the outer safety net).
    """
    if not decision_id:
        return False  # no ID = can't dedup, allow (throttle guards)
    try:
        _ensure_log()
        cutoff = time.time() - _RETENTION_HOURS * 3600
        lines = _DEDUP_LOG.read_text().splitlines()
        # Read in reverse — most recent first
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("decision_id") == decision_id:
                ts = rec.get("ts", 0)
                if ts >= cutoff:
                    return True
                # Old entry beyond retention — not a duplicate
                return False
        return False
    except Exception:
        return False


def mark_executed(decision_id: str, symbol: str = "", side: str = "",
                  ticket: str = "", extra: dict = None) -> None:
    """Record that this decision_id was executed (order submitted + filled)."""
    if not decision_id:
        return  # can't dedup without ID
    try:
        _ensure_log()
        record = {
            "decision_id": decision_id,
            "symbol": symbol,
            "side": side,
            "ticket": str(ticket),
            "ts": time.time(),
        }
        if extra:
            record.update(extra)
        with _DEDUP_LOG.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.warning(f"order_dedup mark_executed failed: {e}")