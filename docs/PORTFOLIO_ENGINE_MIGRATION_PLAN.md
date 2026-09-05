# Portfolio Intelligence Migration Plan

**Date:** 2026-07-21
**Phase:** 1.5

## Lab Module API

```
PortfolioEngine
  .analyze(edges: list[EdgeCandidate]) → PortfolioReport
  .suggest_allocation(edges, method='risk_parity') → dict

EdgeCandidate
  trades: int
  win_rate: float

CorrelationEngine
  .build_matrix(edges) → dict
  .correlate(a, b) → float

StressTestEngine
  (stress scenarios)
```

## Problem

Lab module expects **EdgeCandidate** (strategy-level).
Control Plane needs **position-level** analysis.

## Solution

Adapter that:
1. Converts positions → EdgeCandidate format
2. Runs PortfolioEngine for strategy readiness
3. Adds position-specific metrics (concentration, exposure)
4. Runs stress scenarios on actual portfolio

## New Fields

```json
{
  "portfolio_health": 64,
  "exposure": {"total": 0.73, "long": 0.52, "short": 0.21},
  "concentration": {"largest": "VELVET-USDT", "share": 0.796},
  "stress_scenarios": [...],
  "recommendations": [...]
}
```

## Risk: Low

- Read-only adapter
- No LIVE changes
- Existing v1 still works
