# TradingOS RTS Governor Specification v1

**Date:** 2026-07-21
**Status:** ARCHITECTURE DOCUMENT (not code)
**Purpose:** Universal position protection layer — same logic for BingX, MT5, Bybit, any executor.

---

## 0. Core Philosophy

> **RTS is NOT a strategy. RTS is a position survival system.**

Strategies can be different (HA EMA100, Order Flow, Funding, Trend). RTS is the same layer above all of them.

### Principles
1. Survival > profit
2. Position control > entry accuracy
3. Profit protection > profit maximization
4. One governing logic, multiple executors

---

## 1. Position Lifecycle

```
NEW
 │
 ├─> ENTRY_VALIDATED
 │      │
 │      v
 │    OPEN
 │      │
 │      ├─> PROTECTED (SL + TP confirmed)
 │      │      │
 │      │      ├─> PROFIT_ZONE (PnL > 1R)
 │      │      │      │
 │      │      │      ├─> BE activated
 │      │      │      ├─> Profit Lock
 │      │      │      └─> Trailing
 │      │      │
 │      │      ├─> WARNING (drawdown > 1%)
 │      │      │      │
 │      │      │      ├─> Tighten SL
 │      │      │      └─> Alert operator
 │      │      │
 │      │      ├─> DEFENSIVE (drawdown > 3%)
 │      │      │      │
 │      │      │      ├─> Partial close
 │      │      │      ├─> SL to breakeven
 │      │      │      └─> Block new entries
 │      │      │
 │      │      └─> EMERGENCY (drawdown > 5%)
 │      │             │
 │      │             ├─> Close position
 │      │             ├─> Stop all trading
 │      │             ├─> Notify operator
 │      │             └─> Log KILL event
 │      │
 │      └─> CLOSED (normal exit)
 │
 └─> REJECTED (risk gate blocked)
```

### States
| State | Allowed Actions | Forbidden Actions |
|-------|----------------|-------------------|
| NEW | Pre-check, validation | Entry execution |
| ENTRY_VALIDATED | Execute entry | Skip risk check |
| OPEN | Monitor, manage | Second entry |
| PROTECTED | Manage SL/TP, trail | Remove protection |
| PROFIT_ZONE | Lock profit, trail | Add risk |
| WARNING | Tighten, alert | Open new positions |
| DEFENSIVE | Reduce, protect capital | Any new risk |
| EMERGENCY | Close, stop | All trading |
| CLOSED | Log, analyze | Re-open same setup |

---

## 2. Risk States (System Level)

Not per-position — overall system health.

```
NORMAL
  │
  └── Healthy trading
       Allow: all actions
       Block: nothing
       ──────────────────
       Triggers: none
       Response: default

WARNING
  │
  └── One negative signal detected
       Allow: protect, close
       Block: new entries, add risk
       ──────────────────
       Triggers:
       - Position SL missing
       - Daily loss > 1%
       - Consecutive loss > 3
       - Exchange latency > 5s
       Response: alert, log

DEFENSIVE
  │
  └── Multiple negative signals
       Allow: close only
       Block: all entries, modifications
       ──────────────────
       Triggers:
       - Reconciliation CONFLICT
       - Daily loss > 2%
       - Exchange unreachable
       - Any position unprotected
       Response: tighten SL, reduce risk, alert

EMERGENCY
  │
  └── Critical risk
       Allow: nothing auto
       Block: ALL trading
       ──────────────────
       Triggers:
       - Daily loss > 5% (kill switch)
       - State mismatch CONFLICT
       - Complete exchange disconnect
       - Multiple positions missing protection
       Response: freeze, notify, require manual restart
```

---

## 3. Protection Layer

### 3.1 SL Monitoring
```
Every cycle:
  FOR each position:
    IF position exists AND has SL:
      SL confirmed on exchange? → OK
    ELSE:
      CRITICAL ALERT
      Block new actions
      Attempt restore SL
```

