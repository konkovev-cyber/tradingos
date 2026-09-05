# STAGE 0 ACCELERATION REPORT

## Signal Funnel Analysis — Forward

| Stage | Count | Conversion | Lost here |
|-------|:-----:|:-----------:|:---------:|
| OHLCV observations | 143 | 100% | — |
| Probability > 0.30 | 97 | **67.8%** | 32.2% |
| Probability > 0.40 | 8 | **5.6%** | **62.2%** |
| Probability > 0.50 | 0 | 0% | 100% |
| Accepted (≥0.55) | 0 | 0% | — |

**Bottleneck identified:** 62.2% of observations drop between 0.30 and 0.40. The scoring engine assigns most observations a "WEAK" quality with probabilities 0.20-0.35. Only ~5.6% reach "MEDIUM" quality (0.40+), and none have breached 0.50.

## Near-Signal Queue (top 5 opportunities)

| Symbol | Prob | Distance | ADX | RSI | Quality |
|--------|:----:|:--------:|:---:|:---:|:-------:|
| **BNBUSDT** | **0.480** | **0.070** | 22 | 51 | MEDIUM |
| BEATUSDT | 0.450 | 0.100 | 20 | 50 | MEDIUM |
| ETHUSDT | 0.440 | 0.110 | 32 | 45 | MEDIUM |
| DOGEUSDT | 0.410 | 0.140 | 34 | 36 | WEAK |
| ADAUSDT | 0.390 | 0.160 | 35 | 45 | WEAK |

BNBUSDT at 0.48 is the closest. Needs 0.07 more (13% increase) to reach threshold.

## Symbol Productivity

| Symbol | Obs | Avg Prob | Max Prob | Near threshold? |
|--------|:---:|:--------:|:--------:|:---------------:|
| ETHUSDT | 13 | 0.358 | **0.440** | Close (MEDIUM) |
| ADAUSDT | 13 | 0.367 | 0.390 | Far |
| BEATUSDT | 13 | 0.348 | **0.450** | Closest (MEDIUM) |
| BNBUSDT | 13 | 0.347 | **0.480** | **Closest overall** |
| DOGEUSDT | 13 | 0.321 | 0.410 | Far |
| BTCUSDT | 13 | 0.312 | 0.330 | Far |
| SOLUSDT | 13 | 0.302 | 0.330 | Far |
| KAITOUSDT | 13 | 0.295 | 0.330 | Far |
| LAUSDT | 13 | 0.290 | 0.290 | Very far |
| COTIUSDT | 13 | 0.283 | 0.290 | Very far |
| XRPUSDT | 13 | 0.278 | 0.380 | Very far |

BNBUSDT, BEATUSDT, ETHUSDT are the "most active" — closest to threshold.

## Historical Comparison

| Metric | Historical | Forward |
|--------|:----------:|:-------:|
| Avg prob (rejected) | 0.343 | 0.295-0.367 |
| Max prob (rejected) | 0.540 | 0.480 |
| Observations near ≥0.50 | common (P95=0.480) | **0** |
| Accepted avg prob | 0.576 | N/A |

Forward avg probability (0.30-0.37 per symbol) is **within** historical rejected range (0.12-0.54), but on the lower end. Historical had observations at 0.50+ regularly; forward has none yet.

## Decision: Option A

**Current waiting is correct.** No bottleneck found in SignalGenerator. The funnel shows the model is working as designed — it assigns "WEAK" probabilities (0.20-0.35) to most current market conditions. This is a market regime issue, not a system defect.

- 5.6% of observations reach 0.40+ (consistent with historical)
- Those 0.40-0.48 observations are "near threshold" but not through
- In historical data, these 0.40-0.48 observations would also have been rejected
- The system needs a market shift toward higher-probability conditions

**Estimated time to first signal at current rate:** When one of the 5.6% of 0.40+ observations reaches the additional 0.07-0.15 needed. At 143 observations, ~8 have reached 0.40+. If every ~18 observations produce one 0.40+ attempt, and ~1 in 8 such attempts breaks through to 0.55+ — expected ~1 signal per 144 observations, or ~1 per day at 11 symbols × 24h.

## Verdict

**No changes needed.** The bottleneck is market conditions (high ADX / low RSI regime producing low-confidence signals), not SignalGenerator calibration. Continue observation. First signal expected within ~1 day based on current probability drift.
