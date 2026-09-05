# Hypothesis Queue

**Status:** ACTIVE (no changes allowed to current experiments until Decision Review)
**Created:** 2026-07-20T22:00Z
**Rule:** Hypotheses below are FROZEN during measurement. They become eligible ONLY after Decision Review of PG-v1.1-SHADOW-001 and Action Shadow v0.1.

---

## HYP-001: Wider Health Threshold for Action Shadow
- **Hypothesis:** `Health >= 70` вместо `60` улучшит EV
- **Rationale:** Слишком много слабых позиций проходят Policy
- **Estimated impact:** fewer approvals, potentially higher quality
- **Status:** QUEUED (awaiting baseline data)

## HYP-002: Larger Partial Close
- **Hypothesis:** `TAKE_PARTIAL 50%` лучше `25%`
- **Rationale:** 25% может быть слишком мало для фиксации
- **Estimated impact:** faster profit capture, less upside retained
- **Status:** QUEUED (awaiting baseline data)

## HYP-003: Tighter MOVE_SL_BE Rec Age
- **Hypothesis:** `max_rec_age = 2h` вместо `4h`
- **Rationale:** PIE рекомендации, которым больше 2 часов, теряют актуальность
- **Estimated impact:** fewer approvals, fresher actions
- **Status:** QUEUED (awaiting baseline data)

## HYP-004: Dedupe Window Increase
- **Hypothesis:** `dedupe_window = 45 min` вместо `30 min`
- **Rationale:** PIE повторяет каждые 60с, 30 мин может не покрыть полный цикл
- **Estimated impact:** fewer duplicates, cleaner evidence
- **Status:** QUEUED (awaiting baseline data)

---

## How to add new hypotheses

1. Write it here with ID (HYP-NNN)
2. Do NOT change any experiment code
3. Wait for Decision Review
4. After review: either create new experiment v0.2 or close hypothesis

## Decision Review conditions

```
20 unique approved actions
+
10 completed outcomes

THEN:
  - Review EV
  - Review false signal rate
  - Review data quality
  - Decide: approve, modify, or close hypothesis
```
