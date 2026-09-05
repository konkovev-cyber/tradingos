# Controlled Trading Experiment v1 — Preparation

**Date:** 2026-07-21
**Status:** PLANNED (not started)

---

## Context

Control Plane test PASSED:
- TE = 0, ESR = 0, ERG = 1.0 at micro scale
- System correctly identified "no advantage at current scale"
- This is NOT failure — it's the system working as designed

Next step: Controlled experiment at proper capital scale.

---

## 1. Clean Baseline

### Option A (Recommended): Use existing BingX account
- Close 9 current positions (unknown origin, micro-scale)
- Start fresh with known capital
- Clean statistics from day 1

### Option B: Separate test bucket
- Keep existing positions (observation only)
- Allocate separate test capital
- Risk: mixing data sources

**Decision needed:** A or B?

---

## 2. Component Roles (Fixed)

| Component | Role | Responsibility |
|-----------|------|---------------|
| ubot_bingx | Scanner | Which pairs, what signals, scores |
| TradingOS | Risk Controller | When to enter, risk level, when to hold/exit |
| BingX API | Ground Truth | Real positions, prices, PnL |
| Human | Final Gate | Approval for each action |

---

## 3. Experiment Parameters

```
Duration:       30 days
Trades:         20-50
Positions:      1-3 simultaneous
Risk:           Fixed per trade
Max DD:         5% of capital

Forbidden:
- Martingale
- Grid expansion
- Averaging down
- Hidden positions
- Uncontrolled exposure
```

---

## 4. Trade Journal Format

Each trade must record:

### Entry
- Signal source (ubot scanner score)
- Risk amount ($)
- Expected hold time
- Entry reason

### Exit
- Gross PnL
- Fees
- Spread
- Net PnL
- Hold comparison
- Result (WIN/LOSS)

---

## 5. Success Criteria (after 20+ trades)

| KPI | Target | Meaning |
|-----|--------|---------|
| TE | > 0 | Positive net expectancy |
| ESR | > threshold | Edge survives across conditions |
| ERG | < critical | Execution costs don't destroy edge |

---

## 6. What NOT to do

- ❌ Auto-execution without human approval
- ❌ Full capital access for bot
- ❌ Increased risk to "test faster"
- ❌ KPI changes during experiment
- ❌ New architecture layers

---

## 7. First practical step

Before any trading:

1. Decide: Option A (clean slate) or Option B (separate bucket)
2. Document starting capital
3. Confirm scanner signals are actionable
4. Write experiment protocol
5. Begin with manual execution through Human Approval
