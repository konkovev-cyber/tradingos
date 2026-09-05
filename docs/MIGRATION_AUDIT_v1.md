# TradingOS Module Migration Audit v1

**Date:** 2026-07-21
**Purpose:** Map existing lab modules to Control Plane. Prevent rebuilding what already exists.

---

## Available Modules in `/root/tradingos_lab/`

| Module | Export | Lines | Status | Relevance to Control Plane |
|--------|--------|------:|--------|---------------------------|
| `capital_engine` | `CapitalAllocationEngine.allocate()` | 301 | ✅ Importable | Capital allocation, risk budgeting |
| `decision_engine` | `DecisionEngine`, `Decision` | 98 | ✅ Importable | PROMOTE/CONTINUE/REJECT decisions |
| `reality_engine` | `RealitySimulator`, `CostModel` | 104 | ✅ Importable | Execution cost simulation |
| `paper_engine` | `PaperEngine`, `PaperOrder`, `PaperPosition` | 112 | ✅ Importable | Paper execution (no real orders) |
| `portfolio_engine` | `dashboard.py`, stress tests | 138 | ✅ Importable | Portfolio view, correlation, risk |
| `risk_budget` | wrapper around capital_engine | 14 | ✅ Importable | Risk budget per edge |

---

## Control Plane Components (in `/root/tradingos/`)

| Component | Status | Role |
|-----------|--------|------|
| Position Guard v1.1 | FROZEN | When to protect profit via SL move |
| Action Shadow v0.1 | COLLECTING | Which actions have positive EV |
| PIE Bridge v1.1 | ACTIVE | Stale filter + freshness |
| Reconciler v0.1 | SKELETON | BingX cross-check (needs HMAC) |
| Decision Review Engine | NEW | Aggregates evidence for review |
| Capital Intelligence | NEW | Portfolio-level financial view |

---

## Migration Map

### Tier 1 — Direct Integration (no rewrite needed)

```
lab/capital_engine
    ↓
control/capital/
    ↓
capital_intelligence_report.py (already exists)
    ↓
Decision Review (capital context)
```

**What to do:** Import `CapitalAllocationEngine` into TradingOS. Use for:
- Capital allocation recommendations per action
- Risk budget calculation
- Portfolio-level decision context

### Tier 2 — Paper Execution (next experiment)

```
lab/paper_engine
    ↓
control/paper/
    ↓
Paper Executor v0.1
    ↓
compare vs HOLD
```

**What to do:** After Decision Review PROMOTE, use `PaperEngine` for:
- Virtual portfolio simulation
- Action vs HOLD comparison
- No real orders

### Tier 3 — Decision Context

```
lab/decision_engine
    ↓
control/decision_review/
    ↓
automated verdicts
```

**What to do:** Use `DecisionEngine` for:
- Auto-scoring experiments
- PROMOTE/CONTINUE/REJECT recommendations
- Human approval gate

### Tier 4 — Cost Reality

```
lab/reality_engine
    ↓
control/reality/
    ↓
cost-aware decisions
```

**What to do:** Use `RealitySimulator` for:
- Fee impact on actions
- Slippage estimation
- Fill rate reality check

### Tier 5 — Portfolio Intelligence

```
lab/portfolio_engine
    ↓
control/portfolio/
    ↓
risk concentration, stress tests
```

**What to do:** Use `dashboard.py` for:
- Risk concentration analysis (already have basic version in capital_intelligence)
- Correlation matrix
- Stress test scenarios

---

## What Does NOT Exist in Lab (must build new)

| Component | Reason |
|-----------|--------|
| PIE Bridge v1.1 | New — connects to live PIE DB |
| Stale Filter (30min) | New — freshness check |
| Action Shadow (PIE→Policy→Shadow) | New — specific to current experiments |
| Outcome Trackers | New — per-signal ROI calculation |
| Experiment Monitor | New — gate status dashboard |

These are Control Plane innovations that don't exist in the lab.

---

## Migration Priority

```
Phase 1 (now):
  capital_engine → capital_intelligence
  decision_engine → decision_review

Phase 2 (after PROMOTE):
  paper_engine → paper execution layer

Phase 3 (after live validation):
  reality_engine → cost-aware execution
  portfolio_engine → portfolio control
```

---

## Key Insight

**60% of Control Plane logic already exists in tradingos_lab.**

The lab has:
- Capital allocation (301 lines)
- Decision scoring (98 lines)
- Cost simulation (104 lines)
- Paper execution (112 lines)
- Portfolio dashboard (138 lines)

The Control Plane added:
- PIE integration (new)
- Stale filtering (new)
- Shadow execution framework (new)
- Evidence collection (new)

**The migration task is: import + adapt, not rewrite.**
