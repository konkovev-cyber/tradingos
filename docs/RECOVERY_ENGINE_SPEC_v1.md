# Recovery & Protection Engine v1 — Specification

**Date:** 2026-07-21
**Status:** DESIGN ONLY (no implementation yet)
**Constraint:** DO NOT implement MT5. Design TradingOS-native module.

---

## 1. Problem Statement

TradingOS can detect opportunities and manage risk, but lacks:
- Adaptive position lifecycle management
- Market regime awareness (calm vs shock)
- Capital preservation under stress
- Intelligent recovery from drawdowns
- Anti-spike protection

Current gap: TradingOS approved "SET TP" but couldn't execute, then lost sync with exchange. This reveals missing reconciliation AND missing lifecycle management.

---

## 2. Architecture

```
TradingOS Core
    │
    ├── Market Regime Detector
    │    INPUT: price, ATR, volume, spread, candle_data
    │    OUTPUT: regime (NORMAL | FAST | SHOCK | PANIC)
    │
    ├── Recovery Engine
    │    INPUT: regime + position_state + equity_state
    │    OUTPUT: action (ALLOW_ADD | WAIT | PROTECT | REDUCE | EXIT)
    │
    ├── Anti-Spike Protection
    │    INPUT: candle_data, spread, volume
    │    OUTPUT: freeze_duration
    │
    ├── Profit Rescue Logic
    │    INPUT: max_profit, current_profit, giveback_threshold
    │    OUTPUT: EXIT signal
    │
    ├── Equity Protection
    │    INPUT: equity_drawdown
    │    OUTPUT: tier_response (NORMAL | CAUTION | RECOVERY | EMERGENCY)
    │
    └── Execution Adapter
         INPUT: action from above
         OUTPUT: exchange commands
```

**Key principle:** Recovery Engine is EXCHANGE-AGNOSTIC. It doesn't know about MT5, BingX, or Bybit. It only works with market data and position state.

---

## 3. Market Regime Detector

### Input Factors
- ATR ratio (current / rolling average)
- Candle body percentage
- Spread expansion
- Volume spike
- Price velocity
- Tick frequency

### Regimes

| Regime | Condition | Action Allowed |
|--------|-----------|---------------|
| NORMAL | ATR < 1.5x avg, stable spread | All actions |
| FAST | ATR 1.5-2.5x avg OR volume spike | Reduce risk, no new positions |
| SHOCK | ATR > 2.5x avg OR extreme candle | Only protection, no additions |
| PANIC | ATR > 4x avg OR sustained collapse | Capital preservation only |

### Implementation Notes
- Rolling ATR window: 20-50 bars (configurable)
- ATR thresholds: adaptive to asset (XAUUSD vs BTC vs ETH)
- Regime persistence: minimum N bars before switching
- No regime change during active spike

---

## 4. Recovery Engine State Machine

```
NORMAL ──────────────────────────────→ WAIT_VOLATILITY
  │                                      │
  │ drawdown detected                    │
  ▼                                      │
STRESSED ──────────────────────────→ RECOVERY
  │                                      │
  │ equity < 60%                         │
  ▼                                      │
EMERGENCY ─────────────────────────→ EXIT
  │                                      │
  └──────────────────────────────────────┘
```

### State Transitions

| From | To | Condition |
|------|----|-----------|
| NORMAL | WAIT_VOLATILITY | ATR > 1.5x avg OR volume spike |
| NORMAL | STRESSED | Drawdown > 3% |
| STRESSED | RECOVERY | Drawdown > 10% |
| RECOVERY | EMERGENCY | Drawdown > 20% |
| Any | NORMAL | ATR normalizes AND drawdown recovered |
| EMERGENCY | EXIT | Minimum recovery achieved |

### Actions per State

| State | Allowed | Forbidden |
|-------|---------|-----------|
| NORMAL | All | None |
| WAIT_VOLATILITY | Protection, Exit | New positions, Add |
| STRESSED | Reduce target, Partial exit | New positions |
| RECOVERY | Exit at breakeven, Capital recovery | Averaging, New positions |
| EMERGENCY | Exit any positive, Kill switch | All additions |

---

## 5. Anti-Spike Protection

### Detection
- Candle body > 1.5x ATR
- Price velocity > X points/minute
- Spread > 2x normal
- Volume > 3x average
- Tick frequency anomaly

