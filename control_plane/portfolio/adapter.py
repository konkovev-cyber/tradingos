"""
control_plane/portfolio/adapter.py
Portfolio Intelligence Adapter — bridges Control State to portfolio analysis.

Read-only. No LIVE changes.
"""
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path("/root/tradingos_lab")))
try:
    from portfolio_engine import PortfolioEngine, EdgeCandidate
    from portfolio_engine.stress import StressTestEngine
    LAB_AVAILABLE = True
except ImportError:
    LAB_AVAILABLE = False


class PortfolioAdapter:
    """Read-only bridge between Control State and portfolio analysis."""

    def __init__(self):
        if LAB_AVAILABLE:
            self._engine = PortfolioEngine()
            self._stress = StressTestEngine()
        else:
            self._engine = None
            self._stress = None

    def positions_to_edges(self, positions: List[Dict]) -> list:
        """Convert position data to EdgeCandidate format for lab engine."""
        edges = []
        for p in positions:
            pnl = p.get("unrealized_pnl", 0)
            edge = EdgeCandidate(
                trades=1,
                win_rate=1.0 if pnl > 0 else 0.0,
            )
            edges.append(edge)
        return edges

    def analyze_portfolio(self, positions: List[Dict]) -> Dict:
        """Run portfolio analysis using lab engine + position-specific metrics."""
        if not positions:
            return {"total_exposure": 0, "health": "NO_DATA"}

        # Position-specific metrics (not from lab)
        total_abs = sum(abs(p.get("unrealized_pnl", 0)) for p in positions)
        long_exposure = sum(p.get("unrealized_pnl", 0) for p in positions if p.get("unrealized_pnl", 0) > 0)
        short_exposure = sum(p.get("unrealized_pnl", 0) for p in positions if p.get("unrealized_pnl", 0) < 0)

        by_sym = {}
        for p in positions:
            sym = p.get("symbol", "UNKNOWN")
            by_sym[sym] = by_sym.get(sym, 0) + abs(p.get("unrealized_pnl", 0))

        biggest_sym = max(by_sym, key=by_sym.get) if by_sym else ""
        biggest_pct = (by_sym.get(biggest_sym, 0) / total_abs * 100) if total_abs else 0

        win_count = sum(1 for p in positions if p.get("unrealized_pnl", 0) > 0)
        win_rate = win_count / len(positions) if positions else 0

        # Lab engine analysis (if available)
        lab_report = None
        if self._engine:
            try:
                edges = self.positions_to_edges(positions)
                if edges:
                    lab_report = self._engine.analyze(edges)
            except Exception:
                pass

        return {
            "total_exposure": round(total_abs, 4),
            "long_exposure": round(long_exposure, 4),
            "short_exposure": round(short_exposure, 4),
            "long_count": sum(1 for p in positions if p.get("unrealized_pnl", 0) > 0),
            "short_count": sum(1 for p in positions if p.get("unrealized_pnl", 0) < 0),
            "position_count": len(positions),
            "win_rate": round(win_rate, 3),
            "concentration": {
                "largest_symbol": biggest_sym,
                "largest_pct": round(biggest_pct, 1),
                "status": "CRITICAL" if biggest_pct > 70 else "HIGH" if biggest_pct > 50 else "OK",
            },
            "lab_diversification": lab_report.diversification_score if lab_report else None,
            "lab_expected_pf": lab_report.expected_pf if lab_report else None,
            "lab_expected_dd": lab_report.expected_dd if lab_report else None,
        }
