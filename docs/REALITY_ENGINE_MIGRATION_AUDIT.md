# Reality Engine Migration Audit

**Date:** 2026-07-21
**Phase:** 3

## Lab Module API

```python
CostModel:
  maker_fee, taker_fee, spread_bps, slippage_bps, latency_ms, fill_rate

RealitySimulator(costs: CostModel)
  .simulate(backtest_pf, total_trades, avg_hold_hours) → RealityResult
  .sensitivity(backtest_pf, total_trades) → list[dict]
```

RealityResult:
- backtest_pf, after_fees_pf, after_spread_pf, after_slippage_pf, after_latency_pf
- final_pf, degradation_pct, status (PASS/MARGINAL/FAIL), details

## Design

Reality Engine adapter для Control Plane:
- Input: paper_result (gross PnL), position size, action type
- Cost model: lab default + BingX-specific adjustments
- Output: net PnL, cost breakdown, reality verdict

## BingX-Specific Cost Model (BingX Futures)

- Taker fee: 0.06% (0.0006)
- Maker fee: 0.02% (0.0002)
- Typical spread: 0.5 bps
- Slippage: 1.0 bps (market orders)
- Latency: 50-100ms
- Fill rate: 95-99%

## Output Format

```json
{
  "gross_result": 43.92,
  "costs": {
    "commission": 0.15,
    "slippage": 0.10,
    "funding": 0.02
  },
  "net_result": 43.65,
  "vs_hold": -0.27,
  "recommendation": "ACTION_BETTER" | "HOLD_BETTER" | "NEUTRAL"
}
```

## Rules

- NO exchange API
- NO live orders
- Pure cost simulation
- Math only
