# RTS Guard v0 — GAP Analysis

**Date:** 2026-07-21
**Purpose:** Compare current Safety Gate with full RTS Guard requirements

---

## What EXISTS (Safety Gate + Risk Governor)

| Check | Status | Source |
|-------|--------|--------|
| State Sync (SYNCED/DRIFT/CONFLICT) | ✅ DONE | Safety Gate v1 |
| Daily Loss Limit (2%) | ✅ DONE | Risk Governor |
| Kill Switch (5%) | ✅ DONE | Risk Governor |
| Max Positions (3) | ✅ DONE | Risk Governor |
| Max Position Size (5%) | ✅ DONE | Risk Governor |
| Consecutive Loss Block (3) | ✅ DONE | Risk Governor |
| Portfolio Classification | ✅ DONE | Risk Governor |
| BLOCK on CONFLICT | ✅ DONE | Safety Gate v1 |
| WARN on DRIFT | ✅ DONE | Safety Gate v1 |

---

## What DOES NOT EXIST (RTS Guard v0)

| Check | Status | Priority |
|-------|--------|----------|
| Balance verification before action | ❌ NOT DONE | ⭐⭐⭐⭐ |
| Missing SL detection | ❌ NOT DONE | ⭐⭐⭐⭐⭐ |
| Exchange disconnect detection | ❌ NOT DONE | ⭐⭐⭐⭐ |
| Runtime monitoring loop (periodic) | ❌ NOT DONE | ⭐⭐⭐⭐⭐ |
| Abnormal move detection (ATR spike) | ❌ NOT DONE | ⭐⭐⭐ |
| Position age tracking | ❌ NOT DONE | ⭐⭐⭐ |
| Funding cost monitoring | ❌ NOT DONE | ⭐⭐ |

---

## GAP Summary

**60% of RTS Guard is done** (via Safety Gate + Risk Governor).

**40% missing:**
1. **Runtime monitoring loop** — periodic checks on open positions
2. **Missing SL detection** — alert if position has no stop loss
3. **Exchange disconnect detection** — alert if can't reach BingX
4. **Balance verification** — check balance before any action

---

## What RTS Guard v0 Should Add

### Runtime Monitor (periodic loop)
```python
# Every 60 seconds:
for position in positions:
    check_missing_sl(position)
    check_position_age(position)
    check_exchange_reachable()
    check_balance_sufficient()
```

### Missing SL Detection
```python
if position.stop_loss is None:
    alert("NO STOP LOSS on {position.symbol}")
    block_new_actions()
```

### Exchange Reachability
```python
if not ping_exchange():
    alert("EXCHANGE UNREACHABLE")
    block_new_actions()
```

### Balance Check
```python
if balance < min_balance:
    alert("LOW BALANCE")
    block_new_actions()
```

---

## Recommendation

**Don't build full RTS Guard today.** Build minimal runtime monitor:
1. Missing SL detection (critical)
2. Exchange reachability (critical)
3. Balance check (important)

These 3 checks + existing Safety Gate + Risk Governor = functional RTS Guard v0.
