# Capital Engine Integration — Migration Plan v1

**Date:** 2026-07-21
**Phase:** 1 of Core Integration
**Goal:** Enhance Capital Intelligence with risk scoring from lab capital_engine

---

## What We Import

From `tradingos_lab/capital_engine/`:
- `CapitalEngine` class (methods: `calculate`, `score_v2`, `score_risk`, `score_stability`)
- `CapitalScore` dataclass (total, status, breakdown)
- `CapitalScoreV2` dataclass (total, status, breakdown)

## What We Do NOT Import
- Allocation logic (not needed yet — no multi-strategy portfolio)
- Campaign logic (lab-specific)
- Any database dependencies

## How It Maps to Control Plane

```
Current:
  tradingos_state.json → capital_intelligence.json (basic PnL + risk)

After:
  tradingos_state.json → capital_intelligence_v2.json (PnL + risk + CapitalEngine score)
```

## New Data Fields

```json
{
  "capital_score": {
    "total": 72,
    "status": "CONDITIONAL",
    "risk_score": 45,
    "stability_score": 90,
    "breakdown": {...}
  },
  "portfolio_health": "WARNING",
  "concentration_alerts": [...],
  "allocation_recommendation": "REDUCE_CONCENTRATION"
}
```

## Adapter Design

```python
class CapitalEngineAdapter:
    """Read-only bridge between Control State and lab CapitalEngine."""
    
    def score_portfolio(self, state: dict) -> CapitalScore:
        """Convert tradingos_state.json → CapitalEngine inputs → CapitalScore"""
    
    def score_risk(self, capital_data: dict) -> float:
        """Use CapitalEngine.score_risk for risk assessment"""
    
    def score_stability(self, execution_data: dict) -> float:
        """Use CapitalEngine.score_stability for service health"""
```

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| CapitalEngine expects different data format | Adapter maps fields |
| score_v2 has many optional params | Pass only available data, rest defaults |
| Breaking existing capital_intelligence.json | Create v2, keep v1 as fallback |

## Definition of Done

- [ ] `capital_engine` imports successfully in Control Plane
- [ ] `CapitalEngineAdapter` reads from `tradingos_state.json`
- [ ] `capital_intelligence_v2.json` contains CapitalEngine scores
- [ ] Existing `capital_intelligence.json` still works (backward compatible)
- [ ] Decision Engine can read v2 data
- [ ] No changes to LIVE, PG, or Action Shadow
