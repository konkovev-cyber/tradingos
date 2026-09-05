"""
control_plane/portfolio/engine.py
Portfolio Intelligence Layer — position-level portfolio analysis.

Read-only. No execution. No LIVE changes.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .adapter import PortfolioAdapter

STATE_PATH = Path("/root/tradingos/control_plane/snapshots/current_state.json")
REPORT_PATH = Path("/root/tradingos/control_plane/portfolio/portfolio_intelligence.json")


def load_positions_from_ubot() -> List[Dict]:
    """Load actual positions from ubot_bingx bot_state.db."""
    import sqlite3
    db = Path("/opt/ubot_bingx/bot_state.db")
    if not db.exists():
        return []
    try:
        uri = f"file:{db}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, side, entry_price, qty, pnl "
            "FROM trade_journal WHERE exit_price IS NULL"
        )
        positions = []
        for row in cur.fetchall():
            positions.append({
                "symbol": row[0],
                "side": row[1],
                "entry_price": float(row[2] or 0),
                "qty": float(row[3] or 0),
                "unrealized_pnl": float(row[4] or 0),
            })
        conn.close()
        return positions
    except Exception:
        return []


def analyze() -> Dict:
    positions = load_positions_from_ubot()
    adapter = PortfolioAdapter()
    analysis = adapter.analyze_portfolio(positions)

    # Build stress scenarios
    stress_scenarios = []
    total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

    # Scenario 1: Largest position goes to -20%
    biggest = analysis.get("concentration", {})
    biggest_pct = biggest.get("largest_pct", 0) / 100
    biggest_loss = total_pnl * biggest_pct * -0.2 if total_pnl > 0 else 0
    stress_scenarios.append({
        "scenario": f"{biggest.get('largest_symbol', '')} -20%",
        "impact": round(biggest_loss, 2),
        "description": f"If largest position drops 20%",
    })

    # Scenario 2: All positions hit -10%
    all_loss = total_pnl * -0.1
    stress_scenarios.append({
        "scenario": "All positions -10%",
        "impact": round(all_loss, 2),
        "description": "If every position drops 10%",
    })

    # Scenario 3: 3 worst positions close at loss
    sorted_pnl = sorted([p.get("unrealized_pnl", 0) for p in positions])
    worst_3 = sum(sorted_pnl[:3]) if len(sorted_pnl) >= 3 else sum(sorted_pnl)
    stress_scenarios.append({
        "scenario": "3 worst positions closed",
        "impact": round(worst_3, 2),
        "description": "If 3 worst positions hit stop loss",
    })

    # Health score (0-100)
    health = 50  # base
    conc = analysis.get("concentration", {})
    if conc.get("status") == "CRITICAL":
        health -= 25
    elif conc.get("status") == "HIGH":
        health -= 15

    win_rate = analysis.get("win_rate", 0.5)
    health += int((win_rate - 0.5) * 20)

    if total_pnl > 0:
        health += 10
    elif total_pnl < -10:
        health -= 10

    health = max(0, min(100, health))

    # Recommendations
    recs = []
    if conc.get("status") == "CRITICAL":
        recs.append("REDUCE_SINGLE_POSITION_CONCENTRATION")
    if win_rate < 0.4:
        recs.append("REVIEW_POSITION_QUALITY")
    if total_pnl < -20:
        recs.append("REVIEW_DRAWDOWN")
    if analysis.get("position_count", 0) > 15:
        recs.append("CONSIDER_REDUCE_EXPOSURE")
    if not recs:
        recs.append("MONITOR")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio_health": health,
        "analysis": analysis,
        "stress_scenarios": stress_scenarios,
        "recommendations": recs,
    }


def save_report(data: Dict) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_PATH


def render_text(data: Dict) -> str:
    health = data.get("portfolio_health", 0)
    analysis = data.get("analysis", {})
    conc = analysis.get("concentration", {})
    stress = data.get("stress_scenarios", [])
    recs = data.get("recommendations", [])

    health_icon = "🟢" if health >= 70 else "🟡" if health >= 40 else "🔴"
    conc_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "OK": "🟢"}.get(conc.get("status", ""), "⚪")

    lines = [
        "=" * 64,
        "  TRADINGOS — PORTFOLIO INTELLIGENCE v1",
        "=" * 64,
        f"  Timestamp: {data.get('timestamp', '')}",
        "",
        "  ── Portfolio Health ──",
        f"  Score:       {health_icon} {health}/100",
        "",
        "  ── Exposure ──",
        f"  Total:       {analysis.get('total_exposure', 0):.4f} USDT",
        f"  Long:        {analysis.get('long_exposure', 0):+.4f} ({analysis.get('long_count', 0)} positions)",
        f"  Short:       {analysis.get('short_exposure', 0):+.4f} ({analysis.get('short_count', 0)} positions)",
        f"  Win rate:    {analysis.get('win_rate', 0):.1%}",
        "",
        "  ── Concentration ──",
        f"  Largest:     {conc_icon} {conc.get('largest_symbol', '')} ({conc.get('largest_pct', 0)}%)",
        f"  Status:      {conc.get('status', 'UNKNOWN')}",
        "",
    ]
    if analysis.get("lab_diversification") is not None:
        lines += [
            "  ── Lab Analysis ──",
            f"  Diversification: {analysis.get('lab_diversification', 0):.2f}",
            f"  Expected PF:     {analysis.get('lab_expected_pf', 0):.2f}",
            f"  Expected DD:     {analysis.get('lab_expected_dd', 0):.2f}",
            "",
        ]
    if stress:
        lines.append("  ── Stress Scenarios ──")
        for s in stress:
            impact_icon = "🔴" if s["impact"] < -20 else "🟡" if s["impact"] < 0 else "🟢"
            lines.append(f"    {impact_icon} {s['scenario']:30s} {s['impact']:+.2f} USDT")
        lines.append("")
    lines.append("  ── Recommendations ──")
    for r in recs:
        lines.append(f"    → {r}")
    lines += [
        "",
        "  Note: Advisory only. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
