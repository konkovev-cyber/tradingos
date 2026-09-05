# TradingOS Migration Audit v2

**Date:** 2026-07-21
**Purpose:** Map all lab modules to Control Plane. Prevent rebuilding. Guide integration.

---

## Current Control Plane (what exists)

```
control_plane/
├── state_collector.py          (Unified State)
├── models.py                   (TradingOSState)
├── adapters/
│   ├── position_adapter.py
│   ├── capital_adapter.py
│   ├── execution_adapter.py
│   └── research_adapter.py
├── capital/
│   ├── engine.py               (Capital Intelligence)
│   └── models.py
├── decision/
│   ├── engine.py               (Decision Engine v1)
│   ├── rules.py                (7 rules)
│   └── models.py
├── ceo/
│   ├── aggregator.py           (CEO Control Plane)
│   └── models.py
├── approval/
│   ├── approval_store.py       (Human Approval)
│   └── models.py
└── snapshots/
    └── current_state.json
```

**Total:** 5 layers, all read-only, all advisory.

---

## Lab Modules (what exists in tradingos_lab)

### 1. Capital Engine (`capital_engine/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `allocation.py` | 301 | `CapitalAllocationEngine` | `.allocate(edges)` → allocation plan |
| `__init__.py` | 390 | `CapitalEngine`, `CapitalScore`, `CapitalScoreV2` | `.score(portfolio)` → readiness score |

**Dependencies:** None external (pure Python)
**Relevance:** HIGH — provides risk budgeting, allocation logic, portfolio scoring
**Can reuse:** YES — adapter needed

### 2. Decision Engine (`decision_engine/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 98 | `DecisionEngine`, `Decision` | `.decide(capital, replay, shadow, robustness, trades, dd)` → Decision |

**Dependencies:** None external
**Relevance:** MEDIUM — already have control_plane/decision/ with adapted version
**Can reuse:** Parts (scoring logic) — already integrated

### 3. Reality Engine (`reality_engine/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 104 | `RealitySimulator`, `CostModel`, `RealityResult` | `.simulate(order, market)` → RealityResult |

**Dependencies:** None external
**Relevance:** HIGH — needed for Paper Execution cost modeling
**Can reuse:** YES — adapter needed

### 4. Paper Engine (`paper_engine/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 112 | `PaperEngine`, `PaperOrder`, `PaperPosition` | `.place_order(order)` → PaperPosition |

**Dependencies:** None external
**Relevance:** HIGH — needed for Paper Execution layer
**Can reuse:** YES — adapter needed

### 5. Portfolio Engine (`portfolio_engine/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 266 | `PortfolioEngine`, `PortfolioReport` | `.analyze(portfolio)` → PortfolioReport |
| `dashboard.py` | 138 | — | Dashboard generation |
| `stress.py` | 195 | `StressTest` | `.run(portfolio, scenarios)` → stress results |

**Dependencies:** None external
**Relevance:** HIGH — provides correlation, diversification, stress testing
**Can reuse:** YES — adapter needed

### 6. Risk Budget (`risk_budget/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 14 | wrapper around `capital_engine` | Re-exports RiskBudgetEngine |

**Dependencies:** `capital_engine`
**Relevance:** LOW — already covered by capital_engine
**Can reuse:** Skip (it's just a re-export)

### 7. Replay Engine (`replay_engine/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 314 | `ReplayEngine`, `ReplayResult`, `CandleReplay`, `OrderSimulator` | `.replay(strategy, data)` → ReplayResult |

**Dependencies:** None external
**Relevance:** MEDIUM — useful for backtesting, not needed for current Control Plane
**Can reuse:** LATER — not needed yet

### 8. Strategy Factory (`strategy_factory/`)

| File | Lines | Export | API |
|------|------:|--------|-----|
| `__init__.py` | 145 | Strategy templates | `.create(spec)` → strategy |
| `spec.py` | 53 | StrategySpecification | dataclass |
| `cli.py` | 74 | CLI | command-line interface |

**Dependencies:** None external
**Relevance:** LOW — strategy creation, not needed for Control Plane
**Can reuse:** LATER — not needed yet

---

## Target Architecture: TradingOS Core v2

```
                    TradingOS Core
                         |
         +---------------+---------------+
         |               |               |
    Capital Layer    Decision Layer   Execution Layer
         |               |               |
    ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
    │         │    │         │    │         │
 Capital  Risk   Decision  Rules  Paper   Reality
 Engine   Budget Engine           Engine   Engine
    │         │    │         │    │         │
    └────┬────┘    └────┬────┘    └────┬────┘
         |               |               |
         +---------------+---------------+
                         |
                    CEO Control
                         |
                    Human Approval
```

---

## Migration Priority

### Phase 1 — Capital Intelligence Enhancement (NOW)

| Lab Module | Target | Action |
|-----------|--------|--------|
| `capital_engine` | `control_plane/capital/` | Import `CapitalEngine` for risk scoring |
| `portfolio_engine` | `control_plane/capital/` | Import `PortfolioEngine` for concentration analysis |

**Why first:** Decision Engine needs risk budget data. Capital Intelligence already exists but lacks allocation logic.

### Phase 2 — Paper Execution (AFTER APPROVE)

| Lab Module | Target | Action |
|-----------|--------|--------|
| `paper_engine` | `control_plane/paper/` | Import `PaperEngine` for virtual execution |
| `reality_engine` | `control_plane/paper/` | Import `RealitySimulator` for cost modeling |

**Why second:** Paper Engine provides Action vs HOLD comparison without LIVE risk.

### Phase 3 — Portfolio Intelligence (AFTER PAPER)

| Lab Module | Target | Action |
|-----------|--------|--------|
| `portfolio_engine` | `control_plane/portfolio/` | Import full portfolio analysis |
| `replay_engine` | `control_plane/replay/` | Import for backtesting validation |

**Why third:** Portfolio analysis needs capital and paper data as input.

### Phase 4 — Strategy Layer (LATER)

| Lab Module | Target | Action |
|-----------|--------|--------|
| `strategy_factory` | Control Plane | Strategy templates for future strategies |
| `decision_engine` | Already integrated | Scoring logic already in control_plane/decision/ |

---

## What NOT to Migrate

| Module | Reason |
|--------|--------|
| `risk_budget` | Just a re-export of `capital_engine` |
| `strategy_factory` | Strategy creation, not Control Plane scope |
| `replay_engine` | Backtesting, needed later not now |
| `champion_engine` | Champion selection, not needed yet |

---

## Deduplication Map

| Existing in Control Plane | Lab equivalent | Action |
|--------------------------|---------------|--------|
| `control_plane/decision/` | `lab/decision_engine/` | KEEP adapted version |
| `control_plane/capital/` | `lab/capital_engine/` | ENHANCE with lab import |
| — | `lab/paper_engine/` | IMPORT as new layer |
| — | `lab/reality_engine/` | IMPORT as new layer |
| — | `lab/portfolio_engine/` | IMPORT as new layer |

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Lab modules have hidden dependencies | Test imports before integration |
| Lab modules expect different data formats | Create adapter layer |
| Breaking existing Control Plane | Never modify running layers, only add |
| LIVE contamination | All adapters read-only, no execution |

---

## Key Insight

**80% of Control Plane logic already exists in lab.**

The migration task is: **import + adapt**, not rewrite.

Remaining 20% (what we built new):
- PIE Bridge (stale filter)
- Action Shadow (PIE→Policy→Shadow)
- Outcome Trackers
- Unified State Layer
- CEO Control Plane
- Human Approval Layer
