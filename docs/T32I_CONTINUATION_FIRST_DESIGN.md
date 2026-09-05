# T32.I — CONTINUATION-FIRST EXIT ENGINE: DESIGN

## Context

- Production FROZEN. R148 not touched. P3 live-shadow n=4 (R148 frozen per R151).
- T32.A-H forensics on n=34 historical completed.
- **Main P3 weakness: MFE 1.0-1.5R zone (n=10, delta -$2.60) — premature trail exit.** T32.G/H finding.
- T32.H CF3 model: NET -$1.50 (vs P3 -$3.54, +$2.04 improvement) — promising but not OOS validated, n=4 ≥1.5R, median=0 in anti-cherry.
- This document is **design-only**, not implementation. No new live-shadow, no change to P3.

## Goal

Design an exit mechanism that:
1. Captures more of the existing favorable movement (no giving back)
2. Protects accumulated profit
3. Minimizes giveback
4. Distinguishes continuation from reversal
5. Does not exit too early from strong trends
6. Accounts for trading costs
7. Works on small-cap crypto and small capital

## The key question (not parameters — information)

**At the moment the position is already profitable, is there observable online information that distinguishes "this move will continue" from "this move will reverse"?**

This is the question that matters. Not:
- Where to place partial? +0.5R / +0.75R / +1R — that's been tried
- When to trail? 0.5R / 0.75R — that's been tried
- Should we lock on first 25% or 50% — that's been tried

**The question is: at moment t, after MFE=X, can we differentiate P(continuation) from P(reversal)?**

If yes → state machine can act on it.
If no → no exit can beat the baseline; all we can do is R148-acceptable position sizing.

## Current evidence (T32.A-H)

| Zone | n | P3 net | REAL net | P3 - REAL | Verdict |
|---|---:|---:|---:|---:|---|
| <0.5R | 14 | -$5.05 | -$6.34 | +$1.29 | P3 wins by NOT triggering |
| 0.5-0.75R | 3 | -$0.12 | +$0.27 | -$0.39 | small loss |
| 0.75-1R | 3 | +$1.06 | -$0.03 | +$1.09 | wins (SUB-1R) |
| 1.0-1.5R | 10 | -$0.28 | +$1.90 | -$2.18 | **DEAD WEIGHT** |
| ≥1.5R | 4 | +$0.85 | +$2.36 | -$1.51 | loses |
| TOTAL | 34 | -$4.97 | -$1.85 | -$3.12 | |

**Information boundary analysis:**

CF3 found: in MFE 0.75-1R zone, structure break + failed continuation → exit (small win). In MFE ≥1.0R, **CF3 keeps the same MFE 1.0+ state machine logic** but with no fixed trail. **But CF3 doesn't have a 0.5R+ holding rule** — it simply holds until structure break.

Question: in MFE 1.0-1.5R zone (n=10), **do the trades that continued past P3 trail also satisfy "no struct break + new high still making" criteria**? Or is there information in momentum decay or retracement speed that PRECEDED the structure break?

If YES — we can detect deterioration BEFORE the structure break. CF3 would be insufficient.

If NO — "no new high + no struct break" is just luck; trades that keep going can also satisfy that condition. The real edge is unknowable.

**This is the key information-boundary test that has not been done yet.**

## State machine design (NOT a parameter grid)

### Design principle
State changes are based on **observed motion state**, not on whether MFE crossed a threshold. The system stays in HOLD when the evidence is ambiguous. The system only commits to a protection or exit when there is **strong evidence** of deterioration or reversal.

### States
- S0 INITIAL: position opened, no profit yet. NO action.
- S1 PROFIT_FORMING: trade is in profit but no structural information yet. Hold.
- S2 CONTINUATION_CONFIRMED: there is evidence that the move is alive (e.g. new favorable extreme, momentum holding, structure intact, no adverse impulse). Hold, no protection.
- S3 DETERIORATION_SUSPECTED: small sign that move may be tiring (early momentum decay, slight retrace, slow new highs). Begin gentle protection (e.g. raise SL to break-even).
- S4 CONTINUATION_FAILED: clear failure of continuation (e.g. failed to make a new high after a sufficient time, retraced meaningful portion of MFE, structure broken). Decide: more aggressive protection, or partial exit.
- S5 REVERSAL_CONFIRMED: structural break + failure to recover + adverse impulse → exit.

### Key rules (HARD)
- **NO INFORMATION ≠ EXIT.** Absence of continuation is NOT reversal. The system only exits when there is **strong positive evidence** of reversal, not merely absence of continuation.
- **NO FUTURE INFORMATION.** All states are determined only from observable information at decision bar.
- **NO FIXED R threshold for transitions.** Transitions depend on information state, not on whether MFE reached a number.

### Information required to transition
For S2 → S3 transition: early signs of momentum decay. For S3 → S4 transition: confirmed failure to make new high OR meaningful retrace + time. For S4 → S5 transition: structural break + failure to recover.

