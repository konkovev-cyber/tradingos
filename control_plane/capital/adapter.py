"""
control_plane/capital/adapter.py
Capital Engine Adapter — bridges Control State to lab CapitalEngine.

Read-only. No LIVE changes. No position management.
"""
import sys
from pathlib import Path
from typing import Dict, Optional

# Import lab capital_engine
sys.path.insert(0, str(Path("/root/tradingos_lab")))
try:
    from capital_engine import CapitalEngine, CapitalScore, CapitalScoreV2
    LAB_AVAILABLE = True
except ImportError:
    LAB_AVAILABLE = False


class CapitalEngineAdapter:
    """Read-only bridge between Control State and lab CapitalEngine."""

    def __init__(self):
        if LAB_AVAILABLE:
            self._engine = CapitalEngine()
        else:
            self._engine = None

    def score_risk_from_state(self, capital: Dict) -> Optional[float]:
        """Use CapitalEngine.score_risk for risk assessment."""
        if not self._engine:
            return None
        try:
            unrealized = capital.get("total_unrealized", 0)
            biggest_pct = capital.get("biggest_risk_pct", 0)
            open_pos = capital.get("open_positions", 0)

            max_dd = abs(unrealized) / max(open_pos * 10, 1) * 100
            protection_events = 1 if biggest_pct > 70 else 0

            return self._engine.score_risk(
                max_dd=max_dd,
                protection_events=protection_events,
                kill_switch_active=False,
            )
        except Exception:
            return None

    def score_stability_from_state(self, execution: Dict) -> Optional[float]:
        """Use CapitalEngine.score_stability for service health."""
        if not self._engine:
            return None
        try:
            running = execution.get("services_running", 0)
            total = execution.get("services_total", 1)
            uptime_pct = running / max(total, 1) * 100

            return self._engine.score_stability(
                uptime_pct=uptime_pct,
                restarts=0,
                data_integrity=True,
            )
        except Exception:
            return None

    def score_v2_from_state(self, state: Dict) -> Optional[CapitalScoreV2]:
        """Use CapitalEngine.score_v2 for comprehensive scoring."""
        if not self._engine:
            return None
        try:
            capital = state.get("capital", {})
            execution = state.get("execution", {})
            positions = state.get("positions", {})

            unrealized = capital.get("total_unrealized", 0)
            open_pos = capital.get("open_positions", 0)
            biggest_pct = capital.get("biggest_risk_pct", 0)

            max_dd = abs(unrealized) / max(open_pos * 10, 1) * 100

            return self._engine.score_v2(
                pf=1.0 + (unrealized / 100) if unrealized > 0 else 0.95,
                win_rate=0.5,
                trades=open_pos,
                robustness=50.0,
                max_dd=max_dd,
                protection_events=1 if biggest_pct > 70 else 0,
            )
        except Exception:
            return None
