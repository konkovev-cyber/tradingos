# MT5 Capability Extraction Audit v1

**Date:** 2026-07-21
**Source:** `/root/mt5_trading_bot/`
**Purpose:** Identify all transferable capabilities for TradingOS Exit Intelligence

---

## Audit Results

| # | Capability | MT5 Module | Lines | Transferable? | Priority |
|---|-----------|-----------|------:|:-------------:|----------|
| 1 | Position Sizing | risk_manager.py | ~80 | ✅ YES | ⭐⭐⭐⭐⭐ |
| 2 | Kelly Criterion | risk_manager.py | ~15 | ✅ YES | ⭐⭐⭐ |
| 3 | VaR Calculation | risk_manager.py | ~20 | ✅ YES | ⭐⭐⭐ |
| 4 | Kill Switch | live_guard.py | ~50 | ✅ YES | ⭐⭐⭐⭐⭐ |
| 5 | Degraded States | live_guard.py | ~30 | ✅ YES | ⭐⭐⭐⭐ |
| 6 | RR Decomposition | rr_decomposition.py | ~30 | ✅ YES | ⭐⭐⭐⭐ |
| 7 | Trade Lifecycle | lifecycle_models.py | ~50 | ✅ YES | ⭐⭐⭐⭐⭐ |
| 8 | Position as Object | portfolio_state.py | ~40 | ✅ YES | ⭐⭐⭐⭐⭐ |
| 9 | Risk Verdict | risk_decision.py | ~20 | ✅ YES | ⭐⭐⭐⭐ |
| 10 | Decision Causal Trace | decision_causality_models.py | ~30 | ✅ YES | ⭐⭐⭐⭐ |
| 11 | Event Bus | event_bus.py | ~60 | ✅ YES | ⭐⭐⭐⭐ |
| 12 | Signal Observer | signal_observer.py | ~50 | ✅ YES | ⭐⭐⭐ |
| 13 | Order Manager | order_manager.py | ~80 | ⚠️ PARTIAL | ⭐⭐⭐ |
| 14 | Grid/Martingale | — | — | ❌ NO | ❌ |
| 15 | Averaging Down | — | — | ❌ NO | ❌ |

---

## Key Findings

### What MT5 has that TradingOS doesn't:

1. **TradeLifecycle** — full position lifecycle tracking (OPEN → PARTIAL_CLOSED → CLOSED). TradingOS has no equivalent.

2. **Portfolio as Object** — MT5 treats positions as unified objects, not individual trades. The `PortfolioState` tracks `AssetClass`, position correlation, and portfolio-level risk.

3. **RR Decomposition** — separates RR degradation into execution/exit/market components. TradingOS's Reality Engine only does basic cost subtraction.

4. **Decision Causal Trace** — records exactly WHY a decision was made (causal factors, weights, contributions). TradingOS Decision Engine has no equivalent.

5. **Event Bus** — 15+ typed events (CANDLE, FEATURE, SNAPSHOT, SETUP, ORDER, EXECUTION, DESYNC, etc.). TradingOS has no event system.

6. **Signal Observer** — full signal lifecycle logging (SETUP_DETECTED → FILTERED → SCORED → RISK_CHECKED → PORTFOLIO_CHECKED → EXECUTED). TradingOS has no equivalent.

### What TradingOS already has that MT5 doesn't:

1. **State Reconciliation** — identified as critical, not in MT5
2. **KPI Framework** — TE/ESR/ERG with canonical ownership
3. **Capital Governor** — daily loss limits, position count limits
4. **Profit Watch** — real-time position monitoring
5. **Decision Review** — KPI consumer, not producer
6. **Human Approval** — formal approval workflow
7. **Execution State Machine** — PENDING → BUILDING → SENDING → SENT → VERIFIED

### What needs to be built (neither has):

1. **Exit Intelligence Engine** — user's detailed spec
2. **Profit Memory** — position history tracking
3. **False Break Detector** — spike detection
4. **Adaptive Profit Lock** — ATR-based profit protection
5. **Reversal Score** — multi-factor reversal detection
6. **Scale Out Intelligence** — partial exit logic
7. **Market Shock Handler** — volatility spike protection
8. **State Reconciliation** — ground truth verification
9. **Decision Lifecycle** — CREATED → APPROVED → RECONCILED → CLOSED

---

## Recommended Integration Order

| Phase | What | From | To |
|-------|------|------|----|
| 1 | Trade Lifecycle | MT5 lifecycle_models.py | TradingOS Position Engine |
| 2 | Decision Causal Trace | MT5 decision_causality_models.py | TradingOS Decision Engine |
| 3 | Event Bus | MT5 event_bus.py | TradingOS Event System |
| 4 | Signal Observer | MT5 signal_observer.py | TradingOS Signal Log |
| 5 | State Reconciliation | NEW | TradingOS Control Plane |
| 6 | Exit Intelligence | NEW (user spec) | TradingOS Position Engine |
| 7 | Profit Memory | NEW | TradingOS Position Engine |