### Practical signals
- velocity (bar-to-bar price change)
- new highs / new lows
- distance from local extreme
- momentum decay
- retracement depth
- structure break / sustain
- consecutive favorable / adverse candles
- time since last impulse

These are the same as in T32.A-H, but the **state machine interpretation** differs from "trigger an exit at X": the **transition matrix** depends on multiple signals, not a single one.

## Information boundary: the key test

For each MFE zone, the question is:

**In the bars between 0.75R and 1.0R (or between 1.0R and 1.5R), is there a measurable change in the state signals that distinguishes trades that ended with further favorable excursion from trades that ended with giveback?**

This test must be performed on the existing 34-trade historical data (with online information only). If there is a measurable distinction — the state machine is valid. If there is not — the state machine is just a more complex way of producing the same result as the baseline, and the conclusion is DATA GAP / NO INFORMATION BOUNDARY.

## Profit lock

The T32.H CF3 model includes a profit lock at 0.5R / 0.75R / 1.0R based on CONTINUATION FAILURE. This is a different concept from "lock at MFE 0.5R no matter what": the lock triggers when the move does not show signs of continuation, not when it reaches a number.

**Lock mechanism (proposed):**
- S2 CONTINUATION_CONFIRMED: no lock.
- S3 DETERIORATION_SUSPECTED: lock a small fraction (e.g. 25%) to break-even.
- S4 CONTINUATION_FAILED: lock a larger fraction (e.g. 50%) to break-even or slightly above.
- S5 REVERSAL_CONFIRMED: exit at remaining position.

**This is not a fixed-R threshold.** The lock is a function of the state-transition evidence, not the MFE level. The MFE level is just one of the signals.

## Counterfactual design (testable on 34 trades)

The state machine can be simulated on the existing 34-trade historical paths and compared against:
- REAL (post-factum, the historical exit)
- P3 (current shadow-engine)
- CF3 (T32.H state machine)
- Static alternatives (e.g. 33% at +0.5R)

All scenarios must use only online information.

## Required tests before any "next step"

1. INFORMATION BOUNDARY TEST: For each MFE zone, is there a measurable distinction in state signals between trades that continue and trades that give back?
2. CAUSAL TEST: Does the proposed state machine beat P3 and REAL on n=34 historical?
3. OOS: Is the result robust to anti-cherry, OOS, and concentration?

## Three possible outcomes

- **OUTCOME A:** The state machine beats P3 and REAL with OOS and anti-cherry. → NEXT STEP: design a paired-shadow-profile of the state machine in live-shadow.
- **OUTCOME B:** The state machine does NOT beat baseline in a way that survives anti-cherry + OOS. → CONCLUSION: the exit information boundary is not observable; **the only correct answer is "no live exit edge beyond P3, focus on size and cost"**. Accept that and proceed accordingly.
- **OUTCOME C:** Inconclusive on n=34. → Either gather more data (OOS at n=10+ live-shadow) or accept that n=34 is sufficient and the answer is negative.

**A negative result is a valid, useful conclusion.** The user has explicitly asked for "GO / NO-GO". NO-GO is acceptable.

## What NOT to do
- DO NOT pick more thresholds (0.4R / 0.5R / 0.6R / ...). It has been tried.
- DO NOT redo T32.A-H. They are closed.
- DO NOT change P3 live-shadow or production.
- DO NOT use future MFE / future MAE.
- DO NOT design a "smart stop" that mimics P3.

## One best next experiment

Run the **information boundary test** on the existing 34 historical trades. Specifically:

For each profitable trade, at the bar where MFE first reached each threshold (0.5R / 0.75R / 1.0R / 1.25R / 1.5R), record the state signals (momentum, distance from extreme, new highs, structure, time, etc.) and the subsequent outcome (continued or gave back).

If the state signals are statistically different between "continued" and "gave back" outcomes at any threshold → INFORMATION BOUNDARY exists → state machine is viable → proceed to design.

If not → NO-GO. The honest conclusion is "we cannot observe a reversal before it happens with online information on this dataset; the best we can do is R148-acceptable position sizing".

## Conclusion

The T32.I question is: **can we build an exit engine that captures more of the existing motion by responding to motion state in real time?**

The empirical answer on n=34 may be **NO**. That is OK. The T32.I deliverable is the **test, not a parameter set**. The user has asked for GO / NO-GO, and NO-GO is acceptable.

## Status: AWAITING T32.I EXECUTION

**Production remains FROZEN. R148 unchanged. P3 live-shadow unchanged. Nothing is implemented until the information-boundary test result is in hand.**

## Files
- /tmp/t32h_output.json (raw historical results)
- docs/T32I_CONTINUATION_FIRST_DESIGN.md (this document)
- (will add) /tmp/t32i_info_boundary.py (information boundary test script)
