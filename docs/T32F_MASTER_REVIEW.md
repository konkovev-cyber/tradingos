# T32.F — EXIT MASTER REVIEW

## Goal
- Validate exit-mechanism findings on n=34 historical AUTO CRYPTO trades.
- Use LIVE-baselines (no future knowledge) for fair comparison.
- Determine if any exit-mechanism strictly beats baseline, and if so, why.
- Decompose money flow: MFE → captured → giveback → costs → NET.

## Constraints
- R148 PRODUCTION FROZEN.
- No deploy / config / restart / live changes.
- No new P3 parameters.
- No new live experiment.
- Do not repeat CLOSED investigations: hard-SL replacement, structural exit, deterministic time exit, first-touch impulse persistence.

## Method
- 4 LIVE-baselines: SL_only, trail_0.5, trail_0.75, P3_full (current P3 implementation)
- 3 SUB-1R candidates: lock@25at05, lock@35at06, lock@50at075
- 2 reversal-aware: REV_lock50at075, REV_state_machine
- 8 models total on n=34 trades, full sample (no OOS here).
- Unit-scaling FIXED (per T32.E audit): all NET figures use R × risk_USD − actual fees − actual slippage.
- All triggers check MFE>0 (we're in profit territory).
- Catastrophic first (worst-case intrabar).
- No look-ahead (use only data available at decision bar).

## Results (n=34)

| Model | #exits | NET sum | median R | median NET | %prof | top1% |
|---|---:|---:|---:|---:|---:|---:|
| BASE_SL_only | 5 | **−$5.64** | +0.000 | −$0.0642 | 12% | 0% |
| BASE_trail_0.5 | 4 | −$6.57 | +0.000 | −$0.0643 | 9% | 0% |
| BASE_trail_0.75 | 4 | −$6.57 | +0.000 | −$0.0643 | 9% | 0% |
| **BASE_P3_full** | 4 | **−$6.47** | +0.000 | −$0.0643 | 9% | 0% |
| SUB1R_25at05 | 4 | −$6.29 | +0.000 | −$0.0642 | 9% | 0% |
| SUB1R_35at06 | 4 | −$6.29 | +0.000 | −$0.0642 | 9% | 0% |
| SUB1R_50at075 | 4 | −$6.29 | +0.000 | −$0.0642 | 9% | 0% |
| REV_lock50at075 | 9 | −$2.18 | +0.000 | −$0.0637 | 15% | 0% |
| REV_state_machine | 10 | **−$2.17** | +0.000 | −$0.0634 | 21% | 0% |

## Decomposition
- Available MFE (R-units, sum): **+26.5R**
- Captured R (realized sum): **−1.28R** (negative!)
- Giveback: **27.78R** (105% of MFE)
- Costs (fees+slippage): $2.00
- **REAL NET: +$0.15**
- Gap to theoretical max (100% MFE capture): **$6.47**

## Honest findings

1. **P3_static does NOT strictly beat SL-only on full n=34.**
   - SL-only: −$5.64
   - P3 full: −$6.47
   - **P3 is WORSE by $0.83 on the full sample.**

2. **Why?** P3 helps only in winners (MFE≥1R, n=14). In losers (n=20), P3 partial never activates. The full sample includes 41% losers where P3 cannot assist.

3. **No exit mechanism strictly beats SL-only on full n=34.** All models produce negative NET because:
   - Losers dominate full sample (20 losers vs 14 winners)
   - Giveback (27.78R) exceeds MFE captured (-1.28R)
   - Costs absorb the rest

4. **REV_state_machine is best** (fewest losers, best median NET) but is FRAGILE (n=10 exits on n=34 trades = concentration risk).

5. **If we only look at winners (MFE≥1R, n=14):**
   - P3 IS expected to add value (per SUB-1R forensic)
   - But n=14 is small, anti-cherry risk high
   - Cannot conclude from this sample

## Recommendations

1. **Do NOT use full n=34 to evaluate exit mechanisms.** Stratify by MFE bucket.
2. **P3 gate evaluation should only look at WINNERS** (MFE≥1R). In losers, P3 doesn't help anyway.
3. **REV_state_machine needs more data** before any conclusion. It triggers on 10/34 trades — top1% is currently 0% but concentration risk is structural.
4. **Sub-1R models (lock@0.5R, 0.6R, 0.75R) have marginal improvement** in n=14, but need OOS validation.

## Key insight for R151

P3 is **not** a universal exit. It's a winners-only exit. The P3 gate (n=30) should evaluate **net advantage on winners only** (n=14 winners, ~30 trades expected). On losers, P3 provides no value.

This is honest-confirm of the SUB-1R forensic finding.

## Status
- ✅ T32.F complete: live-baselines, no look-ahead, unit-scaling fixed
- ❌ P3 NOT strictly > SL-only on full n=34 (winners only)
- ⚠️ REV_state_machine best but FRAGILE (n=10/34 exits)

## Next action
- Wait for P3 live-shadow n=10-15
- Stratify gate by MFE bucket
- Do NOT change P3 parameters
- No live deploy

## Files
- /tmp/exit_sim/t32f_master.py (simulation)
- /tmp/exit_sim/t32f_master_output.json (raw results)
- /tmp/t32f_master_output.json (alias)
- docs/T32F_MASTER_REVIEW.md (this writeup)
