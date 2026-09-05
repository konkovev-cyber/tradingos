# Position Guardian — Capability Catalog v1

**Date:** 2026-07-21
**Purpose:** Universal trading capabilities for position management
**Principle:** Each capability works for ANY exchange (Forex, Crypto, Stocks)
**NOT a code transfer from MT5 — native TradingOS implementations**

---

## Protection Capabilities

### 1. Breakeven Protection
- **Problem:** Position in profit, but SL still at entry or below
- **Solution:** Move SL to entry + small offset when profit threshold reached
- **TradingOS:** Position Guardian → Protection Layer
- **KPI:** Profit Capture Rate, Stop Efficiency
- **Adaptive params:** offset size (ATR-based, not fixed %)
- **Risk:** premature exit on noise
- **Dependencies:** Current price, entry price, ATR

### 2. Adaptive Profit Lock
- **Problem:** Fixed trailing stops get hit by noise
- **Solution:** Tiered lock based on profit level and ATR
- **Tiers:**
  - 0-2% profit: no lock
  - 2-4%: lock 20%
  - 5-8%: lock 40%
  - 10%+: lock 60%
  - 20%+: lock 80%
- **Adaptive:** lock levels scale with ATR (wider ATR = wider tiers)
- **KPI:** Profit Capture, False Exit Rate
- **Risk:** locking too tight on volatile assets

### 3. ATR Stop
- **Problem:** Fixed stops don't account for volatility
- **Solution:** SL = entry ± N × ATR
- **Adaptive:** N varies by regime (trend=1.5, range=1.0, breakout=2.0)
- **KPI:** Stop Efficiency, False Break Rate
- **Risk:** too wide in low-vol, too tight in high-vol

---

## Trailing Capabilities

### 4. Structure Trail
- **Problem:** MA-based trails get hit on pullbacks
- **Solution:** Trail based on market structure (HH/HL for LONG, LH/LL for SHORT)
- **TradingOS:** Exit Intelligence → Structure Analysis
- **KPI:** Trend Capture Rate, Premature Exit Rate
- **Adaptive:** lookback period (5-20 bars based on ATR)
- **Risk:** delayed exit on sharp reversals

### 5. MA Trail
- **Problem:** Simple MA trail is noisy
- **Solution:** Multi-MA confirmation (fast MA crosses slow MA)
- **KPI:** Trend Capture, Noise Filter Rate
- **Adaptive:** MA periods scale with timeframe

---

## Exit Capabilities

### 6. Time Exit
- **Problem:** Position held too long without progress
- **Solution:** Close after N bars if profit < threshold
- **Parameters:** max_bars, min_profit_at_exit
- **KPI:** Opportunity Cost, Capital Turnover

### 7. Drawdown Exit
- **Problem:** Large unrealized loss developing
- **Solution:** Exit if drawdown from peak exceeds threshold
- **Parameters:** max_drawdown_pct, recovery_time
- **KPI:** Drawdown Saved, Recovery Rate

### 8. Reversal Exit
- **Problem:** Market reversing, position going against trend
- **Solution:** Exit when reversal signals exceed threshold
- **Indicators:** divergence, OI change, funding flip, volume delta
- **KPI:** Reversal Detection Accuracy, Exit Timing

### 9. Opportunity Cost Exit
- **Problem:** Capital tied up in slow position while better opportunities exist
- **Solution:** Exit if opportunity score of new setup > current position
- **KPI:** Capital Turnover, Reallocation Efficiency

---

## Risk Capabilities

### 10. Dynamic Position Sizing
- **Problem:** Fixed position size doesn't account for current volatility
- **Solution:** Size = (risk_budget × account) / (ATR × multiplier)
- **KPI:** Risk-adjusted return, Max drawdown
- **Adaptive:** multiplier varies by regime

### 11. Kelly Criterion (with bounds)
- **Problem:** Optimal sizing unknown
- **Solution:** Kelly formula with 25% max cap
- **KPI:** Capital efficiency, Overbetting prevention
- **Risk:** requires 30+ trades for stable estimate

### 12. Correlation Check
- **Problem:** Multiple positions in correlated assets
- **Solution:** Reduce size when correlation > 0.7
- **KPI:** Portfolio diversification, Concentration risk

---

## Recovery Capabilities

### 13. False Breakout Recovery
- **Problem:** Price spikes through stop, then reverses back
- **Solution:** Don't exit on single-bar spike; require confirmation
- **Params:** spike_threshold (ATR), confirmation_time (bars)
- **KPI:** False Break Rate, Recovery Rate
- **Risk:** delayed exit on real breakout

### 14. Stop Hunt Detection
- **Problem:** Market makers push price to trigger stops
- **Solution:** Detect rapid volume spike + immediate reversal
- **Indicators:** volume anomaly, wick size, recovery speed
- **KPI:** Stop Hunt Detection Accuracy

---

## Observation Capabilities

### 15. State Reconciliation
- **Problem:** TradingOS doesn't know actual portfolio state
- **Solution:** Read exchange, compare with expected, detect mismatches
- **KPI:** State Accuracy, Desync Detection Rate
- **Critical:** Must run after EVERY action (manual or automated)

### 16. Profit Memory
- **Problem:** Decisions based only on current PnL, not history
- **Solution:** Track full position lifecycle (MFE, MAE, giveback, reversal count)
- **KPI:** Decision Quality, Historical Context Value

### 17. Exit Score
- **Problem:** No unified metric for "should I exit now?"
- **Solution:** Weighted combination of all exit signals
- **KPI:** Exit Timing Accuracy, Profit Capture vs False Exit

---

## Priority Matrix

| Priority | Capability | Impact | Complexity |
|----------|-----------|--------|------------|
| ⭐⭐⭐⭐⭐ | State Reconciliation | CRITICAL | Medium |
| ⭐⭐⭐⭐⭐ | Profit Memory | HIGH | Medium |
| ⭐⭐⭐⭐⭐ | Adaptive Profit Lock | HIGH | Low |
| ⭐⭐⭐⭐ | Exit Score | HIGH | Medium |
| ⭐⭐⭐⭐ | False Breakout Recovery | MEDIUM | High |
| ⭐⭐⭐⭐ | Structure Trail | MEDIUM | Medium |
| ⭐⭐⭐ | Time Exit | MEDIUM | Low |
| ⭐⭐⭐ | Reversal Exit | MEDIUM | High |
| ⭐⭐ | Scale Out | LOW | Medium |
| ⭐⭐ | Correlation Check | LOW | Low |

---

## Architecture Integration

```
TradingOS
    │
    ├── Decision Engine (makes decisions)
    │
    ├── Risk Governor (approves/rejects)
    │
    ├── Position Guardian (manages open positions)
    │    ├── Protection Layer
    │    ├── Trailing Layer
    │    ├── Exit Layer
    │    ├── Risk Layer
    │    └── Recovery Layer
    │
    ├── Reality Engine (costs, execution quality)
    │
    ├── Reconciliation (ground truth)
    │
    └── Profit Memory (position history)
```

Each capability is:
- Exchange-agnostic (works for BingX, Bybit, MT5, any future)
- Independently testable
- Composable with other capabilities
- Parameterized (not hardcoded)
- KPI-measurable
