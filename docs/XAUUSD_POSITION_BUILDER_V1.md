# XAUUSD Adaptive Position Builder v1 — Design

**Date:** 2026-07-21
**Status:** DESIGN ONLY
**Symbol:** XAUUSD
**Principle:** NOT martingale. Controlled adaptive scaling with regime filter.

---

## 1. Problem Statement

XAUUSD doesn't work well with "one entry → SL → TP" because:
- Strong pullbacks (20-40 ATR) before trend continuation
- Sharp reversals during news
- Wide spreads during volatility
- Long drawdown periods in range

Martingale "works" until it doesn't. One strong trend against position destroys account.

**Goal:** Safe position building that survives worst-case scenarios.

---

## 2. Architecture

```
Market Regime Detector
        |
        ↓
Entry Decision
        |
        ↓
Position Manager
        |
        ├── Level 1: Initial Entry (fixed risk)
        ├── Level 2: Add if regime allows (fixed risk)
        ├── Level 3: Add if regime allows (fixed risk)
        └── Level 4: MAX (hard stop)
        |
        ↓
Basket Manager
        |
        ├── Average Price Calculator
        ├── Common TP / Common SL
        ├── Break Even Logic
        └── Trailing Logic
        |
        ↓
Emergency Protection
        |
        ├── Daily Loss Stop
        ├── Max DD Stop
        ├── Volatility Freeze
        └── Regime Change → Close
```

---

## 3. Core Rules

### Rule 1: Fixed Risk Per Level
```
Level 1: risk = 0.25% of equity
Level 2: risk = 0.25% of equity
Level 3: risk = 0.25% of equity
Total risk: 0.75% of equity (max)

NO DOUBLING
NO MULTIPLIERS
Each level = same risk
```

### Rule 2: Max Levels = 3
```
MAX_ENTRIES = 3
Not 4, not 5, not 7

After 3 entries:
- No more buying
- Manage existing basket only
- If price goes against → SL
```

### Rule 3: Regime Filter
```
RANGE MODE:
  Allow adding positions
  Price between EMAs
  ATR normal
  ADX < 20

TREND MODE (against position):
  NO adding
  Only manage existing
  ATR > 1.5x average

NEWS MODE:
  NO entries at all
  Freeze 5 minutes before/after high-impact events
```

### Rule 4: Emergency Stops
```
DD > 3%:     STOP all new entries
DD > 5%:     CLOSE half position
DD > 8%:     CLOSE all
Daily loss > 2%: STOP trading
3 consecutive losses: PAUSE 1 hour
```

---

## 4. Position Building Algorithm

### Initial Entry
```
Signal:
  Market State = RANGE or WEAK_TREND
  Score > 60
  ATR normal
  Spread normal
  Risk budget OK

Action:
  BUY 0.01 lot (or calculated size)
  SL = entry - 1.5 ATR
  TP = entry + 2.0 ATR (initial target)
```

### Level 2 Add
```
Conditions:
  Price dropped 0.5 ATR from last entry
  Market still in RANGE mode
  Total risk < 0.5% equity
  DD < 3%
  Spread normal

Action:
  BUY 0.01 lot
  Recalculate average price
  Recalculate basket SL
  Recalculate basket TP
```

### Level 3 Add (MAX)
```
Conditions:
  Price dropped 1.0 ATR from first entry
  Market still in RANGE mode
  Total risk < 0.75% equity
  DD < 3%
  Spread normal

Action:
  BUY 0.01 lot
  FINAL entry
  Recalculate basket
  NO MORE ENTRIES after this
```

### After Level 3
```
Only manage:
  - trailing SL
  - break even
  - partial close
  - exit
```

---

## 5. Basket Management

### Average Price
```
avg_price = (entry1 * lot1 + entry2 * lot2 + entry3 * lot3) / (lot1 + lot2 + lot3)
```

### Basket SL
```
basket_sl = avg_price - 2.0 * ATR (total position)
```

### Basket TP
```
basket_tp = avg_price + 2.5 * ATR (total position)
```

### Break Even
```
If basket PnL > 1.0 * ATR:
  Move SL to avg_price + small_offset
```

