# MT5 Guardian Audit v1

**Date:** 2026-07-21
**Purpose:** Understand MT5 capabilities before integration with TradingOS
**Rule:** DO NOT write MT5 code. Audit only.

---

## 1. MT5 Components Found

### MQL5 Code (Expert Advisors)
```
TradingOS/Engine/
├── DecisionEngine.mqh
├── RiskSupervisor.mqh

TradingOS/Execution/
├── ExecutionEngine.mqh
├── BrokerSync.mqh
├── FillProbabilityEngine.mqh
├── FillTracker.mqh
├── LatencyRouter.mqh
├── LiquidityMap.mqh
├── MarketImpactModel.mqh
├── MicrostructureEngine.mqh
├── OrderBookModel.mqh
├── QueuePositionModel.mqh
├── SlippageModel.mqh
├── SpreadDynamicsModel.mqh
├── ToxicFlowDetector.mqh
├── ExecutionProfiler.mqh

TradingOS/Recovery/
├── RecoveryEngine.mqh
├── SnapshotStore.mqh
```

### Python Modules
```
risk/risk_manager.py           — position sizing, Kelly, VaR
execution/order_manager.py     — order lifecycle
execution/live_guard.py        — kill switch, loss thresholds
execution/execution_simulator.py — paper trading
execution/trade_plan_builder.py — trade planning
strategy/grid_gold_v3.py      — Grid Gold strategy
strategy/signal_generator.py  — signal generation
monitoring/                    — health, divergence, system metrics
observation/                   — observer, integrity, journal
```

### AUTO SAFE
**Location:** NOT in MT5 bot. In `tradingos/control/auto_safe.py`.
- Uses BingX API (not MT5 terminal)
- Already Protection Only (MOVE_SL_BE only)
- TP loss bug fixed in BingXClient

---

## 2. Autonomous Actions Audit

### Can MT5 EA open orders?
**YES** — OrderManager creates orders via MT5 terminal API.

### Can MT5 EA close orders?
**YES** — OrderManager has close_order method.

### Can MT5 EA change SL?
**YES** — AUTO SAFE in tradingos moves SL via BingX API.

### Can MT5 EA change TP?
**YES** — but AUTO SAFE was losing TP (now fixed).

### Hidden OnTick loops?
**YES** — grid_gold_v3.py has OnTick handlers.
**YES** — signal_generator.py has periodic scanning.

### Timers?
**YES** — MT5 has OnTimer() events.

---

## 3. What Can Be Reused for TradingOS Guardian

| Component | Reuse? | Why |
|-----------|--------|-----|
| RiskSupervisor.mqh | ⚠️ Study | Risk logic patterns |
| RecoveryEngine.mqh | ⚠️ Study | Recovery patterns |
| live_guard.py | ✅ Already in TradingOS | Kill switch, loss thresholds |
| risk_manager.py | ✅ Already in TradingOS | Position sizing, Kelly |
| execution_simulator.py | ✅ Already in TradingOS | Paper trading |
| trade_plan_builder.py | ⚠️ Study | Trade planning patterns |
| grid_gold_v3.py | ❌ Grid/Martingale | Dangerous, skip |
| signal_generator.py | ⚠️ Study | Signal patterns |

---

## 4. MT5 Guardian v1 — Proposed Architecture

```
TradingOS Guardian (brain)
    │
    ├─ State Reconciliation
    ├─ Safety Gate
    ├─ RTS Guard
    ├─ Protection State
    ├─ Risk Governor
    └─ Decision Engine
         │
         ↓
    MT5 Adapter (thin)
         │
         ↓
    MT5 Terminal
         │
         ↓
    Broker execution
```

**MT5 Guardian responsibilities:**
- READ: positions, orders, history, balance, margin
- PROTECT: SL validation, TP validation, emergency stop
- EXECUTE: only TradingOS commands

**MT5 Guardian does NOT:**
- Open new positions (TradingOS decides)
- Choose strategy (TradingOS decides)
- Manage risk (TradingOS decides)
- Run OnTick loops (TradingOS triggers)

---

## 5. API Contract Proposal

### TradingOS → MT5 (commands)
```
OPEN_POSITION: symbol, side, size, sl, tp
MODIFY_SL: symbol, new_sl
MODIFY_TP: symbol, new_tp
CLOSE_POSITION: symbol, reason
GET_STATE: positions, orders, balance
```

### MT5 → TradingOS (events)
```
POSITION_UPDATE: symbol, side, size, pnl
ORDER_UPDATE: order_id, status, fill_price
RISK_ALERT: type, severity, symbol
EXECUTION_RESULT: order_id, success, error
```

---

## 6. Key Finding

MT5 has a rich execution layer (ExecutionEngine.mqh, BrokerSync.mqh, etc.) that could be valuable for the MT5 Adapter. But it must be controlled by TradingOS, not run autonomously.

The existing `live_guard.py` + `risk_manager.py` in MT5 are already adapted into TradingOS. The remaining MT5 value is in execution quality (slippage, fill probability, latency) — not in strategy or risk.

**Recommendation:** After Micro Live #1, create MT5 Adapter that wraps MT5 terminal as a thin execution layer controlled by TradingOS Guardian.
