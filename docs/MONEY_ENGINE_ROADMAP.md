# TradingOS Money Engine — Roadmap

## Core Thesis

Entry signals already have value. Problem is NOT finding new signals.
Problem is managing position lifecycle and preserving profit.

## Key Evidence

- Autopsy v2: 84% positions profitable at peak, 68% gave back profit
- Shadow Simulator v1.3: LOCK_05 (+1.00%) > TRAIL_03 (+0.54%) > NO_GUARDIAN (+0.00%)
- Current 5 positions confirmed giveback pattern in real-time

## Phases

### Phase 1 — Shadow Validation (CURRENT)
- Shadow Protection Simulator running
- Collecting 30-50 closed trades
- Comparing: NO_GUARDIAN vs LOCK_05 vs TRAIL_03
- NO real MOVE_SL yet

### Phase 2 — Minimal Real Action
- Enable ONLY MOVE_SL_BE
- No partial closes, no trailing, no averaging
- After statistical proof from Phase 1

### Phase 3 — Adaptive Guardian
- After 50+ trades
- Regime-based adaptation
- Dynamic risk scaling

## Frozen (DO NOT CHANGE)

- Entry strategies
- Risk parameters
- max_positions
- New indicators
- New strategies

## KPIs

1. **Profit Capture Ratio** = Realized PnL / MFE
2. **Giveback %** = (MFE - Final PnL) / MFE
3. **Guardian Improvement** = LOCK_05 PnL - NO_GUARDIAN PnL
4. **Drawdown Reduction**

## Architecture

```
Signal → Entry → Position Guardian → Protection Decision → Realized PnL
```

## Key Files

- `/opt/ubot_bingx/data/guardian_journal.jsonl` — live decision log
- `/opt/ubot_bingx/data/position_timeline.jsonl` — MFE/MAE per tick
- `/opt/ubot_bingx/data/shadow_protection_report_v13.jsonl` — simulation results
- `/opt/ubot_bingx/scripts/shadow_protection_simulator.py` — v1.3 simulator
