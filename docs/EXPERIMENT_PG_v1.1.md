# Experiment: Position Guard v1.1 — SHADOW-001

## Identity
- **Experiment ID:** `PG-v1.1-SHADOW-001`
- **Mode:** SHADOW ONLY (no live execution)
- **Start:** 2026-07-20T21:37:00Z
- **Owner:** TradingOS Control Plane
- **Status:** COLLECTING EVIDENCE

## Frozen Rules (Decision Engine)
The following parameters and logic MUST NOT change during this experiment:

```yaml
decision_engine:
  pnl_trigger: 1.0       # %
  mfe_trigger: 1.5       # %
  health_min: 80.0
  protect_profit_pct: 0.5

actions:
  allowed:
    - HOLD
    - MOVE_SL
    - SKIPPED
    - IGNORE
  forbidden:
    - CLOSE_POSITION
    - REVERSE
    - ADD_POSITION

execution:
  mode: SHADOW
  modify_stop_in_live: false
  simulate_modify_stop: true
```

## Data Pipeline (Frozen)
```
PIE DB
  → PIE Bridge (freshness 30m + position_summary check)
  → Position Reconciler (BingX reality cross-check, currently empty)
  → Decision Engine
  → JSONL Shadow Log
  → Outcome Tracker (read-only)
```

## Frozen Validation
- **Stale threshold:** 30 minutes without `BAR_UPDATE` for a `position_id` → `STALE`
- **Closed confirmation:** PIE `position_summary.closed_at >= snapshot.timestamp_utc` → `CLOSED_CONFIRMED`
- **Absurd value filter:** `abs(pnl_pct) > 100` or `abs(mfe_pct) > 100` → skip
- **Incomplete record filter:** missing `symbol`/`position_id`/`timestamp_utc` → skip

## Baseline (frozen at experiment start)
```
Snapshots total:    198
Fresh:              188  (95.0%)
Skipped:            10   (5.1%)
Data quality score: 92.4/100

Decisions:
  HOLD:     175  (88.4%)
  MOVE_SL:  13  ( 6.6%)   # all ADA-USDT, pre-stale-filter
  SKIPPED:  10  ( 5.1%)   # ADA + BCH, blocked by freshness

MOVE_SL signal quality (historical):
  avg health: 97.3
  avg MFE:    +2.04%
```

## Exit Condition
Experiment ends when ONE of the following is true:

1. **20 valid `MOVE_SL` signals** where:
   - `validation.status` ∈ {ACTIVE, OK}
   - `position_id` is present and non-empty
   - The position was confirmed ACTIVE at signal time

2. **5 closed positions after a valid `MOVE_SL` signal** (with computable `saved_pct`)

## Daily Monitoring (5 metrics only)
1. Data quality score (target: ≥ 90)
2. Fresh / stale ratio
3. Valid `MOVE_SL` count
4. Outcome Tracker results (when applicable)
5. Errors / restarts of `position-guard.service`

## Forbidden During Experiment
- No changes to Decision Engine parameters
- No new actions
- No Paper Execution
- No BingX HMAC adapter
- No Telegram approval flow
- No new validation rules

## Post-Experiment Decision Tree
```
IF  average saved_pct > 0.5% over 20+ signals
    AND  false_signal_rate < 15%
    AND  data_quality_score >= 90
THEN
    proceed to Paper Execution stage
ELIF
    average saved_pct between 0 and 0.5%
    AND  false_signal_rate < 25%
THEN
    tighten / loosen rules and run experiment v1.2
ELSE
    freeze, document negative result
```
