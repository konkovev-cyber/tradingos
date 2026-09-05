"""
Monte Carlo Simulator — проверяет устойчивость стратегии к случайности.

Перемешивает сделки 10000 раз, оценивает вероятность разорения.

Usage:
  python -c "from monte_carlo import simulate; print(simulate(trades))"
"""

import random
from typing import Optional


def simulate(
    trades: list[dict],
    simulations: int = 10000,
    initial_capital: float = 10000.0,
    risk_per_trade: float = 0.01,
) -> dict:
    """
    Monte Carlo simulation of trade sequence.

    Args:
        trades: List of trade dicts with 'net_pnl' (float) and 'pnl_pct' (float)
        simulations: Number of shuffled sequences to run
        initial_capital: Starting capital
        risk_per_trade: Fraction of capital risked per trade

    Returns:
        dict with simulation results
    """
    if not trades:
        return {"status": "no_data", "simulations": 0}

    pnls = [t["net_pnl"] for t in trades]
    pnl_pcts = [t.get("pnl_pct", t["net_pnl"] / initial_capital) for t in trades]

    results = []
    for _ in range(simulations):
        random.shuffle(pnls)
        capital = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for pnl in pnls:
            capital += pnl
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100
            if dd > max_dd:
                max_dd = dd
        results.append({
            "final_capital": capital,
            "max_dd_pct": max_dd,
            "ruined": capital < initial_capital * 0.5,
        })

    final_capitals = [r["final_capital"] for r in results]
    max_dds = [r["max_dd_pct"] for r in results]
    ruined = sum(1 for r in results if r["ruined"])

    return {
        "status": "complete",
        "simulations": simulations,
        "trades_used": len(trades),
        "avg_final_capital": round(sum(final_capitals) / simulations, 2),
        "median_final_capital": round(sorted(final_capitals)[simulations // 2], 2),
        "min_final_capital": round(min(final_capitals), 2),
        "max_final_capital": round(max(final_capitals), 2),
        "avg_max_dd_pct": round(sum(max_dds) / simulations, 2),
        "median_max_dd_pct": round(sorted(max_dds)[simulations // 2], 2),
        "worst_max_dd_pct": round(max(max_dds), 2),
        "ruin_probability_pct": round(ruined / simulations * 100, 2),
        "passed": ruined / simulations < 0.05,
    }
