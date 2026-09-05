# Execution Ownership Map v1

**Date:** 2026-07-21
**Purpose:** Identify ALL active executors and their capabilities
**Risk:** Multiple autonomous agents controlling the same account

---

## 1. Active Executors

### AUTO SAFE (tradingos/control/auto_safe.py)
| Aspect | Details |
|--------|---------|
| **Lives in** | `tradingos/adapters/bingx_client.py` |
| **Interface** | `cancel_existing_stops()`, `place_stop_loss()` |
| **Can open orders** | ❌ (not designed for entry) |
| **Can modify SL** | ✅ via `place_stop_loss()` |
| **Can modify TP** | ❌ (was cancelling, NOW FIXED) |
| **Can cancel orders** | ✅ `cancel_existing_stops()` |
| **Can close positions** | ❌ |
| **Target** | BingX API |
| **Status** | Protection-only by design |

### MT5 OrderManager (mt5_trading_bot/execution/order_manager.py)
| Aspect | Details |
|--------|---------|
| **Lives in** | `mt5_trading_bot/execution/order_manager.py` |
| **Interface** | `close_position()`, `modify_position()` |
| **Can open orders** | ✅ (via MT5 terminal) |
| **Can modify SL** | ✅ |
| **Can modify TP** | ✅ |
| **Can cancel orders** | ✅ |
| **Can close positions** | ✅ |
| **Target** | MT5 terminal (was accidentally on BingX) |
| **Status** | ✅ BingX keys removed, now Forex demo only |

### ubot_bingx (multiple files)
| Aspect | Details |
|--------|---------|
| **Lives in** | `/opt/ubot_bingx/execution/order_manager.py` |
| **Interface** | `place_order()`, `close_position()`, `cancel_order()` |
| **Can open orders** | ✅ |
| **Can modify SL** | ✅ (via risk_manager) |
| **Can modify TP** | ✅ |
| **Can cancel orders** | ✅ |
| **Can close positions** | ✅ |
| **Target** | BingX API |
| **Status** | RUNNING (scanner mode, not live trading) |

### scalper-v6 (Bybit)
| Aspect | Details |
|--------|---------|
| **Lives in** | (separate service) |
| **Target** | Bybit |
| **Status** | RUNNING (independent, no overlap) |

---

## 2. Conflict Matrix

| Symbol | Who Can Manage | Potential Conflict |
|--------|---------------|-------------------|
| **BingX positions** | AUTO SAFE (tradingos), ubot_bingx (order_manager), MT5 (WAS but ✅ FIXED) | ✅ RESOLVED — MT5 keys removed |
| **Bybit positions** | scalper-v6 | Isolated — no overlap |
| **MT5 positions** | OrderManager, TradingOS Guardian (planned) | 🟡 Need clear ownership |

---

## 3. Critical Risks

### Risk 1: Two agents on same account
- **PAST INCIDENT:** MT5 OrderManager was trading BingX via wrong API keys
- **FIX:** BingX keys removed from MT5, commented out in `.env.bak`
- **VERIFIED:** No MT5 process has BingX API access

### Risk 2: ubot_bingx AUTOMATICALLY opening positions
- ubot_bingx runs in scanner mode (Signals=0/5, no auto-trade)
- But `order_manager.py` has live placement capability
- **CONCERN:** If mode switches to LIVE, TradingOS won't control entries

### Risk 3: AUTO SAFE modifies SL independently
- AUTO SAFE moved SL on ARB, SOL, BCH positions
- Was losing TP in the process (NOW FIXED)
- **RESOLVED:** TP loss bug fixed, now preserves TP

### Risk 4: position-guard.service (TradingOS Shadow)
- Current mode: OBSERVE_ONLY
- Cannot open/close/modify
- ✅ SAFE by design

---

## 4. Execution Ownership Matrix

| Operation | Owner | Backup | Status |
|-----------|-------|--------|--------|
| **Open BingX position** | TradingOS → Human Approval | ubot_bingx (read-only) | 🟡 BLOCKED (manual only) |
| **Close BingX position** | TradingOS → Human Approval | ubot_bingx (read-only) | 🟡 BLOCKED |
| **Modify SL on BingX** | AUTO SAFE (tradingos) | Manual | ✅ FIXED (preserves TP) |
| **Modify TP on BingX** | Manual only | N/A | 🔴 NO auto-TP (safe) |
| **Open MT5 position** | MT5 OrderManager | TradingOS Guardian (planned) | 🟡 Demo only |
| **Close MT5 position** | MT5 OrderManager | TradingOS Guardian (planned) | 🟡 Demo only |
| **Modify MT5 position** | MT5 OrderManager | AUTO SAFE (planned) | 🟡 Demo only |

---

## 5. Recommendations

### Before Micro Live #1:
1. ✅ BingX API keys removed from MT5 — VERIFIED
2. ⚠️ ubot_bingx: verify it CANNOT auto-trade in LIVE mode
3. ⚠️ Verify no other service has access to BingX API

### For Micro Live #1:
- Only ONE position at a time
- AUTO SAFE can modify SL (TP preserved)
- ubot_bingx: OBSERVE only
- Manual close only (no auto-close)

### After Micro Live:
- Move ubot_bingx to read-only adapter
- TradingOS becomes sole execution authority
- MT5: separate Forex demo — no crypto access
