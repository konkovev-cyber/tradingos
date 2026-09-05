# DUAL-STREAM CIO DASHBOARD — TEMPLATE v2.0

This template auto-fills when both Reality Pilots have sufficient closed trades.

**CIO Governance v2** — multi-criteria capital allocation with qualification gate, stream score, confidence level, and progressive scaling.

---

# CIO CAPITAL ALLOCATION — DECISION FLOW

```
Reality Pilot (each stream)
        │
        ▼
VALIDATION REVIEW
        │
        ▼
QUALIFICATION GATE
   (minimum requirements)
        │
        ▼
STREAM SCORE
   (multi-criteria weighted)
        │
        ▼
CAPITAL ALLOCATION
   (between qualified streams)
        │
        ▼
PROGRESSIVE SCALING
   (staged, with revalidation)
        │
        ▼
REVALIDATION
   (after each stage)
```

---

# Section A — Per-Stream Results

## A.1 Bybit Reality Pilot

```
Closed trades:         ____ (need ≥50)
Expectancy:            ____ R
Profit Factor:         ____
Win Rate:              ____ %
Max Drawdown:          ____ %
Average Holding Time:  ____ hours
Guardian Net Value:    ____ ($)

Verdict (per VALIDATION_REVIEW_GUIDE):
  [ ] SCALE   (PF≥1.5, DD<20%, Guardian≥0)
  [ ] TUNE    (PF 1.0–1.5 or local factor issue)
  [ ] STOP    (PF<1.0 sustained)
```

## A.2 MT5 Reality Pilot

```
Closed trades:         ____ (need ≥50)
Expectancy:            ____ R
Profit Factor:         ____
Win Rate:              ____ %
Max Drawdown:          ____ %
Average Holding Time:  ____ hours
Guardian Net Value:    ____ ($)

Verdict:
  [ ] SCALE
  [ ] TUNE
  [ ] STOP
```

---

# Section B — Qualification Gate

Before any stream can receive additional capital, it must pass ALL of the following:

```
QUALIFICATION GATE

Required:
  ✓ Sufficient closed trades (≥50)
  ✓ Expectancy > 0
  ✓ Profit Factor > 1
  ✓ Max Drawdown within limit (<20% for SCALE, <35% for TUNE)
  ✓ Operational Reliability above target (services active, no critical failures)
  ✓ Guardian Net Value ≥ 0 (non-negative)

Result:
  [ ] QUALIFIED — eligible for capital allocation
  [ ] NOT QUALIFIED — remain in current phase (or TUNE/STOP per review)
```

If a stream fails Qualification Gate, it receives **zero** new capital until the next review.

---

# Section C — Stream Score (Multi-Criteria)

For each qualified stream, compute a weighted score:

```
CIO STREAM SCORE

Metric                         Weight    Score (0-100)    Weighted
─────────────────────────────────────────────────────────────────
Expectancy                     35%       ____             ____
Profit Factor                  25%       ____             ____
Max Drawdown (inverted)        20%       ____             ____
Operational Reliability        10%       ____             ____
Guardian Net Value             10%       ____             ____
─────────────────────────────────────────────────────────────────
TOTAL SCORE                   100%                       ____
```

### Scoring rules

| Metric | 0 points | 50 points | 100 points |
|--------|----------|-----------|------------|
| Expectancy | ≤ 0 R | 0.5 R | ≥ 1.0 R |
| Profit Factor | ≤ 1.0 | 1.5 | ≥ 2.0 |
| Max Drawdown (inverted) | ≥ 35% | 20% | ≤ 5% |
| Operational Reliability | < 80% uptime | 95% uptime | 100% uptime, 0 errors |
| Guardian Net Value | < 0 (negative) | 0 (neutral) | > 0 (positive) |

Linear interpolation between breakpoints.

---

# Section D — Confidence Level

Adjust allocation based on statistical confidence:

```
CONFIDENCE LEVEL

Closed trades:
  0–20     LOW       — restrict to pilot capital only
  21–50    MEDIUM    — allow Stage 1 scaling
  51–100   HIGH      — allow Stage 2 scaling
  100+     VERY HIGH — allow full target allocation
```

Even if Stream Score is high, LOW confidence caps the scaling stage.

---

# Section E — Capital Allocation

Capital is distributed **only between qualified streams**, proportional to their Stream Score.

```
Example:

Bybit Score = 84
MT5 Score   = 61

Total qualified score = 84 + 61 = 145

Bybit allocation = 84 / 145 = 58%
MT5 allocation   = 61 / 145 = 42%
```

If only one stream qualifies → 100% to that stream.

If neither qualifies → STOP both, no capital increase.

---

# Section F — Progressive Scaling

Even after SCALE is approved, capital increases in stages:

```
Stage 1 — Pilot capital (current)
  Amount:  $10
  Revalidation: after 50 closed trades

Stage 2 — 2–3× pilot
  Amount:  $20–$30
  Revalidation: after 50 more closed trades

Stage 3 — 5× pilot
  Amount:  $50
  Revalidation: after 100 more closed trades

Stage 4 — Target allocation
  Amount:  full allocation per Stream Score
  Revalidation: periodic
```

**Transition rule:** A stream can only advance to the next stage if it passes Qualification Gate again at the current stage. If metrics degrade, scaling stops or rolls back.

---

# Section G — Operational Health

```
Bybit services:
  Scanner:     [active/inactive]
  Guardian v3: [active/inactive]
  Telegram:    [active/inactive]
  Uptime:      ____ hours
  Errors:      ____ / 24h

MT5 services:
  Bridge:      [active/inactive]
  Guardian:    [active/inactive]
  Reconnects:  ____ / 24h
  Errors:      ____ / 24h
```

---

# Section H — Final CIO Decision

```
Decision:     _________________ (SCALE / TUNE / STOP / MIXED)
Capital split: Bybit ____% / MT5 ____%
Scaling stage: ____ (1 / 2 / 3 / 4)
Effective:    _________
Risk budget:  _________
Re-review:    after next ____ closed trades
```

---

# Section I — Decision Justification

Required:
1. What changed since last review?
2. Which stream(s) passed Qualification Gate?
3. What's the asymmetric risk if decision is wrong?
4. What's the trigger for next review?

---

# Status

This template is a **planning artifact**. It does not run automatically.
CIO fills it manually when both streams have sufficient data.

Until then: empty.
