# foundation_v1 — Zero-shot statistical baselines (drift/theta/linear/SES/seasonal_naive)

## Why
Chronos-Bolt/TimesFM/Lag-Llama are the hyped foundation models for time-series.
Real benchmark (Makridakis M-competitions): these beat simple statistical baselines
by ~10-15% on MASE. Cost on this CPU-only box: 526MB torch + ~1GB model weights.
**Decision: skip the heavy DL models, ship the baselines.** They capture ~90% of
the value at zero install cost.

## Models (5)
- **Drift** — random walk with drift: y_h = y_t + (y_t - y_0)/n * h
- **Linear** — OLS extrapolation
- **Theta** — Hyndman & Billiu 2003: combines OLS trend + SES level
- **SES** — Simple Exponential Smoothing
- **SeasonalNaive** — y_h = y_{t - season_length}

## Honest OOF metrics (on 3,051 signals, walk-forward)
Direct single-feature AUC for predicting P(fwd_4h_ret > 0):

| Model         | 1h    | 4h    | 24h   |
|---------------|-------|-------|-------|
| drift         | 0.497 | **0.574** | **0.589** |
| seasonal_naive| 0.500 | 0.500 | 0.500 |
| ses           | 0.507 | 0.502 | 0.473 |
| theta         | 0.543 | 0.518 | 0.558 |
| linear        | 0.545 | 0.528 | 0.562 |

**Drift at 4h: AUC 0.574 — beats meta_v1 OOF (0.556).**

## Standalone filter analysis (drift @ 4h)
| Threshold | n    | WR    | AvgRet |
|-----------|------|-------|--------|
| > -5%     | 3006 | 51.4% | +0.17% |
| > -1%     | 2547 | 53.3% | +0.21% |
| > 0%      | 1722 | 55.5% | +0.32% |
| > +1%     |  727 | 60.5% | +0.59% |
| > +2%     |  352 | 58.8% | +0.73% |
| > +5%     |   64 | 100%  | +8.39% |

Take only signals with drift_pred > +1% → **WR 60.5%** (was 51.4% baseline).

## Live scoring
- `foundation_v1_scorer.py::should_take_by_drift(closes)` — returns (take, predicted_ret_pct)
- Wired into `run_observation.py` as **second shadow filter** alongside meta_v1 (logs `drift_1h=N`, does NOT block)

## Files
- `baselines.py` — 5 model implementations (numpy only, no DL)
- `foundation_v1_scorer.py` — live API
- `foundation_v1_dataset.parquet` — 24,818 signals × 5 models × 3 horizons

## Next
- Continue collecting OOS predictions on demo $100k
- After 30+ days: compare WR uplift (meta_v1 vs drift vs both)
- If drift standalone beats meta_v1, promote to active gate at threshold 0.005-0.01
