# State Reconciliation Audit

**Date:** 2026-07-21
**Purpose:** Identify gaps between TradingOS expected state and BingX reality

---

## Current State Sources

| Source | File | Role | Fields |
|--------|------|------|--------|
| BingX Read Adapter | `bingx_read/real_account_state.json` | Ground truth | symbol, side, qty, entry, mark, pnl, leverage, SL, TP |
| Position Snapshot | `core/position/models.py` | Internal state | symbol, side, entry, mark, qty, pnl, stop_price |
| Portfolio Classification | `risk_governor/portfolio_classification.json` | Labels | legacy/experiment, owner, status |
| KPI Collector | `kpi_collector/state.json` | KPI samples | te, esr, erg counts |
| Position Guard | `core/position/position_guard.py` | Guard decisions | actions, outcomes |

---

## What Exists

### Existing Reconciler (`core/position/reconciler.py`)
- Checks: symbol EXISTS in both PIE and BingX
- Does NOT compare: entry_price, qty, side, SL, TP
- Result: PASS/FAIL based on symbol presence only

### PositionSnapshot (`core/position/models.py`)
- Fields: timestamp, exchange, symbol, side, entry_price, mark_price, pnl_pct, mfe_pct, health_score, qty, stop_price, position_id, status
- **Missing:** take_profit, leverage, position_id (often empty)

### BingX Read Adapter (`bingx_read/client.py`)
- Returns: symbol, side, entry_price, mark_price, qty, unrealized_pnl, pnl_ratio, leverage, stop_loss, take_profit, position_id
- **Has all fields needed for reconciliation**

---

## Conflict Cases (from this session)

### Case 1: ARBUSDT LONG → SHORT
- Expected: LONG qty=36.7
- Actual: SHORT qty=36.30
- **TradingOS did NOT detect this change**
- Root cause: No field-level comparison, only symbol check

### Case 2: bot_state.db vs BingX API
- bot_state.db: 12 positions (ALLO, BANK, ESPORTS...)
- BingX API: 9 positions (ARB, ATOM, BCH...)
- **Zero overlap** — completely different states
- Root cause: bot_state.db is stale, BingX API is ground truth

### Case 3: Position with no TP
- ARB had SL=0.0899 but NO TP
- TradingOS detected via Profit Watch
- But couldn't execute TP (API signature error)
- Operator manually closed and reopened SHORT

---

## Missing for Reconciliation

| What | Status | Priority |
|------|--------|----------|
| Expected state storage | ❌ Not implemented | ⭐⭐⭐⭐⭐ |
| Field-level comparison | ❌ Only symbol check | ⭐⭐⭐⭐⭐ |
| Change detection | ❌ No LONG→SHORT detection | ⭐⭐⭐⭐⭐ |
| Decision invalidation | ❌ No lifecycle | ⭐⭐⭐⭐ |
| State versioning | ❌ No version tracking | ⭐⭐⭐ |

---

## Proposed v1 Schema

### Position State (expected)
```json
{
  "symbol": "ARB-USDT",
  "side": "LONG",
  "qty": 36.7,
  "entry_price": 0.09001,
  "stop_loss": 0.0899,
  "take_profit": null,
  "source": "TRADINGOS",
  "version": 1,
  "last_synced": "2026-07-21T07:12:32Z"
}
```

### Position State (actual from BingX)
```json
{
  "symbol": "ARB-USDT",
  "side": "SHORT",
  "qty": 36.30,
  "entry_price": 0.09084,
  "stop_loss": 0.09226,
  "take_profit": 0.08908,
  "source": "BINGX_API",
  "version": 2,
  "last_synced": "2026-07-21T07:36:08Z"
}
```

### Reconciliation Result
```json
{
  "symbol": "ARB-USDT",
  "status": "MISMATCH",
  "changes": [
    {"field": "side", "expected": "LONG", "actual": "SHORT"},
    {"field": "qty", "expected": 36.7, "actual": 36.30},
    {"field": "entry_price", "expected": 0.09001, "actual": 0.09084},
    {"field": "take_profit", "expected": null, "actual": 0.08908}
  ],
  "action": "INVALIDATE_DECISIONS",
  "timestamp": "2026-07-21T07:36:08Z"
}
```

---

## Conclusion

The existing reconciler is a **symbol-level check**, not a **field-level reconciliation**. This is the root cause of the ARBUSDT desync. State Reconciliation v1 must add field-level comparison and change detection.
