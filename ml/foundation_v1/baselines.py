"""
foundation_v1_baselines.py — Lightweight zero-shot forecasters.

These are the academic benchmarks for time-series foundation models (Chronos,
TimesFM, Lag-Llama beat them by ~10-15% on MASE — not worth 526MB torch
download on this CPU-only box).

Models:
1. Drift: predicted_y = last_y + (last_y - first_y)/n  (random walk with drift)
2. SeasonalNaive: predicted_y = y[t - season_length]
3. SES: Simple Exponential Smoothing (alpha fit)
4. Theta: Theta method (Assimakopoulos & Nikolopoulos 2000)
5. Linear: OLS on time index

Output: predicted return at +1h, +4h, +24h horizons per (symbol, time).
"""
import numpy as np
import pandas as pd
from typing import Optional


def drift_forecast(series: np.ndarray, horizon: int) -> float:
    """Random walk with drift. Predict h steps ahead."""
    n = len(series)
    if n < 2: return float(series[-1]) if n else 0.0
    drift = (series[-1] - series[0]) / max(n - 1, 1)
    return float(series[-1] + drift * horizon)


def seasonal_naive(series: np.ndarray, horizon: int, season: int = 4) -> float:
    """Seasonal naive — predict using value from season-length ago."""
    if len(series) <= season: return float(series[-1]) if len(series) else 0.0
    # For 15m bars with season=4 (1h), predict bar at t+4 = bar at t+4 from now
    target_idx = len(series) - 1 + horizon
    src_idx = target_idx - season
    if src_idx < 0 or src_idx >= len(series):
        return float(series[-1])
    return float(series[src_idx])


def ses_forecast(series: np.ndarray, horizon: int, alpha: float = 0.3) -> float:
    """Simple Exponential Smoothing."""
    if len(series) == 0: return 0.0
    s = float(series[0])
    for v in series[1:]:
        s = alpha * v + (1 - alpha) * s
    # forecast horizon steps ahead = level s
    return float(s)


def theta_forecast(series: np.ndarray, horizon: int) -> float:
    """Theta method (Assimakopoulos & Nikolopoulos 2000).
    Combines OLS trend + SES of second differences.
    Predict = OLS extrapolation + SES level component.
    """
    n = len(series)
    if n < 4: return float(series[-1]) if n else 0.0
    t = np.arange(n, dtype=np.float64)
    # OLS trend
    A = np.vstack([t, np.ones(n)]).T
    slope, intercept = np.linalg.lstsq(A, series, rcond=None)[0]
    ols_pred = slope * (n - 1 + horizon) + intercept

    # SES of theta-line (theta=2: Y'_t = 0.5 * Y_t + 0.5 * trend_t)
    theta_line = 0.5 * series + 0.5 * (intercept + slope * t)
    s_last = float(theta_line[0])
    alpha = 0.2
    for v in theta_line[1:]:
        s_last = alpha * v + (1 - alpha) * s_last
    # OLS extrapolation at horizon
    ols_h = slope * (n - 1 + horizon) + intercept
    # Theta forecast (Hyndman-Billiu 2003): s_h + 0.5 * (OLS_h - s_T)
    # where s_h ≈ s_last (SES extrapolates flat)
    return float(s_last + 0.5 * (ols_h - s_last))


def linear_forecast(series: np.ndarray, horizon: int) -> float:
    """OLS linear extrapolation."""
    n = len(series)
    if n < 2: return float(series[-1]) if n else 0.0
    t = np.arange(n, dtype=np.float64)
    A = np.vstack([t, np.ones(n)]).T
    slope, intercept = np.linalg.lstsq(A, series, rcond=None)[0]
    return float(slope * (n - 1 + horizon) + intercept)


def predict_returns(closes: np.ndarray, horizons: list[int],
                    lookback: int = 64) -> dict[str, dict[int, float]]:
    """For each model, predict returns at each horizon (h steps ahead).
    Returns {model: {horizon: predicted_return_pct}}.
    """
    if len(closes) < 5:
        return {}
    series = closes[-lookback:] if len(closes) > lookback else closes
    last = float(closes[-1])
    if last <= 0:
        return {}

    out = {}
    for model_name, model_fn in [
        ("drift", drift_forecast),
        ("seasonal_naive", seasonal_naive),
        ("ses", ses_forecast),
        ("theta", theta_forecast),
        ("linear", linear_forecast),
    ]:
        per_h = {}
        for h in horizons:
            try:
                pred = model_fn(series, h)
                ret_pct = (pred - last) / last  # signed return %
                per_h[h] = ret_pct
            except Exception:
                per_h[h] = 0.0
        out[model_name] = per_h
    return out