### Partial Close
```
If basket PnL > 1.5 * ATR:
  Close 30% of position
  Move SL to avg_price

If basket PnL > 2.5 * ATR:
  Close 50% of remaining
  Trail SL by 0.5 ATR
```

---

## 6. Regime-Specific Behavior

### RANGE Mode (default)
```
Allow:
  - Initial entry
  - Add positions (up to level 3)
  - Normal management

Behavior:
  - Grid-like (add on pullback)
  - Target: mean reversion
  - Wider SL (2.0 ATR)
```

### TREND Mode (strong direction)
```
If TREND is WITH position:
  - Allow entry (1 level only)
  - Trail aggressively (0.5 ATR)
  - Target: trend continuation

If TREND is AGAINST position:
  - NO new entries
  - Tighten SL
  - Consider partial close
  - Do NOT add to losing position
```

### COMPRESSION Mode
```
Allow:
  - Initial entry only
  - No adding
  - Wait for breakout

Behavior:
  - Smaller lot
  - Wider SL (breakout protection)
  - TP = 1.5 ATR (conservative)
```

### NEWS Mode
```
FREEZE:
  - No new entries
  - No adding
  - No modification

Duration:
  - 5 min before event
  - 15 min after event

Exception:
  - Emergency close allowed
```

---

## 7. Emergency Protocol

### Level 1: CAUTION (DD > 1.5%)
```
Action:
  - Stop new entries
  - Log WARNING
  - Continue managing existing
```

### Level 2: STRESS (DD > 3%)
```
Action:
  - Stop all entries
  - Tighten SL to 1.0 ATR from current price
  - Log ALERT
  - Notify operator
```

### Level 3: EMERGENCY (DD > 5%)
```
Action:
  - Close 50% of position
  - Move SL to breakeven on remaining
  - Log CRITICAL
  - Require manual review
```

### Level 4: KILL (DD > 8%)
```
Action:
  - Close ALL positions
  - Stop trading for 24 hours
  - Log KILL_EVENT
  - Require manual restart
```

---

## 8. Integration with TradingOS

### Position Guardian checks:
```
1. Reconciliation: SYNCED?
2. Protection: SL exists? TP exists?
3. Risk: DD within limits?
4. Regime: appropriate for action?
5. Emergency: any triggered?
```

### KPI:
```
- Basket PnL
- Average Price
- ATR at entry
- Regime at entry
- Number of adds
- Time in position
- Max favorable excursion
- Max adverse excursion
```

---

## 9. What We Take from Current Grid Gold V3 Pro

| Component | Take? | Modify? |
|-----------|-------|---------|
| Market State Engine | YES | Keep as-is |
| Scoring System | YES | Simplify |
| Correlation Filter | YES | Keep as-is |
| Partial Close | YES | Simplify levels |
| Live Guard | YES | Lower thresholds |
| Grid/Martingale | NO | REPLACE with fixed risk |
| 7 grid levels | NO | REPLACE with max 3 |
| Lot multipliers | NO | REPLACE with fixed lots |
| DD -20% limit | NO | REPLACE with -3%/-5%/-8% |

---

## 10. Comparison: Old vs New

| Aspect | Grid Gold V3 Pro | RTS v0.1 |
|--------|-----------------|----------|
| Max levels | 7 | 3 |
| Lot sizing | Multipliers (1.0-1.6x) | Fixed |
| DD limit | -20% | -3%/-5%/-8% |
| Regime filter | Yes (6 states) | Yes (simplified to 4) |
| Emergency | Kill switch only | 4-level escalation |
| Recovery | Add more (dangerous) | Close partial, trail |
| Market filter | Yes | Yes (stronger) |
| Position guardian | No | Yes (SL/TP required) |

---

## 11. Success Criteria

After backtest on XAUUSD M5 (12 months):

```
Win rate:              > 45%
Max drawdown:          < 8%
Recovery factor:       > 1.5
Avg trades per month:  8-15
Profit factor:         > 1.3
Max consecutive losses: < 5
```

If criteria NOT met → adjust parameters, NOT increase risk.
