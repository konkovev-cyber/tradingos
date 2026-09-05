# MT5 RTS Guardian v0.1 — Design

**Date:** 2026-07-21
**Status:** DESIGN (no code changes yet)
**Mode:** DEMO ONLY
**Goal:** Turn MT5 demo into RTS-style position management layer

---

## Architecture

```
MT5 Terminal (Market Data)
         │
         ▼
    Signal Layer (existing strategy)
         │
         ▼
    ╔═══════════════════════╗
    ║  RTS GUARDIAN v0.1   ║
    ╠═══════════════════════╣
    ║  Position Guardian    ║
    ║  Risk Manager         ║
    ║  Profit Protection    ║
    ║  Market Filter        ║
    ║  Decision Log         ║
    ╚═══════════════════════╝
         │
         ▼
    MT5 Execution (OrderManager)
         │
         ▼
    Broker (Forex DEMO)
```

---

## 1. Position Guardian

### Controls per position
| Check | Implementation |
|-------|---------------|
| SL exists | Verify before open, monitor every cycle |
| TP exists | Verify before open, monitor every cycle |
| Position size | Compare with risk budget |
| Risk % | Calculate as % of account |
| Trade age | Time since open — auto-close if stale |

### Rules
```
SL must be set BEFORE entry
TP must be set BEFORE entry
No position without protection
If protection lost → alert + restore
```

---

## 2. Risk Manager (from existing)

### Position Sizing
```
risk_per_trade = 0.25% of account
max_daily_loss = 2%
max_positions = 1 (demo), 3 (max)
```

### Existing code to use
| File | Function | Reuse |
|------|----------|-------|
| `risk/risk_manager.py` | `calculate_position_size()` | ✅ As-is |
| `risk/risk_manager.py` | `kelly_criterion()` | ✅ As-is |
| `risk/risk_manager.py` | `check_daily_limits()` | ✅ As-is |
| `execution/live_guard.py` | `LiveGuard.update()` | ✅ As-is |

---

## 3. Profit Protection

### Logic (from TradingOS RTS Governor)

```
Level 1 (+0.5R):
  SL → break even
  No TP change

Level 2 (+1R):
  SL → entry + 0.5R
  Lock 30% profit

Level 3 (+2R):
  SL → entry + 1R
  Partial close 25%
  Trail remaining

Level 4 (+3R+):
  SL → entry + 2R
  Lock 70% profit
  Trail by ATR
```

### Key Rule
```
MODIFY_SL: 
  ✅ Can change SL
  ❌ Cannot delete or change TP
  TP must be preserved at all times
```

---

## 4. Market Filter

### Blocks
| Condition | Action |
|-----------|--------|
| Spread > 3x normal | NO ENTRY |
| ATR > 2x average | NO ENTRY |
| High-impact news | FREEZE 5 min before, 15 min after |
| Low liquidity | NO ENTRY |

### Check before each signal
```
if spread > threshold: block
if volatility > threshold: block  
if news_window: block
else: allow
```

---

## 5. Decision Log

### Format (same as TradingOS)
```
timestamp: 2026-07-21T12:00:00Z
symbol: XAUUSD
action: OPEN | MODIFY_SL | CLOSE | BLOCK
reason: "SL moved to BE after +0.5R"
before: SL=2300, TP=2350
after: SL=2325, TP=2350
```

### Storage
```
control/rts_guardian/decision_log.jsonl
```
Same format as BingX RTS Governor — unified reporting later.

---

## 6. Integration Plan

### Phase 1: Read-Only (now)
- Connect to MT5 terminal
- Read positions, orders, balance
- Run Position Guardian checks
- **NO modifications**

### Phase 2: Protection Only (next)
- Enable SL/TP modification
- Enable BE and trailing
- **NO new entries**

### Phase 3: Controlled Entry (after demo proof)
- Enable entry via RTS Guardian
- Risk Manager sizing
- Market Filter before entry

---

## 7. What We Reuse from Existing Code

| Component | File | Use |
|-----------|------|-----|
| LiveGuard | `execution/live_guard.py` | Kill switch, loss trajectory |
| RiskManager | `risk/risk_manager.py` | Position sizing, Kelly, daily limits |
| OrderManager | `execution/order_manager.py` | Modify SL/TP |
| Observer | `observation/observer.py` | Market data |
| TradingOS RTS | `core/rts_governor/` | Decision logic (copy pattern) |

---

## 8. What We DO NOT Use

| Component | Reason |
|-----------|--------|
| grid_gold_v3.py | Martingale, dangerous |
| ATOS/ | Legacy autonomous EA |
| RecoveryEngine.mqh | Not needed yet |

---

## 9. DEMO ONLY Mode

```
MT5 RTS Guardian operates in DEMO ONLY mode until:
- 10+ cycles completed
- No protection losses
- Decision Log verified
- Human approval for LIVE
```

---

## 10. Success Criteria

```
- Position opens with SL + TP
- SL moves preserve TP (verified)
- Daily loss limit enforced
- Block on high volatility
- Decision Log complete
- 10 demo cycles without protection failure
```
