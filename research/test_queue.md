# Test Queue

## Sprint 1 — Current

| ID | Status | Started | Expected |
|----|--------|---------|----------|
| LS-001 | SHADOW LIVE | 2026-07-10 | 2026-07-12 |

## Sprint 2 — Next (after LS-001 verdict)

| Order | ID | Why now | Dependencies |
|-------|----|---------|--------------|
| 1 | FILTER-001 | Protects all future experiments | News calendar data |
| 2 | MR-001 | Complements LS-001 (different regime) | FeatureEngine (VWAP) |
| 3 | VOL-001 | Covers transition regime | FeatureEngine (BB width) |

## Sprint 3 — Conditional

| Order | ID | Trigger |
|-------|----|---------|
| 4 | SESSION-001 | If MR-001 shows edge on XAUUSD |
| 5 | TF-001 | If VOL-001 shows edge on trend days |

## Experiment Lifecycle

```
PLANNED → READY → SHADOW → REVIEW → ACCEPT/REJECT/REWORK
```

## Current Queue

```
[READY]    FILTER-001   News Avoidance     (needs: calendar data)
[PLANNED]  MR-001       VWAP Reversion     (needs: VWAP feature)
[PLANNED]  VOL-001      Compression Breakout (needs: BB width feature)
[PLANNED]  SESSION-001  Asian Range        (needs: session detector)
[PLANNED]  TF-001       EMA Pullback       (needs: EMA features)
```
