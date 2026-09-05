"""
execution/mt5/guardian.py
MT5 Position Guardian — applies TradingOS protection rules to MT5 positions.

Phase 1: Read-Only (observation, no modifications)
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .schemas import MT5Position, MT5Snapshot, protection_status
from .adapter import MT5Adapter

logger = logging.getLogger("mt5.guardian")


class MT5Guardian:
    """
    MT5 Position Guardian — mirrors TradingOS RTS Governor for Forex.
    Phase 1: observation only.
    """

    def __init__(self, adapter: MT5Adapter):
        self.adapter = adapter
        self._risk_state = "NORMAL"

    def evaluate_protection(self, position: MT5Position) -> Dict:
        """Check protection status for one position."""
        status = protection_status(position.sl, position.tp)
        alerts = []

        if status == "UNPROTECTED":
            alerts.append("CRITICAL: position without SL or TP")
        elif status == "SL_ONLY":
            alerts.append("WARNING: TP missing")
        elif status == "TP_ONLY":
            alerts.append("WARNING: SL missing")

        return {
            "symbol": position.symbol,
            "ticket": position.ticket,
            "protection": status,
            "has_sl": position.sl is not None,
            "has_tp": position.tp is not None,
            "sl": position.sl,
            "tp": position.tp,
            "alerts": alerts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def evaluate_risk(self, snapshot: MT5Snapshot) -> Dict:
        """Check account-level risk."""
        if not snapshot.account:
            return {"risk_state": "UNKNOWN"}

        equity = snapshot.account.equity
        balance = snapshot.account.balance
        dd_pct = ((balance - equity) / balance * 100) if balance > 0 else 0

        if dd_pct > 5:
            risk_state = "EMERGENCY"
        elif dd_pct > 3:
            risk_state = "DEFENSIVE"
        elif dd_pct > 1:
            risk_state = "WARNING"
        else:
            risk_state = "NORMAL"

        # Check per-position risk
        high_risk_positions = []
        for pos in snapshot.positions:
            pos_risk = abs(pos.profit) / balance * 100 if balance > 0 else 0
            if pos_risk > 0.5:
                high_risk_positions.append(pos.symbol)

        return {
            "risk_state": risk_state,
            "drawdown_pct": dd_pct,
            "high_risk_positions": high_risk_positions,
            "position_count": len(snapshot.positions),
        }

    def full_evaluation(self) -> Dict:
        """Run full evaluation on current MT5 state."""
        if not self.adapter.last_snapshot:
            return {"error": "No MT5 snapshot available", "verdict": "BLOCK"}

        snapshot = self.adapter.last_snapshot
        risk = self.evaluate_risk(snapshot)

        position_reports = []
        for pos in snapshot.positions:
            prot = self.evaluate_protection(pos)
            position_reports.append(prot)

        # Determine overall verdict
        criticals = [p for p in position_reports if "CRITICAL" in str(p.get("alerts", []))]
        warnings = [p for p in position_reports if "WARNING" in str(p.get("alerts", []))]

        if risk["risk_state"] == "EMERGENCY" or criticals:
            verdict = "BLOCK"
        elif risk["risk_state"] == "DEFENSIVE" or warnings:
            verdict = "LIMITED"
        else:
            verdict = "ALLOW"

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "risk_state": risk["risk_state"],
            "positions": position_reports,
            "drawdown_pct": risk["drawdown_pct"],
            "critical_count": len(criticals),
            "warning_count": len(warnings),
        }

    def render_status(self, evaluation: Dict) -> str:
        """Render guardian status for logging."""
        icon = {"ALLOW": "🟢", "LIMITED": "🟡", "BLOCK": "🔴"}.get(evaluation.get("verdict", "BLOCK"), "?")
        lines = [
            f"\n{'='*50}",
            f"  MT5 GUARDIAN v0.1 — {icon} {evaluation.get('verdict', 'UNKNOWN')}",
            f"{'='*50}",
            f"  Risk: {evaluation.get('risk_state', '?')} | DD: {evaluation.get('drawdown_pct', 0):.2f}%",
            f"  Positions: {len(evaluation.get('positions', []))}",
        ]
        for p in evaluation.get("positions", []):
            prot_icon = "🟢" if p["protection"] == "FULLY_PROTECTED" else "🔴"
            alerts = "; ".join(p.get("alerts", []))
            lines.append(f"  {prot_icon} {p['symbol']:8s} | {p['protection']:16s} | SL={'✅' if p['has_sl'] else '❌'} TP={'✅' if p['has_tp'] else '❌'} {alerts}")
        if evaluation.get("critical_count", 0) > 0 or evaluation.get("warning_count", 0) > 0:
            lines.append(f"\n  ⚠️  {evaluation.get('critical_count', 0)} critical, {evaluation.get('warning_count', 0)} warnings")
        lines.append(f"{'='*50}")
        return "\n".join(lines)
