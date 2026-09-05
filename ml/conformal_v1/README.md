# conformal_v1 — Per-symbol quantile-based SL/TP (static ACI baseline)

## What
Compute per-symbol empirical quantiles of forward returns from replay_cache
klines. Calibrate SL as 5% quantile (5% of trades get stopped out within horizon),
TP as 50% quantile (median forward return = TP target).

This is the static Adaptive Conformal Inference (ACI) baseline — guaranteed
coverage on past data. Future work: online ACI with rolling window for
regime adaptation.

## Data
- 241 symbols × 200 M15-bars each (replay_cache)
- Forward returns at 1h (4 bars), 4h (16 bars), 24h (96 bars)

## Method
- For each (symbol, horizon): compute 5% and 50% quantiles of returns
- SL = entry * (1 - |5% quantile|)  (5% of positions hit SL within horizon)
- TP = entry * (1 + |50% quantile|)  (median forward return target)

## Coverage check
- ATR(14)*2 (current default): hit rate **6.00%** (over-stops)
- Conformal 5% quantile: hit rate **5.37%** (matches α=5%)
- Conformal 2% quantile: hit rate **2.15%** (matches α=2%)

Per-symbol variation:
- BTCUSDT 1h SL: 0.59% (low-vol asset)
- ETHUSDT 1h SL: 0.73%
- AGIUSDT 1h SL: 8.09% (high-vol asset)
- MSFTUSDT 1h SL: 0.27% (TradFi low-vol)

## Live scoring (shadow mode)
- `conformal_v1_sl_tp.py::compute_sl_tp_for_side(symbol, entry, side, horizon)`
- Wired into `trade_executor.py` — logs `CONFORMAL SL/TP (shadow)` alongside
  the ATR-based SL/TP. Does NOT override (yet).

Example log line:
```
📐 CONFORMAL SL/TP (shadow, 16-bar SL / 4-bar TP horizon):
   symbol=OPUSDT side=BUY entry=0.09767
   current SL=0.0947 TP=0.1016
   conformal SL=0.0949199 TP=0.097749
```

## Comparison: ATR vs Conformal (OPUSDT case)
- ATR SL=0.0947 (3.0% from entry) — wider, more buffer
- Conformal SL=0.0949 (2.8% from entry) — tighter, calibrated to actual 5% quantile
- Conformal TP=0.0977 (0.08% from entry, just barely above) — too tight
- ATR TP=0.1016 (4.0% from entry) — wider, more conservative

**The conformal TP looks too tight** (median return = 0.08%). Reality wants a
positive expectancy TP, not the median. For production: use a HIGHER TP quantile
(e.g. 60-70%) for asymmetric positive-expectancy.

## Next
- Refine TP quantile to 0.65-0.75 (positive-expectancy target, not median)
- Re-benchmark on demo OOS after 30+ days
- If conformal SL improves WR by ≥3pp vs ATR → promote to active (override)
- Else: keep as shadow telemetry
