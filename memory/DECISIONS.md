# TradingOS Decisions Log

> All important decisions that affect system behavior.
> Format: DATE | Decision | Reason | Status

---

## 2026-07-25

**Decision:** Only Executor v0 can trade on Bybit.
**Reason:** Multiple executors caused confusion. Legacy ubot is DOWN and must not be restarted.
**Status:** ACTIVE

**Decision:** Create memory system for AI session continuity.
**Reason:** Each session started analysis from scratch. Wasted time, introduced errors.
**Status:** ACTIVE

---

## 2026-07-24

**Decision:** Executor v0 is single-shot, not daemon.
**Reason:** Prevents recreating ubot architecture. One trade per run.
**Status:** ACTIVE

**Decision:** Bybit chosen over BingX for Executor v0.
**Reason:** Better API docs, proper testnet, cleaner V5 API.
**Status:** ACTIVE

**Decision:** Guardian Hard Safety blocks trades, Trading Logic is DRY_RUN.
**Reason:** Safe testing of Executor v0 without experimental features.
**Status:** ACTIVE

---

## 2026-07-22

**Decision:** No new strategies during acceptance test.
**Reason:** Frozen: no strategy changes, no indicators, no MOVE_SL real execution.
**Status:** ACTIVE

**Decision:** 30-trade acceptance series required before any architecture changes.
**Reason:** Prove execution loop works before adding complexity.
**Status:** ACTIVE (0/30 completed)

---

## 2026-07-21

**Decision:** Position Management Loop extracted as independent asyncio task.
**Reason:** Main trading loop was blocking guardian. Now runs every 10s regardless.
**Status:** ACTIVE

---

## 2026-07-20

**Decision:** Money Engine > Strategy Engine.
**Reason:** User confirmed: "TradingOS перешел из режима 'найти сигнал' в режим 'управлять капиталом после входа'".
**Status:** ACTIVE

---

## Architecture Decisions (Permanent)

1. **No new layers.** Architecture freeze declared.
2. **One executor per exchange.** Bybit = Executor v0. BingX = legacy (DOWN).
3. **Guardian = brain, Executor = hands.** Guardian decides, Executor executes.
4. **Shadow-first methodology.** Run virtual protection on real data before enabling real execution.
5. **30-trade validation required** before any Guardian protection goes live.

## 2026-07-27

**Decision:** Position Monitor auto-executed Breakeven Lock on DOGEUSDT
**Reason:** MFE reached 4.45% (above 0.3% threshold), policy moved SL from 0.065 to 0.06973 (entry + 0.0001 buffer)
**Status:** ACTIVE — TP preserved at 0.075
**Note:** This was triggered automatically by the existing position_monitor.py Guardian logic. The protection system is working live without intervention.

