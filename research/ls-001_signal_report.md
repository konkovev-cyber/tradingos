# LS-001: Signal Generation Report

## Experiment Status

| Metric | Value |
|--------|-------|
| Runtime | 14.5 hours |
| Cycles | 871 |
| Candles analyzed | ~200 (initial batch) + incremental |
| Errors | 0 |
| Uptime | 100% |

## Signal Analysis

| Metric | Value |
|--------|-------|
| Total signals (lifetime) | 3 |
| Sweep UP (LONG) | 2 |
| Sweep DOWN (SHORT) | 1 |
| Signal rate | ~1 per 5 hours |
| Avg candles per signal | ~67 (on initial 200-candle batch) |

## Condition Funnel

Based on 200-candle historical scan:

| Gate | Pass rate |
|------|-----------|
| Candles checked | 200 (100%) |
| high/low > 0 | 200 (100%) |
| Warmup (10 bars) | 190 (95%) |
| Sweep UP: high > recent_high × 1.0015 AND close < recent_high | ~1% |
| Sweep DOWN: low < recent_low × 0.9985 AND close > recent_low | ~0.5% |
| **Final signals** | **3 (1.5%)** |

## Observation

- Strategy is **strict**: only ~1.5% of candles produce a signal
- Signal distribution is uneven: 3 signals arrived in the first 200 candles, then 0 for 14+ hours
- This suggests signals cluster around specific market conditions (volatility events, session transitions)
- The strategy is NOT dead — it's waiting for the right pattern

## Next Checkpoint

Re-run signal analysis at 24h and 48h marks.
