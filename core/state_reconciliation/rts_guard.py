"""
core/state_reconciliation/rts_guard.py
RTS Guard v0 — Minimal runtime safety checks.

Checks:
  1. Missing SL detection
  2. Exchange reachability
  3. Balance verification

Read-only. No execution. Advisory only.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import PositionState

logger = logging.getLogger("tradingos.rts_guard")

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"


@dataclass
class RTSAlert:
    alert_type: str
    severity: str
    symbol: str
    message: str
    timestamp: str


def check_missing_protection(protection_states: List) -> List[RTSAlert]:
    """Check protection status using ProtectionState (orders-aware)."""
    alerts = []
    for ps in protection_states:
        if ps.unprotected:
            alerts.append(RTSAlert(
                alert_type="NO_PROTECTION",
                severity=SEVERITY_CRITICAL,
                symbol=ps.symbol,
                message=f"{ps.symbol} has NO SL and NO TP — completely unprotected",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
        elif not ps.has_tp:
            alerts.append(RTSAlert(
                alert_type="NO_TP",
                severity=SEVERITY_WARNING,
                symbol=ps.symbol,
                message=f"{ps.symbol} has SL={ps.sl_price} but NO TP",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
        elif not ps.has_sl:
            alerts.append(RTSAlert(
                alert_type="NO_SL",
                severity=SEVERITY_WARNING,
                symbol=ps.symbol,
                message=f"{ps.symbol} has TP={ps.tp_price} but NO SL",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
    return alerts


async def check_exchange_reachable() -> Optional[RTSAlert]:
    """Check if BingX API is reachable."""
    try:
        from control_plane.bingx_read.client import BingXReadClient
        client = BingXReadClient()
        try:
            positions = await client.get_positions()
            return None
        finally:
            await client.close()
    except Exception as e:
        return RTSAlert(
            alert_type="EXCHANGE_UNREACHABLE",
            severity=SEVERITY_CRITICAL,
            symbol="SYSTEM",
            message=f"Cannot reach BingX API: {e}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


def check_balance(balance: float, min_balance: float = 10.0) -> Optional[RTSAlert]:
    """Check if balance is above minimum threshold."""
    if balance < min_balance:
        return RTSAlert(
            alert_type="LOW_BALANCE",
            severity=SEVERITY_CRITICAL,
            symbol="ACCOUNT",
            message=f"Balance {balance:.2f} below minimum {min_balance:.2f}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    return None


async def run_checks(positions: List[PositionState], balance: float = 0.0) -> Dict:
    """Run all RTS Guard checks."""
    from core.state_reconciliation.protection_state import check_protection_state

    alerts = []

    # Check protection status (cross-references positions + orders)
    protection_states = await check_protection_state()
    alerts.extend(check_missing_protection(protection_states))

    # Check exchange reachability
    exchange_alert = await check_exchange_reachable()
    if exchange_alert:
        alerts.append(exchange_alert)

    # Check balance
    balance_alert = check_balance(balance)
    if balance_alert:
        alerts.append(balance_alert)

    critical = [a for a in alerts if a.severity == SEVERITY_CRITICAL]
    warnings = [a for a in alerts if a.severity == SEVERITY_WARNING]

    verdict = "BLOCK" if critical else "WARN" if warnings else "OK"

    return {
        "verdict": verdict,
        "alerts": alerts,
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def render_rts(result: Dict) -> str:
    icon = {"OK": "🟢", "WARN": "🟡", "BLOCK": "🔴"}.get(result["verdict"], "?")
    lines = [
        "=" * 64,
        "  RTS GUARD v0 — Runtime Safety Check",
        "=" * 64,
        f"  Verdict:     {icon} {result['verdict']}",
        f"  Critical:    {result['critical_count']}",
        f"  Warnings:    {result['warning_count']}",
        "",
    ]
    for a in result["alerts"]:
        sev_icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "⚪"}.get(a.severity, "?")
        lines.append(f"    {sev_icon} [{a.severity:8s}] {a.alert_type}: {a.message}")
    if not result["alerts"]:
        lines.append("    ✅ All checks passed")
    lines += [
        "",
        "  Rules:",
        "    Missing SL      → CRITICAL",
        "    Exchange down   → CRITICAL",
        "    Low balance     → CRITICAL",
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
