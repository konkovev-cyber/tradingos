"""
foundation_v1_scorer.py — Live scorer for foundation_v1 baselines.

Predicts forward returns at multiple horizons using zero-shot statistical
baselines (drift, theta, linear, ses, seasonal_naive). These are the academic
benchmarks that foundation models (Chronos, TimesFM) try to beat.

Output: dict of {model_name: predicted_return_at_horizon}.

Usage:
    from ml.foundation_v1.foundation_v1_scorer import score_signal
    preds = score_signal(closes_array)  # dict of {model: {horizon: ret_pct}}

For integration as a filter, use:
    from ml.foundation_v1.foundation_v1_scorer import should_take_by_drift
    take, drift_pred = should_take_by_drift(closes, threshold=0.005, horizon=4)
"""
from typing import Optional
import numpy as np

from .baselines import predict_returns


def score_signal(closes: np.ndarray, horizons: list[int] = [4, 16, 96],
                 lookback: int = 64) -> dict:
    """Run all foundation_v1 baselines on the close array.
    Returns {model: {horizon: predicted_return_pct}}.
    """
    if closes is None or len(closes) < 5:
        return {}
    return predict_returns(np.asarray(closes, dtype=np.float64), horizons, lookback)


def should_take_by_drift(closes: np.ndarray, threshold: float = 0.005,
                          horizon: int = 4, lookback: int = 64) -> tuple[bool, float]:
    """Drift-based filter.
    Default: take signal only if predicted 1h return (horizon=4 M15 bars) > threshold.
    Returns (take, predicted_return_pct).
    """
    if closes is None or len(closes) < 5:
        return False, 0.0
    preds = predict_returns(np.asarray(closes, dtype=np.float64), [horizon], lookback)
    drift_pred = preds.get("drift", {}).get(horizon, 0.0)
    return (drift_pred > threshold), float(drift_pred)
