# MT5 Capability Extraction Audit

**Date:** 2026-07-21
**Purpose:** Extract useful MT5 capabilities for TradingOS integration
**Source:** `/root/mt5_trading_bot/`

---

## MT5 Bot Code Structure

```
mt5_trading_bot/
├── agents/
│   ├── execution_agent.py        — execution decisions
│   ├── mt5_execution_agent.py    — MT5-specific execution
│   ├── shadow_execution_agent.py — shadow execution
│   ├── risk_agent.py             — risk decisions
│   ├── scoring_agent.py          — signal scoring
│   ├── planner_agent.py          — trade planning
│   └── portfolio_agent.py        — portfolio management
├── core/
│   ├── event_bus.py              — async pub/sub event system
│   ├── risk_decision.py          — APPROVE/REJECT/REDUCE verdicts
│   ├── rr_decomposition.py       — RR degradation analysis
│   ├── lifecycle_models.py       — position lifecycle
│   ├── portfolio_state.py        — portfolio state
│   ├── state_manager.py          — state persistence
│   └── signal_quality.py         — signal quality metrics
├── risk/
│   └── risk_manager.py           — position sizing, Kelly, VaR, daily limits
├── execution/
│   ├── order_manager.py          — order lifecycle
│   ├── live_guard.py             — kill switch, loss thresholds
│   ├── execution_simulator.py    — paper execution
│   └── trade_plan_builder.py     — trade planning
└── atos/
    ├── engine.py                 — core ATOS engine
    └── fsm.py                    — finite state machine
```

---

## Classification

### ✅ TRANSFER TO TradingOS (high value)

| MT5 Module | TradingOS Target | Why |
|------------|-----------------|-----|
| `risk/risk_manager.py` | `Capital Engine` | Position sizing, Kelly criterion, VaR, daily loss limits |
| `execution/live_guard.py` | `Risk Engine` | Kill switch, loss thresholds, degraded/critical states |
| `core/risk_decision.py` | `Decision Engine` | APPROVE/REJECT/REDUCE verdict system |
| `core/rr_decomposition.py` | `Reality Engine` | RR degradation into execution/exit/market components |
| `execution/order_manager.py` | `Execution Engine` | Order lifecycle management |
| `core/event_bus.py` | `Event Bus` | Async pub/sub event system |
| `core/lifecycle_models.py` | `Position Engine` | Position lifecycle states |
| `core/portfolio_state.py` | `Portfolio Engine` | Portfolio state tracking |

### ⚠️ RESEARCH ONLY (need validation)

| MT5 Module | TradingOS Target | Why |
|------------|-----------------|-----|
| `core/signal_quality.py` | `Signal Engine` | Signal quality metrics — needs validation |
| `agents/scoring_agent.py` | `Strategy Engine` | Scoring logic — needs validation |
| `execution/trade_plan_builder.py` | `Decision Engine` | Trade planning — needs validation |

### ❌ EXCLUDE (dangerous)

| MT5 Module | Reason |
|------------|--------|
| Grid/Martingale logic | Hidden risk, destroys TE |
| Averaging down | Creates false profit, real risk |
| Uncontrolled position expansion | Breaks risk management |
| Hardcoded SL/TP for all positions | Not adaptive |

---

## Key Capabilities to Extract

### 1. RiskManager — Position Sizing
```python
def calculate_position_size(balance, risk_percent, sl_pips, pip_value):
    risk_amount = balance * risk_percent
    return risk_amount / (sl_pips * pip_value)

def kelly_criterion(win_rate, avg_win, avg_loss):
    # Kelly formula for optimal position sizing
```

### 2. LiveGuard — Safety System
```python
class GuardStatus: SAFE | DEGRADED | CRITICAL | KILL_SWITCH

# Thresholds:
# -5% → KILL_SWITCH
# -3% → CRITICAL
# -1.5% → DEGRADED
```

### 3. RRDecomposer — Performance Analysis
```python
# Decomposes RR degradation into:
# - execution (slippage)
# - exit (MAE/MFE quality)
# - market (volatility/spread change)
```

### 4. RiskDecision — Verdict System
```python
class RiskVerdict: APPROVE | REJECT | REDUCE
# Clean verdict system for risk gating
```

---

## Integration Plan

| Phase | Source | Target | Action |
|-------|--------|--------|--------|
| 1 | RiskManager | TradingOS Capital Engine | Adapt position sizing logic |
| 2 | LiveGuard | TradingOS Risk Engine | Adapt safety thresholds |
| 3 | RRDecomposer | TradingOS Reality Engine | Adapt RR analysis |
| 4 | RiskDecision | TradingOS Decision Engine | Adapt verdict system |
| 5 | EventBus | TradingOS Event Bus | Consider async events |

---

## Key Insight

MT5 bot had **risk management capabilities** that TradingOS currently lacks:

- Position sizing with Kelly criterion
- Kill switch with loss thresholds
- RR degradation decomposition
- Risk verdict system (APPROVE/REJECT/REDUCE)

These are exactly what TradingOS needs for Controlled Trading Experiment v1.

The right approach: **extract capabilities, not port code**.
