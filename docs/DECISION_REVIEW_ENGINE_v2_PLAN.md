# Decision Review Engine v2 — Migration Plan

**Date:** 2026-07-21
**Phase:** 4

## Existing Canonical Framework (from memory)

**KPI Framework v2** (K75, 2026-07-11):
- TE (True Expectancy) — expected profit per trade after costs
- ESR (Edge Survival Rate) — edge stability across regimes
- ERG (Execution Reality Gap) — degradation from gross to net

**Decision Engine state machine** (2026-07-15):
`WAITING → READY → RUNNING → REVIEW → APPROVED/REJECTED` (+ BLOCKED)

**Shadow Validation Phase scope** (AD13):
- System metrics: signal rejection rate per gate
- Strategy metrics: winrate by regime, expectancy per strategy, DD curves

## What Decision Review v2 Does

Aggregates ALL Control Plane outputs into **one verdict report**:
- Reads: state, capital_v2, portfolio, decision, approval, paper, reality
- Computes: **Reality EV** (Action Net - Hold Net) per approved action
- Uses: existing KPI terminology (TE, ERG)
- Output: `decision_review_v2.json` with single verdict

## Verdict Logic (consistent with existing Decision Engine)

```
PROMOTE:   Reality EV > +0.5% AND sample ≥ 20 AND false_signal < 20% AND DQ > 90
REVISE:    Reality EV near 0 OR DQ between 80-90
FREEZE:    Reality EV negative
NOT_READY: sample < 20 OR DQ < 80
```

## Data Flow

```
tradingos_state.json      → DQ, positions, services
capital_intelligence_v2   → risk, concentration
portfolio_intelligence    → health, stress
decision_report.json      → existing rules
approval.json             → human decisions
paper_simulation.json     → gross EV
reality_result.json       → net EV
                          ↓
                   Decision Review v2
                          ↓
                decision_review_v2.json
```

## Rules

- READ-ONLY aggregator
- NO LIVE changes
- NO rule modifications
- NO execution permission
- REUSES existing Decision Engine rules
- REUSES existing KPI names (TE, ERG)