### 3.2 TP Monitoring
```
Every cycle:
  FOR each position:
    IF position exists AND has TP:
      TP confirmed on exchange? → OK
    ELSE:
      WARNING (TP may disappear after SL move)
      Verify TP loss bug not present
```

### 3.3 Break Even
```
Rule:
  IF PnL > 1R AND SL below entry:
    Move SL to entry + small buffer
    Preserve existing TP
    Log BE action
```

### 3.4 Trailing (Adaptive)
```
Rule:
  Trail by ATR-based distance, not fixed points.
  Stop: 1.5 ATR behind current price.
  Never trail into profit zone — trail from peak.
```

### 3.5 Profit Lock
```
Tiered lock (ATP-adjusted):
  0-1R: no lock
  1-2R: lock 30% of profit
  2-3R: lock 50% of profit
  3R+:  lock 70% of profit
```

---

## 4. Exchange Problem Handlers

### 4.1 Lost Connection
```
Detection:
  API call fails > 3 times in 5 minutes
  Ping timeout > 10s

Response:
  1. Log LOST_CONNECTION event
  2. Set system state = DEFENSIVE
  3. Retry every 30s (max 10 attempts)
  4. After 10 failures → EMERGENCY
  5. Existing positions continue (no auto-close on disconnect)
```

### 4.2 State Mismatch (CONFLICT)
```
Detection:
  Reconciliation finds CONFLICT
  Expected ≠ Actual on exchange

Response:
  1. Log STATE_CONFLICT event
  2. BLOCK all new actions
  3. NOTIFY operator
  4. Require manual resolve
  5. After resolve → full reconciliation cycle
```

### 4.3 Missing Orders
```
Detection:
  Position exists on exchange
  No SL/TP orders for that position

Response:
  1. ALERT operator
  2. Attempt to restore SL/TP
  3. If restore fails → BLOCK new actions
  4. Operator decides: close or manual fix
```

### 4.4 Manual Intervention Detected
```
Detection:
  Position changed by non-TradingOS action
  (Operator, other bot, MT5 AUTO SAFE)

Response:
  1. Detect via Reconciliation
  2. Log MANUAL_INTERVENTION event
  3. INVALIDATE old decisions
  4. Run full reconciliation cycle
  5. Resume with current state
```

---

## 5. Market Problem Handlers

### 5.1 Spread Spike
```
Detection:
  Current spread > 3× normal range

Response:
  1. Set state = WARNING
  2. Block new entries
  3. Block SL modifications (avoid bad fills)
  4. Duration: until spread normalizes + 5 minutes
```

### 5.2 Volatility Spike
```
Detection:
  ATR > 2× rolling average
  Candle body > 1.5× ATR

Response:
  1. Set state = DEFENSIVE
  2. Freeze all new entries
  3. Widen SL if needed (1.5× normal)
  4. Do NOT close positions (avoid whipsaw)
```

### 5.3 News Event (High Impact)
```
Detection:
  Scheduled news (NFP, FOMC, CPI)
  Unscheduled news (flash crash, black swan)

Response (scheduled):
  1. 5 min before: block new entries
  2. During: freeze all modifications
  3. 15 min after: resume normal operations
  
Response (unscheduled):
  1. Immediate freeze
  2. Assess: emergency or temporary?
  3. If black swan → EMERGENCY protocol
```

### 5.4 Liquidity Event
```
Detection:
  Order book thin
  Large spreads
  Slow fills

Response:
  Same as volatility spike + manual notification.
```

---

## 6. Controlled Scaling (No Martingale)

### Rules
```
1. MAX_LEVELS = 3
2. Each level = SAME risk (no doubling)
3. NO adding when TREND is against position
4. NO adding during WARNING/DEFENSIVE/EMERGENCY
5. Recalculate average price after each add
6. Common SL/TP for whole basket
```

