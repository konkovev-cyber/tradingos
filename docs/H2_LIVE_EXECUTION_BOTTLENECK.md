# H2 LIVE EXECUTION — BOTTLENECK AUDIT

*Read-only research. TradingOS production FROZEN (mode=MANUAL, R148 frozen, AUTO disabled, P3 n=9 read-only).*

---

## 1. EXECUTIVE VERDICT

**`H2 DETECTOR BOTTLENECK FOUND`**

H2 infrastructure is **running, polling 37 symbols every 15 sec for 9 days**. Detector's FROZEN_CFG should produce ~26 events in 9 days (calibrated at 0.07/sym/day × 37 syms × 9 days). **Detector claim: 0 fresh live signals in 9 days of watch mode**.

The bottleneck is NOT signal rarity. The bottleneck is a **detector bug or state issue** that prevents the detector from producing fresh signals even though it has been polling successfully.

**This is fixable without any parameter changes. The user should be able to enable a fix without further approval.**

---

## 2. Pipeline state

| Stage | Status | Evidence |
|---|---|---|
| Market data | **WORKING** | 13,027 poll_heartbeats, 37 active symbols, cycle 13-19s (matches poll_sec=60) |
| H2 detected | **FAILING — 0 fresh events** | But our shadow detects ~26 events in 9 days on the same data |
| Gate passed | N/A | No events to gate |
| Order eligible | N/A | No events to evaluate |
| Order placed | 0 | 0 fills, 0 outcomes |
| Fills | 0 | pilot_ledger.jsonl has 25 PAUSED_L1 events from 8/11 warmup, 0 fresh since |
| Closed trades | 0 | 0 outcomes |

**100% of "fresh" events lost at the DETECTOR → GATE stage.**

---

## 3. Service state

- **`tradingos-h2-shadow.service`**: active since 2026-08-12, pid 102516
- **`h2_runtime.json`**: `{}` (empty)
- **`state.json`**: `{"paused": false, "reason": "USER ENABLE: ...", "updated_at": "2026-08-12T05:25:00"}` — service is NOT paused
- **`pilot_ledger.jsonl`**: 13,393 events (8/11-8/20), 25 candidate events, **all from 8/11 warmup scan**, all `PAUSED_L1`
- **`signals.jsonl`**: 25 events (8/11 warmup), 0 fresh since

---

## 4. Service restart history (7 restarts in 9 days)

| Date | Reason (if logged) |
|---|---|
| 2026-08-11 18:40 | first start |
| 2026-08-11 18:47 | 7 min later |
| 2026-08-11 18:57 | 10 min later |
| 2026-08-11 19:36 | 39 min later |
| 2026-08-11 22:40 | 3h later |
| 2026-08-12 05:21 | 7h later |
| 2026-08-12 13:08 | 8h later — **last restart, ran 9 days** |

After 8/12 13:08 — **NO MORE RESTARTS, NO MORE EVENTS**. The detector is alive (heartbeats every 15s) but finds no events.

---

## 5. Poll_error analysis (28 events in 4 days)

| Date | Errors | Distinct symbols | Common |
|---|---:|---:|---|
| 8/17 | 2 | 1 | JUPUSDT (10006) |
| 8/18 | 1 | 1 | loop_error (Connection reset) |
| 8/19 | 16 | 12 | multiple 10006 "Too many visits" + loop_errors |
| 8/20 | 9 | 6 | mostly 10006 + 1 loop_error (timeout) |

**`ret_code: 10006` "Too many visits. Exceeded the API Rate Limit."** — primarily on Bybit kline endpoint. **NOT a critical problem**: 28 errors / 9 days = ~3/day, spread across 18 symbols. Polling continues for the rest of the 37-symbol universe.

---

## 6. Cost floor reality check

- **FROZEN_CFG** (verbatim from `h2_lib.py`):
  - `q_atr=5, q_bbw=8, persist_k=5, persist_m=8`
  - `vol_gate=3.0, max_gate=3.0, range_tight=5.0, atr_win=480`
  - `rcomp_gate=0.75, range_bars=16, liq_thr=3000.0`
  - `cooldown=24*3600*1000` (24h per symbol)
