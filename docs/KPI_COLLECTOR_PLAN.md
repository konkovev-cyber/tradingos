# KPI Collector v1 — Implementation Plan

**Date:** 2026-07-21
**Phase:** 6
**Status:** ONLY ACTIVE DEVELOPMENT (per Control Plane v1 FROZEN)

---

## Contract (from `KPI_FRAMEWORK_INTEGRATION.md`)

```
Reality Engine
    │
    └── Local Signals
             │
             ▼
KPI Collector ← THIS DOCUMENT
    │
    └── TE / ESR / ERG (CANONICAL)
             │
             ▼
Decision Review (consumer only)
```

**KPI Collector is the SOLE producer of TE/ESR/ERG. No one else.**

---

## Scope

### IN:
* Read from Reality Engine output (`paper_simulation.json`, `reality_result.json`)
* Read from Action Shadow outcomes (`action_outcome_tracker`)
* Read from Decision Engine verdicts

### OUT:
* Write to `kpi_collector/state.json`
* Update TE/ESR/ERG with each new sample

### DOES NOT DO:
* ❌ Compute verdicts
* ❌ Modify Decision Review
* ❌ Touch LIVE / execution
* ❌ Define new KPIs
* ❌ Override existing values

---

## KPI Definitions (from K75)

### TE — True Expectancy
```
TE = mean(net_pnl_per_action) / gross_exposure
```
- Net of all costs (commission, spread, slippage, funding)
- Per-action basis
- ≥ 20 samples required

### ESR — Edge Survival Rate
```
ESR = % of regimes where net_pnl > 0
```
- Tracks stability across regime buckets
- Each closed action = 1 sample
- ≥ 20 samples required

### ERG — Execution Reality Gap
```
ERG = (gross_action_pnl - net_action_pnl) / gross_action_pnl
```
- Degradation from paper to reality
- Per-action basis
- ≥ 20 samples required

---

## File Structure

```
control_plane/kpi_collector/
├── models.py        — TE/ESR/ERG dataclasses
├── calculator.py    — pure computation from inputs
├── state.py         — persistent storage (state.json)
├── sampler.py       — sample management + dedup
├── cli.py           — entry point
└── state.json       — canonical KPI state
```

---

## State Schema

```json
{
  "schema_version": "v1",
  "last_updated": "2026-07-21T...",
  "te": {
    "status": "NOT_STARTED|COLLECTING|READY",
    "value": null,
    "samples": 0,
    "target_samples": 20
  },
  "esr": {
    "status": "NOT_STARTED|COLLECTING|READY",
    "value": null,
    "samples": 0,
    "regime_distribution": {}
  },
  "erg": {
    "status": "NOT_STARTED|COLLECTING|READY",
    "value": null,
    "samples": 0
  }
}
```

---

## Design Rules

1. **No computation backwards in time.** Each new sample is appended, never rewrite history.
2. **Samples are immutable.** Once a sample is recorded, it cannot be modified.
3. **Status transitions are explicit:**
   - `NOT_STARTED` → `COLLECTING` (first sample added)
   - `COLLECTING` → `READY` (samples >= 20)
   - `READY` → never demoted (samples only grow)
4. **No values before samples >= 20.** TE/ESR/ERG = null until READY.

---

## Integration with Decision Review

Decision Review already has `collect_kpi_samples()` function that counts action samples. After Phase 6:

- `collect_kpi_samples()` will READ from `kpi_collector/state.json`
- Decision Review logic is **unchanged** — it already gates on samples
- When ERG = READY in collector, Review will display READY

**No code change in Decision Review required.**

---

## Acceptance Criteria

- [ ] `python3 -m control_plane.kpi_collector.cli run` — single cycle, reads inputs, updates state
- [ ] After 20 valid samples, TE/ESR/ERG all = READY
- [ ] Decision Review shows READY without modification
- [ ] `state.json` persists between runs
- [ ] No file under `control_plane/` other than `kpi_collector/` is modified

---

## Out of Scope (Phase 7+)

- CAER (Cost-Adjusted Edge Ratio) — K75 mentions, defer
- RAE (Regime-Aligned Expectancy) — K75 mentions, defer
- Real-time streaming — defer
- Auto-cleanup of stale samples — defer
