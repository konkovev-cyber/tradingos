# Micro Live Runbook v1

**Date:** 2026-07-21
**Purpose:** Operational protocol for first real-money test under TradingOS control

---

## Pre-Trade Checklist

Before ANY trade, verify:

```
□ Reconciliation: SYNCED (no CONFLICT/DRIFT)
□ RTS Guard: OK (all checks passed)
□ Protection State: all positions FULLY PROTECTED
□ Risk Governor: ALLOW (daily loss OK, position limits OK)
□ Exchange: reachable
□ Balance: sufficient
```

If any check fails: **NO TRADE**

---

## Trade Lifecycle

### T0: Signal
- Scanner (ubot_bingx) finds candidate
- Action Shadow evaluates
- Protection State checks SL/TP

### T1: Pre-check
```
RTS Guard:      OK
Reconciliation: SYNCED
Risk Governor:  ALLOW
Protection:     FULLY PROTECTED
```

### T2: Open Position
- Minimal size (0.1-0.2% risk)
- 1 position only
- Log: symbol, side, entry, size, time

### T3: Monitor
- Protection State: check SL/TP every cycle
- RTS Guard: check balance, exchange, protection
- Log all state changes

### T4: AUTO SAFE Action (if triggered)
- MOVE_SL_BE_L1 activates
- Verify: old SL deleted, new SL created, **TP preserved**
- Log: before/after state

### T5: Close
- Manual close (or TP hit)
- Log: exit price, PnL, reason

### T6: Report
- KPI capture
- Reconciliation check
- State verification

---

## Abort Conditions

IMMEDIATELY stop if:
- CONFLICT detected
- Missing SL on any position
- Exchange unreachable
- Unexpected position change
- Balance below threshold

---

## Post-Trade Verification

After close, verify:
```
□ Exchange shows correct final state
□ TradingOS reconciliation shows SYNCED
□ KPI recorded
□ No orphaned orders
□ TP was preserved throughout
```

---

## Risk Limits

| Parameter | Value |
|-----------|-------|
| Max positions | 1 |
| Max risk per trade | 0.1-0.2% |
| Max daily loss | 2% |
| Kill switch | 5% |
| Leverage | minimum |
| Observation period | first cycle monitored |
