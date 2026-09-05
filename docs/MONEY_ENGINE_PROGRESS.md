# TradingOS Money Engine — Progress Tracker

## DONE

- [x] Price feed fix (get_ticker returns correct per-symbol prices)
- [x] Duplicate process elimination (systemd services disabled)
- [x] SL/TP idempotency (no more loops)
- [x] MFE/MAE tracking (per-tick in position_timeline.jsonl)
- [x] Trade Journal v2 (entry, exit, mfe, mae, giveback, exit_reason)
- [x] Position Identity Guard (blocks same-side duplicates)
- [x] Guardian Journal (guardian_journal.jsonl with giveback_live_pct)
- [x] Position Management Loop (independent asyncio task, 10s interval)
- [x] Shadow Protection Simulator v1.3 (LOCK_05, TRAIL_03, GIVEBACK_50)
- [x] Profit Protection v1 code in engine.py (DRY RUN, log only)
- [x] Kill Switch ON → OFF controlled observation

## IN PROGRESS

- [ ] Collecting 30-50 closed trades for Guardian Report
- [ ] Shadow validation data collection (LOCK_05 vs TRAIL_03 vs NO_GUARDIAN)

## BLOCKED

- [ ] Real MOVE_SL_BE — waiting for Phase 1 shadow proof
- [ ] MT5 Guardian — waiting for bridge Session 1 validation

## NEXT

- [ ] Guardian Validation Report v1 (after 30-50 closed trades)
- [ ] PIE Action Queue logging (MOVE_SL_BE candidates)
- [ ] Guardian Score (quality metric per position)
- [ ] Guardian failure test (kill observer, check reconciliation)
