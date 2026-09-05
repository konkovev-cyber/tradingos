# SIGNAL DIRECTION ROOT-CAUSE FORENSIC — VERDICT

*Read-only forensic. Production FROZEN, R148/P3 unchanged.*

---

## 1. Direction Decision Chain (FACT from code)

```
signal_generator.py:_determine_direction(h1, h4):

  h4.trend_up = fv.ema_bullish = (ema20 > ema50)   # feature_vector.py:118

  if h4.trend_up is True:        → "BUY"
  if h4.trend_up is False
     and h4.ema20 < h4.ema50:   → "SELL"
  if h4.trend_up is False
     and ema20 >= ema50:        → None (sideways)

  fallback to h1 with same logic
```

**Direction = pure EMA20/EMA50 crossover on H4 (fallback H1).** No momentum, no RSI direction, no order-flow, no market context, no BTC direction. Just `ema20 > ema50 → BUY`, `ema20 < ema50 → SELL`.

---

## 2. 10 WRONG_DIRECTION Trades (FACT)

| sym | side | MFE | realR | net | ADX | RSI | expR | bias_R | guardian |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| MUBARAKUSDT | SELL | 0.13R | -1.06R | -$0.53 | 27 | 65 | 0.68 | -1.74 | NONE |
| KMNOUSDT | BUY | 0.04R | -1.02R | -$0.50 | 37 | 47 | 0.65 | -1.67 | NONE |
| SAGAUSDT | SELL | 0.00R | -1.09R | -$0.55 | 26 | 52 | 0.65 | -1.75 | NONE |
| CCUSDT | SELL | 0.12R | -1.10R | -$0.55 | 25 | 55 | 0.65 | -1.75 | NONE |
| INXUSDT | BUY | 0.00R | -1.12R | -$0.50 | 42 | 55 | 0.68 | -1.80 | NONE |
| AVAAIUSDT | BUY | 0.00R | -1.32R | -$0.16 | 30 | 48 | 0.65 | -1.97 | NONE |
| BTWUSDT | SELL | 0.16R | -1.03R | -$0.46 | 27 | 50 | 0.65 | -1.68 | NONE |
| ROBOUSDT | SELL | 0.00R | -1.10R | -$0.37 | 27 | 54 | 0.65 | -1.75 | NONE |
| PLUMEUSDT | SELL | 0.04R | -1.11R | -$0.37 | 28 | 62 | 0.71 | -1.82 | NONE |
| GWEIUSDT | BUY | 0.11R | -1.50R | -$0.52 | 21 | 46 | 0.65 | -2.15 | NONE |

**ALL 10 have:**
- MFE ≈ 0.0R (price went **immediately** against direction)
- MAE = -0.92R median (price went 0.92R against before SL)
- guardian_trigger = **NONE** (no BE/partial/tight ever fired — straight to SL)
- signal_class = **SIGNAL_LOSS_EXEC_LOSS** (all 10)
- quality = **MEDIUM** (all 10)
- expected_R ≈ 0.65 (identical to GOOD trades 0.67 — signal generator cannot distinguish)

---

## 3. SELL vs BUY — NOT a SELL-specific problem

| Side | n | WRONG | % WRONG | NET | med MFE |
|---|---:|---:|---:|---:|---:|
| BUY | 15 | 4 | **27%** | +$0.679 | 1.05R |
| SELL | 19 | 6 | **32%** | -$0.524 | 0.51R |

**Both sides have ~30% WRONG_DIRECTION.** SELL is slightly worse (32% vs 27%) but NOT the root cause. The root cause is **direction system itself**, not a SELL-specific bug.

---

## 4. bias_R — Signal Generator Overestimates

| | WRONG | GOOD | diff |
|---|---:|---:|---:|
| expected_R | 0.65 | 0.67 | -0.02 (identical) |
| bias_R | **-1.75** | **-0.23** | **-1.52** |
| MAE | **-0.92R** | **-0.25R** | **-0.67R** |

**Signal generator expects 0.65R move on ALL trades (cannot distinguish).** But WRONG trades get -1.75R bias (expected +0.65R, got -1.10R). GOOD trades get -0.23R bias (expected +0.67R, got +0.44R).

