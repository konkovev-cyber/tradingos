# KPI Framework v2 Integration — Control Plane v1

**Date:** 2026-07-21
**Purpose:** Align Control Plane with canonical Phase 3 KPI Framework (K75)
**Status:** MANDATORY — these rules prevent future architectural drift

---

## Canonical KPIs (from memory K75, 2026-07-11)

TradingOS has 3 core KPIs that are **already defined and approved**:

| KPI | Name | Definition | Sample Required |
|-----|------|------------|-----------------|
| **TE** | True Expectancy | Expected profit per trade after all costs | ≥ 20 trades |
| **ESR** | Edge Survival Rate | Edge stability across regimes/periods | ≥ 20 trades |
| **ERG** | Execution Reality Gap | Gross → net degradation from execution | ≥ 20 trades |

**Phase 3 launch snapshot (K111):** `TE=0.015, ERG=0.07, CAER=0.72`

**Related Phase 3 rules:**
- R56: "MT5 protocols = evaluation criteria at trade checkpoints (TE/ERG/CAER)" — single owner
- R95: "Human approval mandatory" + "PASS ≠ auto capital" — multi-gate
- Phase 3 Operational: "Decision Rules at 50 trades" — sample gates are mandatory
- AD46: 3 architectural brains (Decision, Reality, Governor)

---

## Single Source of Truth Principle

Each KPI has **exactly one owner**. All other components are consumers.

```
KPI Framework (K75) — sole owner of TE/ESR/ERG
        ↓
    Consumed by:
    ├─ Decision Review v2 (read-only)
    ├─ CEO Control (display only)
    └─ Future dashboard (read-only)
```

---

## HARD RULE: Decision Review is READ-ONLY for KPIs

This is the **architectural invariant** that prevents future drift.

### ✅ Decision Review MAY:
* Read TE/ESR/ERG from KPI Framework
* Display TE/ESR/ERG in reports
* Use TE/ESR/ERG in verdict logic (e.g., sample gates)
* Explain why a verdict was made based on KPIs

### ❌ Decision Review MAY NOT:
* Compute or recalculate TE
* Compute or recalculate ESR
* Compute or recalculate ERG
* Store its own values of TE/ESR/ERG
* Define new KPI names (e.g., "Reality delta", "Execution gap", "vs_hold", "Execution impact")
* Mix local decision signals with system KPIs in the same field

### Rationale

> *"A second source of truth, even with the same name, will drift."*
> *"If Review could compute its own ERG, then over time Review's ERG and KPI Framework's ERG would diverge."*
> *"The moment they diverge, all downstream decisions become ungrounded."*

This rule is **non-negotiable**. It must be enforced by code review, not just documentation.

---

## Two Levels of Signal

### Local Signals (per-decision)

Source: **Reality Engine** (per-position, per-decision)
Purpose: Answer "Was this specific action worth it?"
NOT a system KPI.
Examples:
- `paper_delta`: gross paper PnL vs hold
- `net_vs_hold`: net (after costs) vs hold
- `local_recommendation`: HOLD_BETTER | ACTION_BETTER | NEUTRAL

### System KPIs (system-wide)

Source: **KPI Collector** (Phase 6+)
Purpose: Answer "How is the system performing overall?"
Required samples: ≥ 20 for each
Examples:
- TE: expected profit per trade after costs
- ESR: edge stability across regimes
- ERG: gross → net degradation

---

## Sample-Based Gating

Decision Review v2 must enforce:
```
TE_samples < 20  →  KPI: NOT_READY
ESR_samples < 20 →  KPI: NOT_READY
ERG_samples < 20 →  KPI: NOT_READY
```

Only when ALL three gates have samples ≥ 20, the verdict can move past `REVIEW`.

Until then, verdict stays `NOT_READY` (insufficient evidence).

---

## Verdict Structure

```json
{
  "verdict": "REVIEW",
  "confidence": 0.6,
  "reasons": ["DQ=75.4 below 80"],
  "kpi_status": {
    "te": "COLLECTING",
    "esr": "NOT_STARTED",
    "erg": "COLLECTING",
    "te_samples": 5,
    "esr_samples": 0,
    "erg_samples": 2
  },
  "decision_eligibility": "NOT_READY",
  "local_signals": {
    "VELVET-USDT": {
      "paper_delta": 0.0,
      "net_vs_hold": -6.78,
      "local_recommendation": "HOLD_BETTER"
    }
  },
  "data_quality": {"score": 75.4},
  "next_action": "WAIT_FOR_DATA_QUALITY_AND_SAMPLES"
}
```

---

## Architecture Diagram (Stable Contract)

```
Reality Engine
    │
    ├─ computes Action Net (per-decision)
    ├─ computes Hold Net (per-decision)
    └─ produces Local Signals

KPI Collector (Phase 6+)
    │
    ├─ updates TE (system-wide, ≥20 samples)
    ├─ updates ESR (system-wide, ≥20 samples)
    └─ updates ERG (system-wide, ≥20 samples)

Decision Review v2
    │
    ├─ reads Local Signals (from Reality)
    ├─ reads KPI samples (from KPI Collector)
    └─ produces Verdict (NOT_READY | REVIEW | PROMOTE | FREEZE)

Capital Gate
    │
    └─ checks: verdict == PROMOTE && KPIs == READY
       → Execution eligibility
```

**No arrows pointing back from Review to KPI computation.**

---

## Status Enums

### KPI Status:
- `NOT_STARTED`: 0 samples
- `COLLECTING`: 1-19 samples
- `READY`: ≥ 20 samples

### Decision Eligibility:
- `NOT_READY`: any KPI < READY OR DQ < 80
- `ELIGIBLE`: all KPIs READY AND DQ ≥ 80

### Local Recommendation:
- `HOLD_BETTER`: action lost to hold (after costs)
- `ACTION_BETTER`: action beat hold (after costs)
- `NEUTRAL`: within noise band (|delta| < 0.10)

---

## Enforcement Checklist

For any future change to Decision Review v2:

- [ ] Does it compute any new TE/ESR/ERG value? → **REJECT**
- [ ] Does it store TE/ESR/ERG locally? → **REJECT**
- [ ] Does it add a new metric name? → **Requires architecture review**
- [ ] Does it use TE/ESR/ERG from KPI Collector? → **OK**
- [ ] Does it use Local Signals from Reality? → **OK**
- [ ] Does it explain its verdict using KPIs? → **OK**

---

## Reference

- K75: KPI Framework v2 definition
- K84: 100 Trades Structural Signal Extraction
- K111: Phase 3 launch KPI snapshots
- R56: MT5 evaluation criteria = TE/ERG/CAER
- R95: Human approval mandatory, PASS ≠ auto capital
- AD46: 3 architectural brains
- Phase 3 Operational: Decision Rules at 50 trades
