"""
Walk Forward Validator — проверяет стратегию на независимых периодах.

Разбивает историю на train/test окна.
Стратегия отвергается если PF на validation < 1.0 или < 50% окон прибыльны.

Usage:
  python -c "from walk_forward import validate; print(validate(trades_by_month))"
"""

from collections import defaultdict


def validate(
    trades: list[dict],
    window_months: int = 6,
    step_months: int = 3,
    min_pf: float = 1.0,
    min_profitable_windows: float = 0.4,
) -> dict:
    """
    Walk Forward validation.

    Args:
        trades: List of trade dicts with 'month' (str 'YYYY-MM') and 'net_pnl' (float)
        window_months: Training window size
        step_months: Step between windows
        min_pf: Minimum PF on validation to pass
        min_profitable_windows: Minimum fraction of profitable windows

    Returns:
        dict with results
    """
    if not trades:
        return {"status": "no_data", "windows": 0, "passed": False}

    # Group trades by month
    by_month = defaultdict(list)
    for t in trades:
        by_month[t["month"]].append(t)

    months = sorted(by_month.keys())
    if len(months) < window_months + step_months:
        return {
            "status": "insufficient_data",
            "months": len(months),
            "needed": window_months + step_months,
            "passed": False,
        }

    windows = []
    for i in range(0, len(months) - window_months - step_months + 1, step_months):
        train_months = months[i : i + window_months]
        test_months = months[i + window_months : i + window_months + step_months]

        train_trades = [t for m in train_months for t in by_month[m]]
        test_trades = [t for m in test_months for t in by_month[m]]

        if not train_trades or not test_trades:
            continue

        train_pnl = sum(t["net_pnl"] for t in train_trades)
        test_pnl = sum(t["net_pnl"] for t in test_trades)
        train_loss = sum(abs(t["net_pnl"]) for t in train_trades if t["net_pnl"] < 0)
        test_loss = sum(abs(t["net_pnl"]) for t in test_trades if t["net_pnl"] < 0)

        train_pf = (train_pnl + train_loss) / max(train_loss, 0.0001)
        test_pf = (test_pnl + test_loss) / max(test_loss, 0.0001)

        windows.append({
            "train": f"{train_months[0]} to {train_months[-1]}",
            "test": f"{test_months[0]} to {test_months[-1]}",
            "train_trades": len(train_trades),
            "test_trades": len(test_trades),
            "train_pf": round(train_pf, 3),
            "test_pf": round(test_pf, 3),
            "train_pnl": round(train_pnl, 6),
            "test_pnl": round(test_pnl, 6),
        })

    if not windows:
        return {"status": "no_windows", "windows": 0, "passed": False}

    avg_train_pf = sum(w["train_pf"] for w in windows) / len(windows)
    avg_test_pf = sum(w["test_pf"] for w in windows) / len(windows)
    profitable_windows = sum(1 for w in windows if w["test_pf"] >= min_pf)
    profitable_ratio = profitable_windows / len(windows)

    passed = avg_test_pf >= min_pf and profitable_ratio >= min_profitable_windows

    return {
        "status": "complete",
        "windows": len(windows),
        "avg_train_pf": round(avg_train_pf, 3),
        "avg_test_pf": round(avg_test_pf, 3),
        "profitable_windows": f"{profitable_windows}/{len(windows)}",
        "profitable_ratio": round(profitable_ratio, 3),
        "passed": passed,
        "details": windows,
    }