**The signal generator has NO mechanism to predict whether the EMA-crossover trend will continue or reverse.**

---

## 5. Counterfactual Timing

| Offset | NET | WR |
|---|---:|---:|
| -2 bars | -$4.876 | 21% |
| -1 bars | -$5.948 | 15% |
| 0 (actual) | -$4.919 | 18% |
| +1 bars | -$4.736 | 21% |
| +2 bars | -$3.997 | 21% |

**Earlier entry is WORSE.** Later entry (+2 bars) slightly better but still deeply negative. Timing is not the fix.

---

## 6. Component Ablation — Not Applicable

Direction = `ema20 > ema50` on H4. There is only **one directional component** (EMA crossover). There is nothing to ablate — the entire direction decision is a single EMA20/EMA50 comparison.

---

## 7. Probability Calibration

| prob bin | n | actual favorable | med MFE | NET | % WRONG |
|---|---:|---:|---:|---:|---:|
| 0.55-0.59 | 24 | 56% | 0.70R | +$0.30 | 33% |
| 0.60+ | 10 | 60% | 0.80R | -$0.15 | 30% |

**Probability does NOT calibrate direction quality.** 0.60+ prob has 30% WRONG — same as 0.55-0.59. The probability is a **score-normalized value** (`confidence = final_score / 100.0`), not a calibrated directional prediction.

---

## 8. Root Cause Summary

**The EMA20/EMA50 crossover direction system is a lagging trend-following indicator.** On 10/34 trades (29%), the EMA crossover detects a trend that has **already ended**, and price immediately reverses against the signal direction. The signal generator:

1. **Cannot predict trend continuation vs reversal** — expected_R is identical (0.65 vs 0.67)
2. **Has no momentum/market-context confirmation** — pure EMA crossover only
3. **No filter exists that separates WRONG from GOOD** — ADX, RSI, score, prob, quality all fail OOS
4. **Not a SELL-specific bug** — both sides ~30% WRONG
5. **Not a timing issue** — counterfactual offsets don't help
6. **Not a data defect** — all 10 trades have valid signal_class, no NaN/stale

---

## 9. FINAL VERDICT: **NO_ROOT_CAUSE_FOUND (F)**

The signal generator's direction decision is a **simple EMA20/EMA50 crossover** that produces 29% WRONG_DIRECTION trades. This is not a bug, not a SELL asymmetry, not a timing issue, not a data defect. It's a **structural limitation of using a lagging EMA crossover as the sole directional indicator on small-cap crypto.**

**The signal generator contains no mechanism to distinguish "trend will continue" from "trend has ended."**

---

## 10. ONE Proposed Correction (NOT IMPLEMENTED)

**ROOT CAUSE:** EMA20/EMA50 crossover is the sole directional indicator. It is lagging and cannot distinguish continuing trends from exhausted trends.

**WHY:** On small-cap crypto, EMA crossovers frequently occur at the END of a move, not the beginning. By the time EMA20 crosses EMA50, the move is often already exhausted.

**EXACT CHANGE:** Add a **momentum confirmation filter** — require that the EMA crossover direction is confirmed by at least one of:
- M15 momentum in the same direction (close > EMA20 on M15 for BUY, close < EMA20 for SELL)
- RSI above 50 for BUY, below 50 for SELL (trend strength confirmation)
- Distance from EMA20 < 2 ATR (not overextended)

This would NOT change the direction logic — it would ADD a confirmation gate that rejects exhausted-trend entries.

**EXPECTED ECONOMIC EFFECT:** Filter out 10/34 WRONG_DIRECTION trades (~29%), saving ~$4.5 in losses. May also filter some GOOD trades — OOS test required.

**WHAT REMAINS FROZEN:** Everything. This is a design proposal only.

**FAILURE CONDITION:** If the confirmation filter removes GOOD trades at the same rate as WRONG (33% BAD vs 27% BAD after filter), it has no edge.

**OOS:** Not yet tested — this is a proposed correction, not a validated one.

---

## Production remains FROZEN. R148/P3 unchanged.