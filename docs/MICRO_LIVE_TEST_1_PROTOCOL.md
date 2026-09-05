# Micro Live Test #1 — Execution Protocol

**Test ID:** MICRO_LIVE_1
**Mode:** DRY RUN → MANUAL EXECUTION
**Date:** 2026-07-21
**Status:** READY

---

## 1. Pre-Trade Checklist

Before ANY trade, verify ALL checks pass:

| Check | Source | Required |
|-------|--------|----------|
| 🔄 Reconciliation | `core.state_reconciliation.cli` | SYNCED |
| 🚦 Safety Gate | `core.state_reconciliation.safety_gate` | ALLOW |
| 🛡️ RTS Guard | `core.state_reconciliation.rts_guard` | OK |
| ✅ Protection State | `core.state_reconciliation.protection_state` | FULLY_PROTECTED |
| 📊 Risk Governor | `control_plane.risk_governor.cli` | ALLOW |

```
python3 -c "
import asyncio
# Run checks — should all pass
"
```

If ANY check FAILS → NO TRADE.

---

## 2. Trade Lifecycle

### Stage 1: PENDING
- Run pre-trade checklist
- Log checks to `decision_log.jsonl`
- Set MICRO_LIVE_1 state = CREATED

### Stage 2: APPROVED
- Human confirms position parameters
- Symbol, side, size, SL, TP defined
- Approval recorded with timestamp
- Set MICRO_LIVE_1 state = APPROVED

### Stage 3: EXECUTED
- Manual entry on BingX
- Verify: position appeared on exchange
- RTS Governor evaluates: OPEN → PROTECTED
- Log: entry_time, entry_price, side, size
- Set MICRO_LIVE_1 state = EXECUTED

### Stage 4: PROTECTED
- Protection State confirms SL + TP
- RTS Governor: PROTECTED state
- Decision Log: SL confirmed, TP confirmed
- If AUTO SAFE activates MOVE_SL → verify TP PRESERVED
- Set MICRO_LIVE_1 state = PROTECTED

### Stage 5: MONITORED
- RTS Governor runs every cycle
- Decision Log records each evaluation
- Any WARNING/DEFENSIVE/EMERGENCY → log + alert

### Stage 6: CLOSED
- Manual close or TP/SL hit
- Reconciliation: SYNCED after close
- KPI: record TE/ESR/ERG sample
- Set MICRO_LIVE_1 state = CLOSED

---

## 3. Abort Conditions

| Condition | Action |
|-----------|--------|
| Reconciliation CONFLICT | ⛔ BLOCK |
| Missing SL after open | 🔴 ALERT + restore |
| Missing TP after open | 🔴 ALERT + restore |
| Exchange unreachable | 🟡 WARNING + wait |
| Unknown position change | 🔴 EMERGENCY freeze |
| Daily loss > 2% | ⛔ Stop all |

---

## 4. Parameters

| Parameter | Value |
|-----------|-------|
| Max positions | 1 |
| Risk per trade | 0.1-0.2% ($0.30-$0.66) |
| Max daily loss | 2% ($6.60) |
| Leverage | minimum (1-2x) |
| Kill switch | -5% ($16.60) |
| Max position size | $5-$10 |

---

## 5. Post-Trade Report

After close, generate:

```
# Micro Live Test #1 Report

## Lifecycle
- CREATED: timestamp
- APPROVED: timestamp
- EXECUTED: timestamp
- PROTECTED: timestamp
- CLOSED: timestamp

## Parameters
- Symbol, side, entry, SL, TP, size

## Protection Events
- SL set? yes/no
- TP set? yes/no
- MOVE_SL triggered? yes/no
- TP preserved after move? yes/no

## RTS Governor Decisions
- All decisions logged

## Result
- PnL: +/- X
- Fees: $X
- Max Adverse Excursion: X
- Max Favorable Excursion: X

## Conclusion
- PASS/FAIL
- What worked
- What needs improvement
```

---

## 6. Success Criteria

| Criterion | Target |
|-----------|--------|
| Position opened on BingX | ✅ Visible |
| Protection State confirms SL/TP | ✅ FULLY_PROTECTED |
| Reconciliation shows SYNCED after open/close | ✅ |
| Decision Log records all events | ✅ Complete |
| RTS Governor correct lifecycle | ✅ OPEN → PROTECTED → CLOSED |
| TP preserved (if MOVE_SL triggered) | ✅ Confirmed |

---

## 7. Controlled Components

| Component | Role | Mode |
|-----------|------|------|
| AUTO SAFE | SL protection only | ALLOWED |
| ubot_bingx | Scanner/observe | BLOCKED for execution |
| scalper-v6 | Bybit (independent) | NO OVERLAP |
| MT5 OrderManager | Forex demo only | SEPARATE |
| TradingOS | Decision + Control | ACTIVE |
