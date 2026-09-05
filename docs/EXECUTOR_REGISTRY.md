# Executor Registry v1

**Date:** 2026-07-21
**Purpose:** Single source of truth for who can execute what.
**Rule:** No executor may act without explicit permission.

---

## Current Executors

| Executor | Exchange | OPEN | CLOSE | MODIFY_SL | MODIFY_TP | Status |
|----------|----------|------|-------|-----------|-----------|--------|
| **TradingOS Guard** | BingX | DENIED | DENIED | ALLOWED | DENIED | ✅ Active |
| **ubot_bingx** | BingX | DENIED | DENIED | DENIED | DENIED | ⚠️ Scanner mode |
| **scalper-v6** | Bybit | LIVE | LIVE | LIVE | LIVE | ✅ Independent |
| **MT5 OrderManager** | MT5 (Forex) | DEMO | DEMO | DEMO | DEMO | ✅ Demo only |
| **position-guard** | BingX | DENIED | DENIED | DENIED | DENIED | ✅ Observe only |

## Permission Matrix

### OPEN
| Executor | Permission | Notes |
|----------|-----------|-------|
| Human Operator | ✅ APPROVAL_REQUIRED | Manual entry only |
| ubot_bingx | ❌ BLOCKED | Scanner mode — no auto-open |
| scalper-v6 | ✅ LIVE | Independent Bybit bot |
| MT5 OrderManager | ✅ DEMO | Forex demo only |
| TradingOS | 🟡 DESIGN | Future auto-approval |

### CLOSE
| Executor | Permission | Notes |
|----------|-----------|-------|
| Human Operator | ✅ APPROVAL_REQUIRED | Manual close |
| ubot_bingx | ❌ BLOCKED | No auto-close |
| scalper-v6 | ✅ LIVE | Independent |
| MT5 OrderManager | ✅ DEMO | Demo only |
| AUTO SAFE | ❌ BLOCKED | Protection-only |

### MODIFY_SL
| Executor | Permission | Notes |
|----------|-----------|-------|
| AUTO SAFE | ✅ ALLOWED | Protection only — TP loss bug FIXED |
| ubot_bingx | ❌ BLOCKED | No SL modifications |
| Human | ✅ MANUAL | Via exchange UI |

### MODIFY_TP
| Executor | Permission | Notes |
|----------|-----------|-------|
| **NONE** | ❌ BLOCKED | No auto-TP modification |
| Human | ✅ MANUAL | Via exchange UI |

---

## Execution Modes

### LOCKED (current for BingX)
```
- No new positions
- No auto-close
- Protection SL moves ALLOWED
- Observation only
```

### APPROVAL_REQUIRED (next for Micro Live)
```
- New positions require Human Approval
- Protection SL moves ALLOWED
- No auto-close
- ubot_bingx BLOCKED
```

### LIVE_ENABLED (future)
```
- TradingOS Decision Engine may execute
- RTS Guard must PASS
- Risk Governor must ALLOW
- Reconciliation must be SYNCED
```

---

## Rules

1. **No executor may open a position without TradingOS approval**
2. **ubot_bingx: BLOCKED for all actions** while in scanner mode
3. **AUTO SAFE: SL moves only** — verified TP preserved
4. **Any executor change must be logged**
5. **Violation = EMERGENCY mode**