### Example
```
Level 1: BUY 0.01 lot @ 2350 (risk 0.25%)
Level 2: BUY 0.01 lot @ 2347 (range mode, -0.5 ATR)
Level 3: BUY 0.01 lot @ 2344 (range mode, -1.0 ATR, MAX)

Total risk: 0.75%
Average price: (2350 + 2347 + 2344) / 3 = 2347
Basket SL: 2340 (common for all)
Basket TP: 2355 (common for all)
```

---

## 7. Decision Log

Every RTS action MUST be logged:

```
TIMESTAMP: 2026-07-21T10:15:30Z
POSITION:  BTCUSDT
ACTION:    MOVE_SL
REASON:    Profit Lock L1
BEFORE:    SL=45000
AFTER:     SL=46000
TP:        48000 (PRESERVED)
RISK:      0.3% → 0.15%
```

### Log Categories
| Category | Examples |
|----------|----------|
| ENTRY | NEW, REJECTED, ENTRY_VALIDATED |
| PROTECTION | SL_SET, TP_SET, BE_ACTIVATED |
| MODIFICATION | MOVE_SL, MOVE_TP, LOCK_PROFIT |
| EXIT | TP_HIT, SL_HIT, MANUAL_CLOSE, EMERGENCY_CLOSE |
| SYSTEM | CONFLICT, LOST_CONNECTION, RECONCILED |
| ERROR | PROTECTION_LOST, API_FAILURE |

---

## 8. Integration with Existing Components

### What Already Exists (70% complete)

| Component | Role in RTS Governor |
|-----------|---------------------|
| State Reconciliation | Detects CONFLICT, DRIFT, CLOSED → triggers state change |
| Safety Gate | BLOCK on CONFLICT → implements RTS stop |
| RTS Guard | Balance, exchange, protection checks → runtime safety |
| Protection State | SL/TP from orders → protection monitoring |
| Risk Governor | Daily loss, kill switch, position limits → risk layer |
| KPI Collector | TE/ESR/ERG → measurement layer |

### What Needs Integration

| Gap | What to Build |
|-----|--------------|
| Position Lifecycle | State machine: NEW → OPEN → PROTECTED → CLOSED |
| Risk States | System-level: NORMAL → WARNING → DEFENSIVE → EMERGENCY |
| Decision Log | Structured logging for every RTS action |
| Profit Lock | Tiered profit protection by R-multiple |
| Controlled Scaling | Max 3 levels, fixed risk, regime filter |

---

## 9. Integration Architecture

```
Existing TradingOS Components:
  Reconciliation → Safety Gate → RTS Guard → Protection State → Risk Governor → KPI
       │               │            │              │                │          │
       └───────────────┴────────────┴──────────────┴────────────────┴──────────┘
                                        │
                                   RTS Governor
                                        │
                        ┌───────────────┼───────────────┐
                        │               │               │
                   Position        Risk States      Decision Log
                   Lifecycle       (system-wide)    (every action)
                        │               │               │
                        └───────────────┴───────────────┘
                                        │
                                  Human Approval
                                        │
                                  Execution Adapter
                                        │
                                  Exchange (BingX / MT5 / Bybit)
```

---

## 10. Implementation Order

1. **Position Lifecycle state machine** — integrate existing components into one lifecycle
2. **Decision Log** — structured logging for every action
3. **System Risk States** — NORMAL → WARNING → DEFENSIVE → EMERGENCY
4. **Profit Lock** — tiered by R-multiple
5. **Controlled Scaling** — max 3 levels, fixed risk, regime filter

**Not before Micro Live #1:** Recovery Engine, Market Regime Detector, Profit Rescue (unless proven necessary).

---

## 11. Success Criteria

After integration, the system should:
1. Never have a position without SL/TP for more than 1 minute
2. Always detect CONFLICT within 1 reconciliation cycle
3. Never lose TP when moving SL
4. Automatically block new entries during WARNING/DEFENSIVE/EMERGENCY
5. Log every protection action with reason
6. Survive a black swan event (no account loss)
