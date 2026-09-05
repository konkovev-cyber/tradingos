"""
meta_v1_scorer.py — Live scorer for meta_v1_full_model.

Loads trained model and scores incoming signals. Designed to be called from
signal_generation pipeline or as a shadow validator that runs in parallel.

Usage:
    from ml.meta_v1_scorer import score_signal
    prob = score_signal(features_dict)  # returns P(fwd_4h_ret > 0)

Features expected:
    score (0-100), rsi (0-100), adx, atr, ema20, final_probability, confidence, rejected (0/1)
"""
from pathlib import Path
import numpy as np
import lightgbm as lgb

_MODEL = None
FEAT_COLS = ["score","rsi","adx","atr","ema20","final_probability","confidence","rejected"]


def _load():
    global _MODEL
    if _MODEL is None:
        path = Path("/root/tradingos/ml/meta_v1/meta_v1_full_model.txt")
        if not path.exists():
            return None
        _MODEL = lgb.Booster(model_file=str(path))
    return _MODEL


def score_signal(features: dict) -> float | None:
    """Return P(fwd_4h_ret > 0) ∈ [0,1] or None if model unavailable."""
    m = _load()
    if m is None:
        return None
    x = np.array([[features.get(c, -1) for c in FEAT_COLS]], dtype=np.float32)
    return float(m.predict(x)[0])


def should_take(features: dict, threshold: float = 0.55) -> tuple[bool, float | None]:
    """Soft gate: return (take, probability).
    take=True only if meta_prob >= threshold AND model loaded.
    Use this as a SHADOW filter alongside the existing gates.
    """
    p = score_signal(features)
    if p is None:
        return True, None  # fail-open (model unavailable, do not block)
    return (p >= threshold, p)
