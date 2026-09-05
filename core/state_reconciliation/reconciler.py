"""
core/state_reconciliation/reconciler.py
State Reconciliation Engine v1 — compares expected vs actual position state.

Read-only. No execution. No LIVE changes.
Detects: side reversals, qty changes, entry changes, SL/TP changes, position opened/closed.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .models import (
    FieldChange,
    PositionChangeType,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
)

logger = logging.getLogger("tradingos.reconciliation")


def reconcile_positions(
    expected: List[PositionState],
    actual: List[PositionState],
) -> List[ReconciliationResult]:
    """
    Compare expected positions with actual exchange state.
    Returns list of ReconciliationResult for each position.
    """
    now = datetime.now(timezone.utc).isoformat()
    expected_map = {p.symbol: p for p in expected}
    actual_map = {p.symbol: p for p in actual}
    all_symbols = set(expected_map.keys()) | set(actual_map.keys())
    results = []

    for symbol in sorted(all_symbols):
        exp = expected_map.get(symbol)
        act = actual_map.get(symbol)

        if exp is None and act is not None:
            # Position exists on exchange but NOT in TradingOS
            results.append(ReconciliationResult(
                symbol=symbol,
                status=ReconciliationStatus.POSITION_OPENED,
                expected=None,
                actual=act,
                changes=[FieldChange("position", None, "EXISTS_ON_EXCHANGE", PositionChangeType.POSITION_OPENED)],
                timestamp=now,
            ))
            logger.info(f"RECONCILER: {symbol} OPENED on exchange (not in TradingOS)")
            continue

        if exp is not None and act is None:
            # Position in TradingOS but NOT on exchange
            results.append(ReconciliationResult(
                symbol=symbol,
                status=ReconciliationStatus.POSITION_CLOSED,
                expected=exp,
                actual=None,
                changes=[FieldChange("position", "EXISTS_IN_TRADINGOS", None, PositionChangeType.POSITION_CLOSED)],
                timestamp=now,
            ))
            logger.info(f"RECONCILER: {symbol} CLOSED on exchange (still in TradingOS)")
            continue

        if exp is None and act is None:
            continue

        # Both exist — compare fields
        changes = compare_fields(exp, act)
        if changes:
            # Check if side was reversed
            side_reversed = any(c.change_type == PositionChangeType.SIDE_REVERSED for c in changes)
            status = ReconciliationStatus.CONFLICT if side_reversed else ReconciliationStatus.DRIFT
            results.append(ReconciliationResult(
                symbol=symbol,
                status=status,
                expected=exp,
                actual=act,
                changes=changes,
                timestamp=now,
            ))
            logger.info(f"RECONCILER: {symbol} {status.value} — {len(changes)} field(s) changed")
        else:
            results.append(ReconciliationResult(
                symbol=symbol,
                status=ReconciliationStatus.SYNCED,
                expected=exp,
                actual=act,
                timestamp=now,
            ))

    return results


def compare_fields(expected: PositionState, actual: PositionState) -> List[FieldChange]:
    """Compare two PositionState objects field by field."""
    changes = []

    if expected.side != actual.side:
        changes.append(FieldChange("side", expected.side, actual.side, PositionChangeType.SIDE_REVERSED))

    if abs(expected.qty - actual.qty) > 0.001:
        changes.append(FieldChange("qty", expected.qty, actual.qty, PositionChangeType.QTY_CHANGED))

    if abs(expected.entry_price - actual.entry_price) > 0.0001:
        changes.append(FieldChange("entry_price", expected.entry_price, actual.entry_price, PositionChangeType.ENTRY_CHANGED))

    if expected.stop_loss != actual.stop_loss:
        changes.append(FieldChange("stop_loss", expected.stop_loss, actual.stop_loss, PositionChangeType.SL_CHANGED))

    if expected.take_profit != actual.take_profit:
        changes.append(FieldChange("take_profit", expected.take_profit, actual.take_profit, PositionChangeType.TP_CHANGED))

    if expected.leverage != actual.leverage:
        changes.append(FieldChange("leverage", expected.leverage, actual.leverage, PositionChangeType.LEVERAGE_CHANGED))

    return changes


def classify_changes(results: List[ReconciliationResult]) -> Dict[str, List[ReconciliationResult]]:
    """Group results by status for easier processing."""
    grouped: Dict[str, List[ReconciliationResult]] = {}
    for r in results:
        key = r.status.value
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(r)
    return grouped


def render_results(results: List[ReconciliationResult]) -> str:
    """Render reconciliation results as text."""
    lines = [
        "=" * 64,
        "  STATE RECONCILIATION v1",
        "=" * 64,
        f"  Timestamp: {datetime.now(timezone.utc).isoformat()}",
        f"  Positions checked: {len(results)}",
        "",
    ]

    grouped = classify_changes(results)
    for status, items in grouped.items():
        icon = {
            "SYNCED": "✅",
            "DRIFT": "⚠️",
            "MISSING": "❌",
            "CONFLICT": "🔴",
            "POSITION_OPENED": "🆕",
            "POSITION_CLOSED": "🗑️",
        }.get(status, "?")
        lines.append(f"  {icon} {status}: {len(items)}")
        for r in items:
            if r.changes:
                for c in r.changes:
                    lines.append(f"    {r.symbol}: {c.field_name} {c.expected} → {c.actual} ({c.change_type.value})")
            else:
                lines.append(f"    {r.symbol}: OK")
        lines.append("")

    return "\n".join(lines)
