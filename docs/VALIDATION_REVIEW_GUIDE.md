# VALIDATION REVIEW GUIDE v1.0

This document defines exactly **how** a VALIDATION REVIEW is conducted and **what** decisions become possible after it completes.

It is the final element of the management loop:

```
CIO_OPERATING_DIRECTIVE_v1.md  →  principles (immutable)
CIO_PLAYBOOK.md                 →  daily procedures (evolves)
VALIDATION_REVIEW_GUIDE_v1.md   →  decision rules (immutable)
```

---

# When VALIDATION REVIEW is triggered

VALIDATION REVIEW starts when **all** of the following are true:

| Trigger | Threshold | Source |
|---------|-----------|--------|
| Closed trades (real, post-fix) | ≥ 50 | `/root/tradingos/logs/trades/trade_results.jsonl` |
| Operational stability | No critical failures in last 14 days | systemd journal, Guardian log |
| Guardian effectiveness data | At least 1 BE / Partial / Tight fired post-fix and closed | Guardian v3 report |
| Opportunity Cost analyzed | ≥ 100 rejected candidates evaluated | `opportunity_loss.evaluate_rejections()` |
| All 12 KPIs computed | Yes, in at least one report | CIO daily report |
| DECISION READINESS Statistical axis | ≥ 80% | scoreboard composite |

If any trigger is false → CONTINUE (wait for data).

If all triggers are true → VALIDATION REVIEW is open.

---

# Inputs to VALIDATION REVIEW

These 9 data points must be computed before the review can start.

| # | Metric | Source | Formula |
|---|--------|--------|---------|
| 1 | Closed trade count | trade_results.jsonl | `count()` |
| 2 | Expectancy | per-trade R values | `mean(R_i)` |
| 3 | Profit Factor | per-trade PnL | `sum(profit) / abs(sum(loss))` |
| 4 | Win Rate | per-trade outcomes | `wins / total` |
| 5 | Max Drawdown | equity curve | peak-to-trough on closed PnL cumulative |
| 6 | Average Holding Time | per-trade record | `mean(close_time - open_time)` |
| 7 | Guardian Net Value | Guardian v3 4-metric | `sum(benefit) - sum(missed_profit)` |
| 8 | Opportunity Cost | rejected candidates | per-rejection `would_win - would_lose` summary |
| 9 | Capital Growth | start vs current equity | `(current - 10.19) / 10.19 * 100` |

Each must be calculated from real data. Zero trade count → cannot run VALIDATION REVIEW.

---

# Quality gates on the inputs

Even if 50+ trades exist, VALIDATION REVIEW may be **invalid** if:

| Quality issue | Description | Action |
|---------------|-------------|--------|
| Insufficient N | < 30 trades | Defer review |
| Single regime bias | All trades in one market regime | Defer or flag |
| Critical operational failure | Guardian outage, exchange rejection, capital loss from system bug | Defer until failure analyzed |
| Mixed pre-fix and post-fix | Trades include both | Use only post-fix data |
| Survivorship bias | Only winners counted | Audit full trade_results.jsonl |
| Non-representative sample | All trades within 7 days (low diversity) | Extend observation window |

If any quality issue is found, the review is **deferred** with documented reason.

---

# Three possible decisions

VALIDATION REVIEW can result in exactly one of three decisions.

## SCALE

**Conditions (all required):**

- Expectancy > 0 AND statistically significant (p < 0.05)
- Profit Factor ≥ 1.5
- Max Drawdown < 20% of capital
- Win Rate consistent with Expectancy (no paradox)
- Average Holding Time is operational (not blocking turnover)
- Guardian Net Value ≥ 0 (positive or neutral)
- No critical operational failures during the review period
- Capital Growth positive over review period

**Result:** strategy is allowed to scale.

**Action plan:**
- Increase `risk_per_trade` by 25% per month, max 4× current
- Maintain `max_positions` until PF improves further
- Re-evaluate at +50 more closed trades

---

## TUNE

**Conditions (any one):**

- Expectancy > 0 but local factor drags results (e.g., Guardian Net Value < 0)
- Profit Factor between 1.0 and 1.5 (positive but weak)
- Max Drawdown between 20% and 35%
- Average Holding Time too long (> 4 days consistently)
- Opportunity Cost shows high-value rejections
- Statistical significance borderline

**Result:** strategy is valid but needs local correction.

**Action plan:**
- Identify ONE specific parameter to change (Guardian threshold, SL/TP ratio, etc.)
- Change ONE parameter at a time
- Test new parameter on N=20 minimum new closes
- Compare before/after metrics
- Roll back if improvement not confirmed
- Re-review after 50 new trades

---

## STOP

**Conditions (any one):**

- Expectancy < 0 sustained (last 30 trades negative)
- Profit Factor < 1.0
- Max Drawdown > 35% or unrecoverable
- Guardian Net Value strongly negative (Guardian consistently hurts)
- Critical operational failure caused real capital loss
- Statistical evidence shows no edge even after 50+ trades

**Result:** strategy is archived. Project reverts to research phase.

**Action plan:**
- Archive trade_results.jsonl, Guardian state, opportunity_loss logs
- Move config files to `archive/` with timestamp
- Stop all trading services
- Document WHY the strategy was stopped
- Begin new hypothesis generation phase

---

# Decision tree

```
Start
  │
  ├── Expectancy > 0?  ── No ──> STOP
  │     │
  │     Yes
  │
  ├── PF ≥ 1.5 AND DD < 20% AND Guardian ≥ 0?  ── Yes ──> SCALE
  │     │
  │     No
  │
  ├── Expectancy > 0 AND PF ≥ 1.0 AND DD < 35%?  ── Yes ──> TUNE
  │     │
  │     No
  │
  └── STOP
```

---

# Decision output format

VALIDATION REVIEW must produce a single document:

```text
/root/tradingos/docs/VALIDATION_REVIEW_YYYY-MM-DD.md
```

Contents:

1. Trigger checklist (all 6 conditions met)
2. Input data summary (all 9 metrics)
3. Quality gates check (no defer reasons)
4. Decision (SCALE / TUNE / STOP)
5. Justification (which conditions met / failed)
6. Action plan (next steps)
7. Re-review date (if SCALE or TUNE)

---

# Forbidden during VALIDATION REVIEW

- ❌ Modify strategy mid-review
- ❌ Cherry-pick favorable data points
- ❌ Apply different criteria than those in this document
- ❌ Defer indefinitely to "collect more data" without a defined criterion
- ❌ Override decision based on single recent trade
- ❌ Change criteria to match preferred outcome

---

# Re-review schedule

After each decision, the next review is scheduled:

| Decision | Next review | Trigger |
|----------|-------------|---------|
| SCALE | +50 closed trades | automatic |
| TUNE | +50 closed trades after parameter change | automatic |
| STOP | n/a | archived |

If a SCALE or TUNE review returns STOP, the cycle ends.

---

# Compliance with policy

This guide implements `CIO_OPERATING_DIRECTIVE_v1.md`. Specifically:

- "no decisions by single trade" — enforced by 50-trade minimum
- "decisions only by statistics" — enforced by 9-metric inputs
- "decision must move toward SCALE/TUNE/STOP" — enforced by 3-decision tree
- "evidence separated from assumptions" — enforced by quality gates section
- "no opinion-based decisions" — enforced by criteria-based decision tree

---

# Versioning

This is `v1.0`. Future versions must follow the rules in `CIO_OPERATING_DIRECTIVE_REGISTRY.md`.

Updates to this guide require explicit decision and new version file.