### Response
```
FREEZE for N candles (configurable)
```

During freeze:
- No new positions
- No averaging
- No martingale
- Protection orders remain active
- Exit allowed only at profit

### Persistence
- Freeze lasts until market normalizes
- Minimum freeze: 3 candles
- Maximum freeze: 20 candles (configurable)

---

## 6. Profit Rescue Logic

### Peak Profit Tracking
```
max_profit = max(all historical profits)
current_profit = current PnL
giveback = (max_profit - current_profit) / max_profit
```

### Rescue Triggers
| Giveback | Action |
|----------|--------|
| < 30% | WAIT (normal trailing) |
| 30-50% | LOCK 50% of remaining profit |
| 50-70% | LOCK 80% of remaining profit |
| > 70% | EXIT at current level |
| > 90% | EMERGENCY EXIT |

### Implementation
- Track max_profit continuously
- Update on every price tick
- Compare with current profit
- Trigger rescue when threshold exceeded

---

## 7. Equity Protection

### Tiers
| Equity Level | Status | Response |
|-------------|--------|----------|
| > 80% | NORMAL | All actions allowed |
| 60-80% | CAUTION | Reduce position size, tighter stops |
| 40-60% | RECOVERY | Only recovery actions, no new positions |
| < 40% | EMERGENCY | Exit all, kill switch |

### Emergency Protocol
1. Close all positions with any positive PnL
2. Set tight stops on remaining
3. Block new positions for N hours
4. Log emergency event
5. Require human approval to resume

---

## 8. Profit Rescue Logic (RTS-inspired)

### Core Principle
> "Capital preservation before profit maximization"

### Algorithm
```
peak_profit = track_max(all_time_high)
current_profit = current PnL
giveback_pct = (peak - current) / peak * 100

if giveback_pct > 70%:
    EXIT immediately
elif giveback_pct > 50%:
    LOCK 80% of remaining
elif giveback_pct > 30%:
    LOCK 50% of remaining
else:
    NORMAL trailing
```

### Key Insight from RTS
> "Don't wait for perfect exit. Take any positive exit when capital is at risk."

This is the opposite of "let winners run" — it's "don't let winners become losers."

---

## 9. Integration Points

```
Market Regime Detector
        │
        ▼
Recovery Engine ←── Position Guardian
        │            (current state)
        ▼
Action Decision
        │
        ▼
Risk Governor (existing)
        │
        ▼
Decision Engine (existing)
        │
        ▼
Human Approval (existing)
        │
        ▼
Execution Adapter
```

**Recovery Engine does NOT:**
- Open new positions
- Execute orders
- Modify existing orders directly
- Replace Risk Governor or Decision Engine

**Recovery Engine DOES:**
- Recommend actions based on regime and position state
- Block dangerous actions during shock/panic
- Trigger profit rescue when giveback threshold exceeded
- Manage equity protection tiers

---

## 10. KPIs

| KPI | Definition | Target |
|-----|-----------|--------|
| Recovery Efficiency | % of drawdown recovered | > 50% |
| Risk Survival | % of shocks survived without loss | > 80% |
| False Recovery | % of times averaging worsened situation | < 20% |
| Spike Avoidance | % of dangerous entries prevented | > 90% |
| Profit Rescue | % of max profit preserved after reversal | > 40% |

---

## 11. Dependencies

| Component | Required? | Purpose |
|-----------|-----------|---------|
| Market data (price, ATR, volume) | YES | Regime detection |
| Position state (entry, PnL, size) | YES | Lifecycle management |
| Risk Governor | YES | Gate for actions |
| Decision Engine | YES | Final decision |
| Reality Engine | OPTIONAL | Cost modeling |
| Reconciliation | OPTIONAL | State verification |

---

## 12. Phases (Design Only)

| Phase | What | Output |
|-------|------|--------|
| 1 | State Machine | RecoveryStateMachine class |
| 2 | Market Regime Detector | RegimeDetector class |
| 3 | Recovery Engine | RecoveryEngine class |
| 4 | Anti-Spike Protection | SpikeDetector class |
| 5 | Profit Rescue | ProfitRescue class |
| 6 | Equity Protection | EquityProtection class |
| 7 | Backtest Simulator | Paper engine |
| 8 | Integration test | Prove on XAUUSD M5 history |

**NO MT5 ADAPTER until Phase 8 proves value.**
