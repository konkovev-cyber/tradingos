"""
Research Score Calculator — единый рейтинг стратегий.

Score = 0.25×PF + 0.20×Stability + 0.20×SampleSize + 0.15×DD + 0.10×WalkForward + 0.10×Simplicity

Usage:
  python -c "from research_score import score; print(score(...))"
"""

def compute(
    pf: float,
    monthly_pnls: list[float],
    trades: int,
    max_dd_pct: float,
    walk_forward_pf: float = 0.0,
    complexity: int = 3,  # 1-5, lower = simpler
) -> dict:
    """
    Compute Research Score (0-100).

    Args:
        pf: Profit Factor (gross)
        monthly_pnls: List of net PnL per month
        trades: Total number of trades
        max_dd_pct: Maximum drawdown percentage
        walk_forward_pf: Walk forward validation PF (0 if not tested)
        complexity: 1 (very simple) to 5 (very complex)
    """
    # PF score (0-25)
    pf_score = min(pf / 2.0, 1.0) * 25 if pf > 0 else 0

    # Stability score (0-20) — based on monthly consistency
    if monthly_pnls and len(monthly_pnls) >= 3:
        profitable_months = sum(1 for p in monthly_pnls if p > 0)
        stability = profitable_months / len(monthly_pnls)
        # Penalize for high variance
        avg = sum(monthly_pnls) / len(monthly_pnls)
        variance = sum((p - avg) ** 2 for p in monthly_pnls) / len(monthly_pnls)
        std = variance ** 0.5
        consistency = 1.0 - min(std / (abs(avg) + 0.001), 1.0) if avg != 0 else 0.3
        stability_score = (stability * 0.6 + consistency * 0.4) * 20
    else:
        stability_score = 0

    # Sample size score (0-20)
    if trades >= 1000:
        sample_score = 20
    elif trades >= 500:
        sample_score = 15
    elif trades >= 300:
        sample_score = 12
    elif trades >= 100:
        sample_score = 8
    elif trades >= 50:
        sample_score = 5
    else:
        sample_score = 0

    # Drawdown score (0-15)
    if max_dd_pct <= 5:
        dd_score = 15
    elif max_dd_pct <= 10:
        dd_score = 12
    elif max_dd_pct <= 15:
        dd_score = 8
    elif max_dd_pct <= 20:
        dd_score = 4
    else:
        dd_score = 0

    # Walk forward score (0-10)
    if walk_forward_pf > 0:
        wf_score = min(walk_forward_pf / 2.0, 1.0) * 10
    else:
        wf_score = 0

    # Simplicity score (0-10)
    simplicity_score = max(0, 10 - (complexity - 1) * 2.5)

    total = pf_score + stability_score + sample_score + dd_score + wf_score + simplicity_score

    return {
        "total": round(total, 1),
        "pf_score": round(pf_score, 1),
        "stability_score": round(stability_score, 1),
        "sample_score": round(sample_score, 1),
        "dd_score": round(dd_score, 1),
        "wf_score": round(wf_score, 1),
        "simplicity_score": round(simplicity_score, 1),
        "details": {
            "pf": pf,
            "profitable_months": f"{sum(1 for p in monthly_pnls if p > 0)}/{len(monthly_pnls)}" if monthly_pnls else "N/A",
            "trades": trades,
            "max_dd_pct": max_dd_pct,
            "walk_forward_pf": walk_forward_pf,
            "complexity": complexity,
        },
    }


def rank(strategies: list[dict]) -> list[dict]:
    """Rank multiple strategies by Research Score."""
    scored = []
    for s in strategies:
        result = compute(
            pf=s.get("pf", 0),
            monthly_pnls=s.get("monthly_pnls", []),
            trades=s.get("trades", 0),
            max_dd_pct=s.get("max_dd_pct", 100),
            walk_forward_pf=s.get("walk_forward_pf", 0),
            complexity=s.get("complexity", 3),
        )
        scored.append({**s, "score": result["total"], "score_detail": result})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


if __name__ == "__main__":
    # Demo
    result = compute(
        pf=1.35,
        monthly_pnls=[0.02, 0.01, -0.005, 0.03, 0.015, -0.01, 0.02, 0.025],
        trades=642,
        max_dd_pct=8.7,
        walk_forward_pf=1.22,
        complexity=2,
    )
    print(f"Research Score: {result['total']}/100")
    for k, v in result.items():
        if k != "details":
            print(f"  {k}: {v}")
