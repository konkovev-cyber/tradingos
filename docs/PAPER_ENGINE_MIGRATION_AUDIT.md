# Paper Engine Migration Audit

**Date:** 2026-07-21
**Phase:** 2

## Lab Module API

```python
PaperEngine(initial_equity: float = 10000.0)
  .place_order(symbol, side, qty, order_type, price) → PaperOrder
  .fill_order(order_id, fill_price, fill_qty) → bool
  .mark_to_market(prices: dict) → None
  .summary() → dict (equity, cash, unrealized, realized, positions, orders)

PaperOrder:
  order_id, symbol, side, qty, order_type, price, status, filled_qty, avg_fill_price

PaperPosition:
  symbol, side, qty, entry_price, current_price, unrealized_pnl, realized_pnl
```

## Design: Control Plane Paper Layer

```
control_plane/paper/
├── adapter.py      — wraps PaperEngine
├── simulator.py    — scenario runner: ACTION vs HOLD
├── outcome.py      — compute delta PnL
├── models.py       — dataclasses
├── cli.py          — run scenarios
```

## Core Question

> "If TradingOS acted on this decision, how much would it save vs HOLD?"

## Scenarios

1. **HOLD baseline**: keep position, let it close at actual exit
2. **MOVE_SL_BE**: virtual move SL to breakeven
3. **TAKE_PARTIAL**: virtual close 25% at signal price

## Output

```json
{
  "scenario": "MOVE_SL_BE on VELVET",
  "hold_pnl": 38.65,
  "action_pnl": 51.20,
  "delta": +12.55,
  "verdict": "ACTION_BETTER"
}
```

## Integration

- Input: `tradingos_state.json` + approval request
- Action: create virtual paper position, simulate scenarios
- Output: `paper_simulation.json`

## Rules

- NO exchange API
- NO /opt/ubot_bingx changes
- NO real orders
- ONLY virtual simulation
