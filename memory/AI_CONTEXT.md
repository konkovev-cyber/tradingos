# TradingOS AI Context — Single Source of Truth

> **Every AI session MUST read this file first.**
> Last updated: 2026-07-25

---

## TradingOS Identity

**What:** Professional crypto trading system. Multi-exchange (Bybit + BingX + MT5).
**Goal:** Capital protection first, profit second. Money Engine, not Strategy Engine.
**Forbidden:**
- Creating new trading strategies without explicit approval
- Changing TP/SL without checking owner
- Launching new bots/daemons without approval
- Modifying exchange API keys
- Running `rm -rf` or destructive git operations

---

## Current Architecture

### Kernel
- **Status:** RUNNING (PID 1570009)
- **Path:** `/root/tradingos_kernel/tradingos.py daemon`
- **Can modify money:** NO (monitoring only)

### Executor v0 (PRIMARY)
- **Status:** OFFLINE (API 401)
- **Path:** `/root/trading_brain_v4/research/execution/executor_v0.py`
- **Can modify money:** YES (sole authority)
- **Exchange:** Bybit
- **API Keys:** `/root/trading_brain_v4/research/execution/.env`

### Guardian (Risk)
- **Status:** DRY RUN (logs only, no auto-close)
- **Path:** `/root/trading_brain_v4/research/execution/risk_guardian.py`
- **Can modify money:** NO (blocks only)

### Position Guard Worker
- **Status:** RUNNING (PID 1998237)
- **Path:** `/root/tradingos/services/position_guard_worker.py`
- **Can modify money:** NO (BingX observation only)

### Telegram Bot
- **Status:** RUNNING (PID 1993950)
- **Path:** `/usr/bin/python3 -m telegram_control.bot`
- **Can modify money:** NO (notifications only)

### Market Dashboard
- **Status:** RUNNING (PID 1600587)
- **Path:** `/opt/market_agent/.venv/bin/python3 -m uvicorn dashboard.app:app`
- **Can modify money:** NO (read-only web UI)

### Collectors (Data Pipeline)
- **Orderbook Collector:** RUNNING (PID 1406705) — BTCUSDT
- **Trade Collector:** RUNNING (PID 2355173) — BTCUSDT
- **Liquidation Collector:** RUNNING (PID 1406753) — BTCUSDT
- **Health Monitor:** RUNNING (PID 1367692)
- **Can modify money:** NO (data collection only)

### Research Runners (Shadow)
- **MR-001:** RUNNING (PID 1068368) — 5m
- **VOL-001:** RUNNING (PID 1068369) — 15m
- **TF-001:** RUNNING (PID 1068367) — 1h
- **Crypto AB:** RUNNING (PID 1014845) — 5m
- **Can modify money:** NO (shadow/paper only)

### Legacy (DOWN)
- **ubot:** OFFLINE — `/opt/ubot/` — Legacy Bybit bot, DO NOT restart
- **ubot_bingx:** OFFLINE — `/opt/ubot_bingx/` — Legacy BingX bot, DO NOT restart

---

## Current Trading Mode

**Mode:** MICRO LIVE (Executor v0 acceptance testing)
**Status:** PAUSED — Bybit API returning 401, need key refresh
**Active Executor:** Executor v0 (v0, build 2026-07-24)
**Acceptance Series:** 0/30 trades completed

---

## Current Positions

### Bybit (Executor v0 account)
| Symbol | Side | Size | Entry | SL | TP | Created By |
|--------|------|------|-------|----|----|------------|
| DOGEUSDT | BUY | 100 DOGE | 0.06966 | 0.0650 | 0.0750→0.08 | Executor v0 |

**Status:** Open, TP possibly unreachable (14.9% distance)
**Issue:** API 401 prevents modification/check

### BingX (Legacy ubot_bingx)
| Symbol | Side | Size | Entry |
|--------|------|------|-------|
| No open positions | — | — | — |

---

## Known Problems

1. **Bybit API 401** — Both API keys return 401. Server IP: 46.29.232.156. Diagnostic tool: `research/execution/tools/bybit_auth_check.py`. User must verify in Bybit dashboard.

2. **TP unreachable** — DOGEUSDT TP at 0.08 is 14.9% from entry. For DOGE volatility, this may never hit. Needs adaptive TP based on ATR.

3. **No time-based exit** — Positions can hang indefinitely if TP not reached. Need 48-72h timeout with Guardian review.

4. **Session amnesia** — Each new AI session started analysis from scratch. This memory system fixes that.

5. **Multiple legacy processes** — ubot, shadow monitors, research runners still running but non-functional. Safe to ignore but consuming resources.

---

## Completed Fixes

- ✅ Position Management Loop extracted as independent task (2026-07-21)
- ✅ Breakeven Lock v1 implemented (MFE >= 0.3% → move SL to entry)
- ✅ Telegram Trade Cards implemented
- ✅ Bybit V5 set_trading_stop for SL/TP modify (not amend_order)
- ✅ Acceptance Tracker built
- ✅ Capability Audit v1 completed (33/56 READY)
- ✅ This memory system created (2026-07-25)
- ✅ Production Hardening v1 implemented (2026-07-25): WAL, Position State, Decision Lock, Idempotency, Approval, Reconciler, Recovery
- ✅ Executor Hardened Integration (2026-07-25): HardenedExecutor wrapper with full safety pipeline (29/29 tests passing)
- ✅ BUG-1 fix (2026-07-27): `/v5/order/list` → `/v5/order/realtime` in client.py + whitelist in request_builder.py
- ✅ Bybit API recovery (2026-07-27): new key `FyZUY69SLCbYUqw2jM` working, all 3 private endpoints return 200, DOGEUSDT position verified
