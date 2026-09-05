# TradingOS — Operating Mode: Measurement

**Effective from:** 2026-07-20T22:00Z
**Mode:** COLLECTING EVIDENCE (no code changes)
**Exit condition:** Action Shadow v0.1 reaches 20 unique approved actions AND 10 completed outcomes

---

## Active Experiments (FROZEN until exit condition)

### 1. Position Guard v1.1 — SHADOW-001
- Status: **FROZEN**
- Role: validates "when to protect profit via SL move" hypothesis
- Mode: SHADOW only, no execution
- See: `docs/EXPERIMENT_PG_v1.1.md`

### 2. Position Action Shadow v0.1
- Status: **COLLECTING**
- Role: validates "which actions have positive expected value" hypothesis
- Mode: SHADOW only, no execution
- Exit: 20 unique approved actions AND 10 completed outcomes

---

## Frozen Rules (do NOT change during collection)

### Position Guard v1.1
```
pnl_trigger:    1.0%
mfe_trigger:    1.5%
health_min:     80
protect_pct:    0.5%
actions:        HOLD, MOVE_SL, SKIPPED, IGNORE only
```

### Action Shadow v0.1
```
min_pnl_to_act: 0.5%
min_health:     60
max_rec_age:    4 hours
partial_pct:    25%
be_buffer:      0.05%
actions:        MOVE_SL_BE, TAKE_PARTIAL only
dedupe_window:  30 minutes (same symbol+side+action)
```

---

## Forbidden During Collection
- ❌ New action types
- ❌ New thresholds
- ❌ New signal sources
- ❌ Execution (any kind)
- ❌ HMAC / BingX read adapter
- ❌ Paper approval
- ❌ Telegram approval flow
- ❌ Optimization toward early results
- ❌ Changes to PG v1.1 rules
- ❌ Changes to Action Shadow v0.1 rules

---

## Daily Monitoring (5 metrics only)
1. Action Shadow unique approved count (target: 20)
2. Action Shadow completed outcomes (target: 10)
3. PG v1.1 data quality score (target: ≥90)
4. Capital Overview unrealized PnL trend
5. Service errors / restarts

---

## Post-Collection Decision Tree

```
IF  unique_approved >= 20
    AND  completed_outcomes >= 10
    AND  virtual_pnl_avg > 0
    AND  false_signal_rate < 25%
THEN
    → proceed to Paper Approval v0.1 (separate experiment)

ELIF
    completed_outcomes >= 10
    AND  virtual_pnl_avg between -0.5% and 0%
THEN
    → review Policy, run v0.2 experiment with tighter/wider rules

ELSE
    → freeze hypothesis, document negative result
    → do NOT iterate blindly
```

---

## Architecture Snapshot (as of 2026-07-20)

```
TradingOS Control Plane
├── PG v1.1 (frozen)         core/position/
├── Action Shadow v0.1       core/position/actions/
├── PIE Bridge v1.1          core/position/pie_bridge.py
├── Reconciler v0.1          core/position/reconciler.py
├── Decision Engine          core/position/decision.py
├── BingX Adapter v0.1       adapters/bingx/client.py
└── Tools (read-only)
    ├── capital_overview.py
    ├── position_guard_health.py
    ├── position_guard_dashboard.py
    ├── position_guard_report.py
    ├── position_guard_outcome.py
    ├── action_shadow_report.py
    ├── action_shadow_outcome.py
    └── bingx_reality_check.py  (awaits HMAC)

LIVE: /opt/ubot_bingx  (untouched)
PG:   /root/tradingos  (all code in /root/tradingos/)
```