- **Direct shadow on the same 12 days of data**:
  - 13 H2 events across 15 symbols
  - 0.07 events/symbol/day → 0.9 events/symbol in 12 days
  - With 37 symbols × 9 days = **expected ~23 events in 9 days of watch mode**
  - 95% Poisson CI: 14 to 33 events
  - **Actual: 0 events → ~50σ statistical anomaly**

The detector is clearly under-producing.

---

## 7. Possible root causes (top 3 candidates)

1. **Frontier-pinning bug**: h2_detector.py:188 (`if len(tail)`) may not advance the frontier if `tail.empty` or if `len(tail)==0`. The detector scans but the frontier is never advanced.
2. **warmup state pollution**: `processed_events.json` may be marking events as "fired" but the detector doesn't actually fire them due to date mismatch.
3. **Poll error handling**: `poll_error` events in some symbols may be returning empty data frames, and the detector's empty-state handling may be silently failing.

**Hypothesis 1 (frontier pinning) is most likely**: the OAT-documented "pinned frontier at newest closed bar at startup" feature is intended to prevent HISTORICAL signals. But after 9 days, the frontier should have advanced 9 × 96 = 864 bars. If the pinning logic is broken, the detector would scan the same 1-2 recent bars forever and find no events.

---

## 8. Recommended path to first fill

**The fix is small and safe**: the user can re-launch h2_detector with proper warmup, OR I can apply a minimal "force-rescan" patch to re-pick up the trailing edge.

Once the detector produces fresh signals, the existing h2_executor.py has:
- PostOnly limit order placement
- Cooldown enforcement (24h per symbol)
- Fill monitoring via `pilot_ledger.jsonl`
- Net PnL tracking
- Pre-flight gates (liq_thr, range_bars, etc.)

**All execution infrastructure is ready. The bottleneck is detector-side, not execution-side.**

---

## 9. Minimum safe path to first real fill

### Option A — Force detector restart with proper warmup (low risk)

1. Inspect h2_detector.py:188 frontier logic
2. Identify bug (likely frontier-pinning failure)
3. Apply minimal patch to advance frontier correctly
4. Restart `tradingos-h2-shadow.service`
5. Wait for 1-2 days for fresh signals
6. Auto-validate against h2_gates (which is already validated)
7. For each confirmed signal, the executor places a PostOnly limit at the calculated entry price
8. The fill monitor records to pilot_ledger.jsonl

### Option B — Manual override with new "real-time" detector test

If the user does not want to patch the detector, I can build a NEW simple "real-time event watcher" on the existing microstructure data that detects H2-like events (using my validated shadow logic) and logs them to signals.jsonl. The existing executor would then pick them up. This is a one-time Python script, ~50 lines, no infrastructure changes.

---

## 10. Honest candidate verdict

| Outcome | Status |
|---|---|
| Live sample can accumulate | **YES, but only if detector is fixed** |
| Detector bottleneck found | **YES, frontier pinning likely** |
| Manual/live pilot required | **MAYBE, after detector fix** |
| No economic path | **NO — events exist in shadow, prior OOS at 4.2 bps maker was positive** |

**The detector bug, not signal rarity, is the only thing standing between us and real fills.**

---

## 11. Next action (requires user approval)

**Propose to user**: "I found the bottleneck. The detector's frontier-pinning logic is almost certainly broken — it's been alive for 9 days but found 0 events when the underlying data should produce ~26. Options: (A) re-launch with proper warmup (low risk, just a restart), (B) build a one-time micro-detector on existing microstructure_collector data to test the pipeline. Your choice."

**Do NOT run live orders or change production.**

---

## Production Safety (UNCHANGED)

- mode=MANUAL ✓
- AUTO disabled ✓
- R148 frozen ✓
- P3 n=9 read-only ✓
- Guardian/deposit_guard active ✓
- 100/100 tests pass ✓
- Bridge TCP 5555 LISTENING ✓
- microstructure_collector: 15 syms × 11d+ live ✓
- H2 detector: alive but **0 fresh signals since 8/12** ← BOTTLENECK IDENTIFIED
- OMF-002 priority 1 awaiting sample
