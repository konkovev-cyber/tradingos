# TradingOS — ubot_bingx Absorption Audit (2026-07-27)

## File counts (excluding .venv, __pycache__)
- Python files: ~100+ in core/strategy/execution/features
- strategies/strategies/: 11 strategy files
- core/exchanges/: bybit, bingx adapters (will DELETE)
- Total ubot_bingx size: ~62MB

## TASK 1 — Inventory classification (verified by ls + grep)

| File | Responsibility | Classification |
|------|---------------|-----------------|
| `core/engine.py:_process_symbol` (line 1957) | Main loop, calls strategies → decision | KEEP (logic only) |
| `core/engine.py` (line 2000+) | Place order calls | DELETE |
| `strategy/signal_generator.py` | Pure logic BUY/SELL | KEEP |
| `strategy/signal_scoring.py` | Confidence scoring | KEEP |
| `strategy/signal_types.py` | Dataclasses | KEEP |
| `strategy/signal_scorer.py` | Alternative scorer | KEEP |
| `strategy/decision_pipeline.py` | Decision flow | KEEP |
| `core/decision_engine.py` | Decision rules | KEEP |
| `core/decision_core.py` | Core decision | MERGE into decision_engine |
| `core/decision_engine_v2.py` | V2 (older) | DELETE (duplicate) |
| `core/decision_engine_v3.py` | V3 (older) | DELETE (duplicate) |
| `strategy/manager.py` | Strategy aggregator | KEEP |
| `strategy/loader.py` | Strategy discovery | KEEP |
| `strategy/base.py` | BaseStrategy abstract class | KEEP |
| `strategy/audit_layer.py` | Audit trail | KEEP |
| `strategy/diagnostic.py` | Diagnostic helpers | KEEP |
| `strategy/strategies/*.py` (11 files) | 11 strategy implementations | KEEP |
| `core/datahub.py` | Market data aggregator | KEEP |
| `core/data_quality.py` | Data validation | KEEP |
| `core/exchange.py` | Exchange factory | KEEP (rename to tradingos.signals.feeder) |
| `core/decision_engine.py:Decision` dataclass | FINAL DECISION OBJECT | KEEP |
| `core/decision_engine.py:Decision.is_trade()` | BUY/SELL check | KEEP |
| `core/decision_engine.py:Decision.sl` `Decision.tp` | SL/TP computation | KEEP |
| `execution/risk_manager.py:gate()` | Risk gate | KEEP |
| `execution/risk_manager.py:calc_sl_tp()` | SL/TP formula (ATR-based) | KEEP |
| `execution/execution_router.py:execute()` | CALLS exchange.place_order() | DELETE / REPLACE |
| `execution/order_manager.py` | Order wrapper | DELETE |
| `execution/trade_executor.py` | Trade executor | DELETE |
| `execution/portfolio.py` | Portfolio bookkeeping | DELETE (TradingOS uses different model) |
| `execution/reconciliation.py` | Exchange recon | MERGE — we have /root/tradingos_lab/core/state_reconciliation/ |
| `execution/audit_log.py` | Audit logger | KEEP (compatible with Decision Log) |
| `execution/execution_engine.py` | Engine wrapper | DELETE |
| `execution/execution_quality.py` | Quality metrics | MERGE |
| `execution/order_state.py` | Order state | DELETE |
| `execution/shadow_executor.py` | Shadow trading | DELETE (TradingOS uses Executor Hardened dry-run) |
| `execution/shadow_portfolio.py` | Shadow portfolio | DELETE |
| `core/bingx_executor.py` | BingX exec | DELETE (Executor Hardened does BingX via Bybit adapter pattern) |
| `core/bybit_executor.py` | Bybit exec | DELETE (replaced by Executor Hardened) |
| `core/base_executor.py` | Base exec class | DELETE |
| `core/exchanges/bybit.py` | Bybit REST | DELETE (Executor Hardened has its own) |
| `core/exchanges/bingx.py` | BingX REST | DELETE |
| `core/exchanges/base.py` | Base exchange | DELETE |
| `core/capital_allocation.py` | Capital | KEEP (logic) |
| `core/circuit_breaker.py` | CB | KEEP |
| `core/position_observer.py` | Position observer | MERGE — duplicate of /root/tradingos/services/position_guard_worker.py |
| `core/position_intelligence.py` | Position intel | MERGE — already in tradingos tree |
| `core/asel_v4.py` | Strategy evolution | KEEP |
| `core/integration_engine.py` | Engine integration | KEEP |
| `core/intelligent_manager.py` | IPM | KEEP |
| `core/learning_engine.py` | Learning | KEEP |
| `core/market_regime_*.py` | Regime detection | MERGE — tradingos has this |
| `core/micro_pro/` | MicroPro | KEEP |
| `core/mutation_engine.py` | Mutation | KEEP |
| `core/order_tracker.py` | Order tracking | DELETE |
| `core/probability_filter.py` | Probability filter | KEEP |
| `core/quality_filter.py` | Quality filter | KEEP |
| `core/rate_limiter.py` | Rate limit | KEEP |
| `core/reality_tracker.py` | Reality | KEEP |
| `core/trailing_*.py` | Trailing stop | MERGE — already in tradingos |
| `core/execution_trace.py` | Trace | MERGE — already in tradingos |
| `core/execution_truth.py` | Truth | KEEP |
| `core/health.py` | Health | KEEP |
| `core/indicator_validator.py` | Validator | KEEP |
| `core/lot_sizes.py` | Lot sizing | KEEP |
| `core/market_data_health.py` | Health | KEEP |
| `core/market_data_sync.py` | Sync | KEEP |
| `core/metrics.py` | Metrics | MERGE |
| `core/position_context.py` | Context | KEEP |
| `core/simulator.py` | Simulator | KEEP |
| `core/timeline_engine.py` | Timeline | KEEP |
| `features/feature_store.py` | Feature storage | KEEP |
| `features/indicators.py` | Indicators | KEEP |
| `risk/ai_controller.py` | Risk AI | KEEP |
| `exchange/` (top-level) | Exchange adapt | DELETE |
| `main.py` | Entry point | DELETE |
| `bot_ui_v2/` | Telegram UI | DELETE |
| `control_center/` | Web UI | DELETE |
| `control_center.log`, `bot.log`, `cc.log` | Logs | ARCHIVE |
| `bot_state.db` | SQLite state | ARCHIVE |
| `data/` | Live data dir | ARCHIVE |
| `data/entry_snapshot.jsonl` | Position entries | KEEP in archive |
| `data/signal_generator_audit.json` | Signal audit | KEEP in archive |
| `docs/` | Documentation | MERGE — extract useful content |
| `alpha/` | Analysis | ARCHIVE |
| `analytics/` | Analytics | MERGE |
| `backtest/` | Backtest framework | MERGE |
| `backtest_results/` | Old results | ARCHIVE |
| `event_bus/` | Pub/Sub system | KEEP (reusable) |
| `optimization/` | Optimization | MERGE |
| `strategy/v10_*.py` | V10 systems | MERGE |
| `strategy/v9_*.py` | V9 systems | MERGE |
| `CONFIG*.md`, `CONSTITUTION.md` | Config docs | ARCHIVE |


---

## TASK 2 — Signal Pipeline Trace (with file:line evidence)

```
[Step 1] Data collection
  /opt/ubot_bingx/core/datahub.py: get_context(symbol) → ctx
  Returns: ctx.snapshot (last_price), ctx.features (FeatureVector)
  Input: symbol name
  Output: ctx dataclass with market data snapshot + features
  
[Step 2] Feature computation  
  /opt/ubot_bingx/features/indicators.py: compute_indicators() → FeatureVector
  Reads: OHLCV candles
  Output: FeatureVector (RSI, EMA, ATR, ADX, etc.)
  Immutable: per memory "v8.0 BREAKING CHANGE: принимает FeatureVector (immutable)"

[Step 3] Signal Generation (pure logic)
  /opt/ubot_bingx/strategy/signal_generator.py: SignalGenerator.decide(symbol, fv, bar_idx) → str
  Returns: "BUY" | "SELL" | None
  Logic: L1 (HTF alignment) → L2 (Setup) → L3 (Trigger) → Direction
  HAS ZERO SIDE EFFECTS

[Step 4] Signal Aggregation (multi-strategy)
  /opt/ubot_bingx/strategy/manager.py: StrategyManager.analyze(ctx) → Dict[str, Signal]
  Loops over 11 strategies: breakout, ema, funding_arb, ict, momentum_volume, rsi, scalper, sleeping_box, swing, trend, volume
  Aggregates per-strategy signals

[Step 5] Signal Scoring
  /opt/ubot_bingx/strategy/signal_scoring.py: SignalScoringEngine.calculate_with_vectors(...)
  Returns: SignalScore (weighted probabilities, regime stability, meta risk)
  Filters by minimum probability threshold (default 0.55)

[Step 6] Decision
  /opt/ubot_bingx/core/decision_engine.py: DecisionEngine.evaluate(symbol, signals, ctx) → Decision
  Returns: Decision dataclass
  Fields:
    symbol: str
    action: Action (BUY / SELL / NO_TRADE)
    confidence: float 0..1
    size: float (qty)
    stop_loss: float
    take_profit: float
    entry_price: float
    reason: List[str]
    reject_stage: Optional[str]
  
  is_trade() → returns action != NO_TRADE
  
  THIS IS THE FIRST EXECUTABLE TRADING DECISION POINT
  ═══════════════════════════════════════════════════════════════
  Line ~643-1100: pure decision logic, no execution calls
  ═══════════════════════════════════════════════════════════════
  
[Step 7] Risk Gate
  /opt/ubot_bingx/execution/risk_manager.py: RiskManager.gate(snapshot, score) → bool, str
  Returns: (approved, reason)
  Checks: max_open_positions, daily_loss, kill_switch, max_drawdown
  
[Step 8] SL/TP Computation  
  /opt/ubot_bingx/execution/risk_manager.py: risk_manager.calc_sl_tp(side, price, atr_pct) → tuple
  Returns: (sl, tp) tuple, ATR-based
  Formula: SL = price ± atr_pct * sl_atr_mult, TP = price ± atr_pct * tp_atr_mult
  
[Step 9] SRT-OS Integration (optional gate)
  /opt/ubot_bingx/core/srtos/trust_engine.py: trust_score(score, regime) → float
  Multiplier: 0..1 to scale position size
  
[Step 10] Execution (THIS IS WHERE WE CUT)
  ═══════════════════════════════════════════════════════════════
  REMOVED — replaced by DecisionWriter
  ═══════════════════════════════════════════════════════════════
  Decision.action + Decision.sl + Decision.tp + Decision.size
       ↓
  DecisionWriter.write(decision.json)
       ↓
  /root/trading_brain_v4/research/execution/executor_hardened.py
       ↓
  Bybit V5 API
```

### The CLEAN CUT POINT

**Before cut:**
```python
# core/engine.py:2048-2080
if decision.is_trade:
    result = await self.router.execute(
        symbol=decision.symbol,
        side=decision.action.value,
        price=decision.entry_price,
        sl=decision.sl,
        tp=decision.tp,
        ...
    )
    # THEN: set_trading_stop (line 2066+) calls exchange directly
    await self.exchange.set_trading_stop(symbol, side, stop_loss, take_profit)
```

**After cut:**
```python
# core/engine.py:2048-2050 (NEW)
if decision.is_trade:
    decision_writer.write(decision)  # writes decision.json
    return  # TradingOS Executor Hardened handles execution
    # REMOVED: router.execute() and exchange.set_trading_stop() calls
```

### SCHEMA MAPPING: uBot Decision → decision.json

| uBot field | decision.json field | Notes |
|------------|---------------------|-------|
| `decision.symbol` | `symbol` | direct |
| `decision.action.value` ("BUY"/"SELL") | `direction` | direct |
| `decision.size` | `quantity` | direct |
| `decision.entry_price` | `entry_price` | direct |
| `decision.stop_loss` | `stop_loss` | direct |
| `decision.take_profit` | `take_profit` | direct |
| `decision.confidence` | `confidence` | direct |
| `decision.source_strategy` | `strategy` | NEW field |
| UUID4() | `decision_id` | NEW |
| UUID4() | `event_id` | NEW |
| UUID4() | `trace_id` | NEW |
| iso_now() | `timestamp` | NEW |
| "OPEN_POSITION" | `action` | constant |
| 0.5 (or strategy.risk_pct) | `risk_pct` | defaulted |
| f"POS-{symbol}" | `position_id` | NEW |


---

## TASK 3 — Signal/Execution Separation

### KEEP (signal+decision+risk logic)

| Module | Why |
|--------|-----|
| `strategy/signal_generator.py` | Pure decision logic, no execution |
| `strategy/signal_scoring.py` | Confidence, no execution |
| `strategy/signal_types.py` | Dataclasses, pure data |
| `strategy/signal_scorer.py` | Secondary scorer |
| `strategy/decision_pipeline.py` | Decision flow, no execution |
| `core/decision_engine.py` | Final decision, has Decision dataclass |
| `core/decision_core.py` | Core logic, mergeable |
| `strategy/manager.py` | Multi-strategy aggregator |
| `strategy/loader.py` | Strategy discovery |
| `strategy/base.py` | BaseStrategy abstract |
| `strategy/strategies/*.py` (11 files) | Individual strategies |
| `strategy/audit_layer.py` | Audit trail |
| `strategy/diagnostic.py` | Diagnostic helpers |
| `strategy/regime_engine.py` | Market regime |
| `strategy/regime_filter.py` | Regime filter |
| `strategy/coherence_layer.py` | Cross-strategy coherence |
| `strategy/adaptive_learning.py` | ASEL v4 |
| `strategy/self_model.py` | Self model |
| `strategy/portfolio_risk.py` | Portfolio risk |
| `strategy/meta_risk.py` | Meta risk |
| `strategy/performance.py` | Performance tracking |
| `strategy/optimization.py` | Optimization |
| `strategy/health.py` | Health monitoring |
| `features/feature_store.py` | Feature storage |
| `features/indicators.py` | Indicator calc |
| `risk/ai_controller.py` | Risk AI |
| `core/datahub.py` | Market data hub |
| `core/data_quality.py` | Data validation |
| `core/capital_allocation.py` | Capital alloc |
| `core/circuit_breaker.py` | Circuit breaker |
| `core/decision_engine.py` | Decision engine (Decision class) |
| `core/execution_truth.py` | Truth tracking |
| `core/health.py` | Health |
| `core/integration_engine.py` | Engine integration |
| `core/learning_engine.py` | Learning engine |
| `core/asel_v4.py` | Adaptive strategy evolution |
| `core/probability_filter.py` | Probability |
| `core/quality_filter.py` | Quality |
| `core/rate_limiter.py` | Rate limit |
| `core/market_regime_*.py` | Regime |
| `execution/risk_manager.py` (only `gate()` and `calc_sl_tp()`) | Risk gate + SL/TP formula |
| `execution/audit_log.py` | Audit logger |

### REMOVE (execution-side)

| Module | Why |
|--------|-----|
| `execution/execution_router.py:execute()` | Call site — DELETED |
| `execution/order_manager.py` | Order wrapper |
| `execution/trade_executor.py` | Trade executor |
| `execution/portfolio.py` | Portfolio bookkeeping (TradingOS uses different model) |
| `execution/reconciliation.py` | Duplicate of /root/tradingos_lab/core/state_reconciliation/ |
| `execution/order_state.py` | Order state |
| `execution/shadow_executor.py` | Duplicate of Executor Hardened dry-run |
| `execution/shadow_portfolio.py` | Shadow portfolio |
| `execution/execution_engine.py` | Engine wrapper |
| `execution/execution_quality.py` | Duplicate of existing modules |
| `core/base_executor.py` | Base executor class |
| `core/bybit_executor.py` | Bybit (replaced by Executor Hardened) |
| `core/bingx_executor.py` | BingX (replaced by Executor Hardened) |
| `core/exchange.py` | Exchange factory |
| `core/exchanges/bybit.py` | REST client (replaced) |
| `core/exchanges/bingx.py` | REST client (replaced) |
| `core/exchanges/base.py` | Base exchange |
| `core/order_tracker.py` | Order tracking |
| `core/execution_trace.py` | Duplicate |
| `exchange/account_manager.py` | Replaced |
| `exchange/bybit/adapter.py` | Replaced |
| `exchange/factory.py` | Replaced |
| `exchange/base_exchange.py` | Replaced |
| `exchange/paper_exchange.py` | Replaced |
| `main.py` | Replaced by TradingOS service |
| `bot_ui_v2/` | Telegram UI (TradingOS has its own) |
| `control_center/` | Web UI |


---

## TASK 4 — Migration Map

| Legacy Path | New Path | Action |
|-------------|----------|--------|
| `/opt/ubot_bingx/main.py` | (DELETE) | DELETE |
| `/opt/ubot_bingx/config.py` | `/root/tradingos/config.py` | MOVE |
| `/opt/ubot_bingx/config_manager.py` | `/root/tradingos/config_manager.py` | MOVE |
| `/opt/ubot_bingx/config.yaml` | `/root/tradingos/config_signal.yaml` | MOVE |
| `/opt/ubot_bingx/strategy/signal_generator.py` | `/root/tradingos/signals/signal_generator.py` | MOVE |
| `/opt/ubot_bingx/strategy/signal_scoring.py` | `/root/tradingos/signals/scoring.py` | MOVE |
| `/opt/ubot_bingx/strategy/signal_scorer.py` | `/root/tradingos/signals/scorer.py` | MOVE |
| `/opt/ubot_bingx/strategy/signal_types.py` | `/root/tradingos/signals/types.py` | MOVE |
| `/opt/ubot_bingx/strategy/signal_diagnostic.py` | `/root/tradingos/signals/diagnostic.py` | MOVE |
| `/opt/ubot_bingx/strategy/decision_pipeline.py` | `/root/tradingos/signals/pipeline.py` | MOVE |
| `/opt/ubot_bingx/strategy/manager.py` | `/root/tradingos/signals/registry.py` | MOVE |
| `/opt/ubot_bingx/strategy/loader.py` | `/root/tradingos/signals/loader.py` | MOVE |
| `/opt/ubot_bingx/strategy/base.py` | `/root/tradingos/signals/base.py` | MOVE |
| `/opt/ubot_bingx/strategy/strategies/*.py` (11 files) | `/root/tradingos/signals/strategies/*.py` | MOVE |
| `/opt/ubot_bingx/strategy/audit_layer.py` | `/root/tradingos/signals/audit.py` | MOVE |
| `/opt/ubot_bingx/strategy/coherence_layer.py` | `/root/tradingos/signals/coherence.py` | MOVE |
| `/opt/ubot_bingx/strategy/regime_engine.py` | `/root/tradingos/signals/regime.py` | MOVE |
| `/opt/ubot_bingx/strategy/regime_filter.py` | `/root/tradingos/signals/regime_filter.py` | MOVE |
| `/opt/ubot_bingx/strategy/adaptive_learning.py` | `/root/tradingos/signals/learning.py` | MOVE |
| `/opt/ubot_bingx/strategy/meta_risk.py` | `/root/tradingos/signals/meta_risk.py` | MOVE |
| `/opt/ubot_bingx/strategy/portfolio_risk.py` | `/root/tradingos/signals/portfolio_risk.py` | MOVE |
| `/opt/ubot_bingx/strategy/performance.py` | `/root/tradingos/signals/performance.py` | MOVE |
| `/opt/ubot_bingx/strategy/health.py` | `/root/tradingos/signals/health.py` | MOVE |
| `/opt/ubot_bingx/strategy/self_model.py` | `/root/tradingos/signals/self_model.py` | MOVE |
| `/opt/ubot_bingx/features/feature_store.py` | `/root/tradingos/features/store.py` | MOVE |
| `/opt/ubot_bingx/features/indicators.py` | `/root/tradingos/features/indicators.py` | MOVE |
| `/opt/ubot_bingx/risk/ai_controller.py` | `/root/tradingos/risk/ai_controller.py` | MOVE |
| `/opt/ubot_bingx/execution/risk_manager.py` (gate() + calc_sl_tp()) | `/root/tradingos/risk/gate.py` | EXTRACT (strip execution parts) |
| `/opt/ubot_bingx/core/datahub.py` | `/root/tradingos/data/datahub.py` | MOVE |
| `/opt/ubot_bingx/core/data_quality.py` | `/root/tradingos/data/quality.py` | MOVE |
| `/opt/ubot_bingx/core/decision_engine.py` (Decision dataclass + evaluate) | `/root/tradingos/signals/decision.py` | MOVE |
| `/opt/ubot_bingx/core/decision_core.py` | merged into decision.py | MERGE |
| `/opt/ubot_bingx/core/asel_v4.py` | `/root/tradingos/signals/learning_v4.py` | MOVE |
| `/opt/ubot_bingx/core/integration_engine.py` | `/root/tradingos/core/integration.py` | MOVE |
| `/opt/ubot_bingx/core/probability_filter.py` | `/root/tradingos/signals/probability.py` | MOVE |
| `/opt/ubot_bingx/core/quality_filter.py` | `/root/tradingos/signals/quality.py` | MOVE |
| `/opt/ubot_bingx/core/capital_allocation.py` | `/root/tradingos/risk/capital_allocation.py` | MOVE |
| `/opt/ubot_bingx/core/circuit_breaker.py` | `/root/tradingos/risk/circuit_breaker.py` | MOVE |
| `/opt/ubot_bingx/core/execution_truth.py` | `/root/tradingos/exec/truth.py` | MOVE |
| `/opt/ubot_bingx/core/learning_engine.py` | `/root/tradingos/signals/learning_engine.py` | MOVE |
| `/opt/ubot_bingx/core/market_data_health.py` | `/root/tradingos/data/health.py` | MOVE |
| `/opt/ubot_bingx/core/market_regime_*.py` | `/root/tradingos/signals/regime_*.py` | MOVE |
| `/opt/ubot_bingx/core/health.py` | `/root/tradingos/health.py` | MOVE |
| `/opt/ubot_bingx/strategy/v9_*.py` | (DELETE) | DELETE (legacy unused) |
| `/opt/ubot_bingx/strategy/v10_*.py` | (DELETE) | DELETE (legacy unused) |
| `/opt/ubot_bingx/exchange/` | (DELETE entire dir) | DELETE |
| `/opt/ubot_bingx/bot_ui_v2/` | (DELETE entire dir) | DELETE |
| `/opt/ubot_bingx/control_center/` | (DELETE entire dir) | DELETE |
| `/opt/ubot_bingx/main.py` | (DELETE) | DELETE |
| `/opt/ubot_bingx/alpha/` | /root/tradingos/ARCHIVE/ubot_bingx_legacy/alpha/ | ARCHIVE |
| `/opt/ubot_bingx/backtest/` | /root/tradingos/ARCHIVE/... | ARCHIVE |
| `/opt/ubot_bingx/optimization/` | /root/tradingos/ARCHIVE/... | ARCHIVE |
| `/opt/ubot_bingx/data/` | /root/tradingos/ARCHIVE/... | ARCHIVE |
| `/opt/ubot_bingx/docs/` | /root/tradingos/ARCHIVE/... | ARCHIVE |
| `/opt/ubot_bingx/bot.log`, `cc.log`, etc. | /root/tradingos/ARCHIVE/.../logs/ | ARCHIVE |
| `/opt/ubot_bingx/bot_state.db` | /root/tradingos/ARCHIVE/.../db/ | ARCHIVE |

### New /root/tradingos/signals/ structure
```
/root/tradingos/signals/
├── __init__.py
├── signal_generator.py     ← from ubot_bingx
├── scoring.py              ← from ubot_bingx
├── scorer.py               ← from ubot_bingx
├── types.py                ← from ubot_bingx
├── diagnostic.py           ← from ubot_bingx
├── pipeline.py             ← from ubot_bingx
├── registry.py             ← from ubot_bingx
├── loader.py               ← from ubot_bingx
├── base.py                 ← from ubot_bingx
├── decision.py             ← from ubot_bingx (Decision class)
├── audit.py                ← from ubot_bingx
├── coherence.py            ← from ubot_bingx
├── regime.py               ← from ubot_bingx
├── regime_filter.py        ← from ubot_bingx
├── learning.py             ← from ubot_bingx
├── meta_risk.py            ← from ubot_bingx
├── portfolio_risk.py       ← from ubot_bingx
├── performance.py          ← from ubot_bingx
├── health.py               ← from ubot_bingx
├── self_model.py           ← from ubot_bingx
├── probability.py          ← from ubot_bingx
├── quality.py              ← from ubot_bingx
├── strategies/             ← from ubot_bingx
│   ├── __init__.py
│   ├── breakout.py
│   ├── ema_strategy.py
│   ├── funding_arbitrage.py
│   ├── ict.py
│   ├── momentum_volume.py
│   ├── rsi_strategy.py
│   ├── scalper.py
│   ├── sleeping_box.py
│   ├── swing.py
│   ├── trend.py
│   └── volume_strategy.py
└── writer.py              ← NEW: DecisionWriter
```


---

## TASK 5 — Import Patch List

### Pattern A: `strategy.X` → `tradingos.signals.X`
After MOVE to `/root/tradingos/signals/`, all internal imports become:
```python
# OLD (in core/engine.py)
from strategy.signal_generator import SignalGenerator
from strategy.signal_scoring import SignalScoringEngine
from strategy.signal_types import H1Filter, H4Filter, Setup, Trigger
from strategy.decision_pipeline import DecisionPipeline
from strategy.manager import StrategyManager
from strategy.loader import StrategyLoader

# NEW
from tradingos.signals.signal_generator import SignalGenerator
from tradingos.signals.scoring import SignalScoringEngine
from tradingos.signals.types import H1Filter, H4Filter, Setup, Trigger
from tradingos.signals.pipeline import DecisionPipeline
from tradingos.signals.registry import StrategyManager
from tradingos.signals.loader import StrategyLoader
```

### Pattern B: `execution.X` → `tradingos.risk.X`
```python
# OLD
from execution.risk_manager import RiskManager

# NEW
from tradingos.risk.gate import RiskManager
```

### Pattern C: `core.X` → split into multiple targets
```python
# OLD (in core/engine.py)
from core.datahub import DataHub
from core.circuit_breaker import CircuitBreaker
from core.capital_allocation import CapitalAllocator
from core.decision_engine import DecisionEngine, Decision
from core.asel_v4 import ASELv4
from core.execution_truth import ExecutionTruthLayer
from core.probability_filter import ProbabilityFilter
from core.quality_filter import QualityFilter

# NEW
from tradingos.data.datahub import DataHub
from tradingos.risk.circuit_breaker import CircuitBreaker
from tradingos.risk.capital_allocation import CapitalAllocator
from tradingos.signals.decision import DecisionEngine, Decision
from tradingos.signals.learning_v4 import ASELv4
from tradingos.exec.truth import ExecutionTruthLayer
from tradingos.signals.probability import ProbabilityFilter
from tradingos.signals.quality import QualityFilter
```

### Pattern D: `features.X` → `tradingos.features.X`
```python
# OLD
from features.feature_store import FeatureStore
from features.indicators import compute_indicators

# NEW
from tradingos.features.store import FeatureStore
from tradingos.features.indicators import compute_indicators
```

### Pattern E: `core.exchanges.X` and `exchange.X` — DELETE
```python
# OLD — these imports become INVALID because files are deleted
from core.exchanges.bybit import BybitREST
from exchange.bybit.adapter import BybitAdapter

# NEW — these imports DELETE entirely; all exchange access via trading_brain_v4
# (nothing references these after cut)
```

### Strategy class imports — relabel
Strategy files contain class-level imports like:
```python
from tradingos.signals.base import BaseStrategy
from tradingos.signals.types import H1Filter, H4Filter, Setup, Trigger, StrategySignal
```

### Affected files requiring import patch
By file count:
- core/engine.py: ~10 imports
- strategy/manager.py: ~6 imports
- strategy/signal_*.py (4 files): each ~4 imports
- strategy/decision_pipeline.py: ~5 imports
- strategy/strategies/*.py (11 files): each ~3 imports

**Total imports to patch:** ~80 import statements across ~25 files.

### Automated import patch strategy
```bash
# 1. Move files (mechanical)
mkdir -p /root/tradingos/signals/{strategies,}
cp -r /opt/ubot_bingx/strategy/strategies/* /root/tradingos/signals/strategies/
cp /opt/ubot_bingx/strategy/signal_generator.py /root/tradingos/signals/

# 2. sed-based import rewrites per file
for f in /root/tradingos/signals/*.py; do
    sed -i 's|^from strategy\.|from tradingos.signals.|g' "$f"
    sed -i 's|^from features\.|from tradingos.features.|g' "$f"
    sed -i 's|^from core\.|from tradingos.signals.|g' "$f"  # most core moves to signals
    sed -i 's|^from execution\.|from tradingos.risk.|g' "$f"
done
```

### Manual patches required
- `core/engine.py` line 103-200 (UniBot class init) — refactor class to take decisions dict instead of self.router
- `core/engine.py` line 2048-2080 (router call) — REPLACE with `decision_writer.write(decision)`
- `core/engine.py` line 2066-2080 (set_trading_stop call) — DELETE entirely


---

## TASK 6 — Signal Emitter (DecisionWriter)

### NEW file: `/root/tradingos/signals/writer.py`

```python
"""
DecisionWriter — writes Decision -> decision.json.
Only path from ubot_bingx signal logic to TradingOS Executor Hardened.
No exchange access. No REST. No API keys.
"""
import json
import uuid
import time
import logging
from pathlib import Path

DECISION_PATH = Path("/root/trading_brain_v4/research/execution/decision.json")

log = logging.getLogger("tradingos.signals.writer")


class DecisionWriter:
    def __init__(self, output_path: Path = DECISION_PATH):
        self.path = output_path
    
    def write(self, decision) -> dict:
        """Translate Decision dataclass -> decision.json payload."""
        payload = {
            "trace_id": uuid.uuid4().hex,
            "decision_id": uuid.uuid4().hex,
            "event_id": uuid.uuid4().hex,
            "symbol": decision.symbol,
            "direction": decision.action.value,         # "BUY" / "SELL"
            "quantity": decision.size,
            "entry_price": decision.entry_price,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit,
            "action": "OPEN_POSITION",
            "confidence": decision.confidence,
            "strategy": getattr(decision, "source_strategy", "unknown"),
            "risk_pct": 0.5,
            "position_id": f"POS-{decision.symbol}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Atomic write (temp + rename)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self.path)
        
        log.info(f"decision.json written: symbol={payload['symbol']} side={payload['direction']} qty={payload['quantity']} sl={payload['stop_loss']} tp={payload['take_profit']}")
        return payload
```

### NEW file: `/root/tradingos/signals/service.py` — main loop

```python
"""
TradingOS Signal Service — main daemon loop.

Reads market data through DataHub.
Generates signals through StrategyRegistry (was StrategyManager).
Runs DecisionEngine.
On trade decision: writes decision.json via DecisionWriter.
TradingOS Executor Hardened reads decision.json and executes.

NO EXCHANGE ACCESS. NO EXECUTION.
"""
import asyncio
import logging
from tradingos.signals.decision import DecisionEngine, Action
from tradingos.signals.registry import StrategyManager
from tradingos.signals.loader import StrategyLoader
from tradingos.signals.writer import DecisionWriter
from tradingos.data.datahub import DataHub
from tradingos.risk.gate import RiskManager

log = logging.getLogger("tradingos.signals.service")


class SignalService:
    """Main loop. Was UniBot._process_symbol()."""
    
    def __init__(self, symbols: list[str], decision_path=None):
        self.symbols = symbols
        self.datahub = DataHub()
        self.strategy_mgr = StrategyManager()
        self.strategy_mgr.load_strategies(StrategyLoader().discover())
        self.decision_engine = DecisionEngine()
        self.risk_mgr = RiskManager()
        self.writer = DecisionWriter(decision_path)
        self.running = False
    
    async def cycle(self):
        for symbol in self.symbols:
            ctx = self.datahub.get_context(symbol)
            if not ctx or not ctx.is_ready:
                continue
            
            try:
                signals = await self.strategy_mgr.analyze(ctx)
                decision = self.decision_engine.evaluate(symbol, signals, ctx)
                
                if decision.is_trade:
                    snap = self.portfolio.get_equity_snapshot()
                    approved, reason = self.risk_mgr.gate(snap, score=decision.final_score)
                    if approved:
                        self.writer.write(decision)
            except Exception as e:
                log.exception(f"Error processing {symbol}: {e}")
    
    async def run(self, interval_sec: int = 60):
        self.running = True
        log.info(f"Signal Service started for {self.symbols}")
        while self.running:
            await self.cycle()
            await asyncio.sleep(interval_sec)
```

### Entry point: `/root/tradingos/service_main.py`
```python
import asyncio
import argparse
from tradingos.signals.service import SignalService

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["DOGEUSDT", "BTCUSDT"])
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    
    service = SignalService(args.symbols)
    asyncio.run(service.run(args.interval))
```


---

## TASK 7 — Validation Strategy

### Goal
For identical market snapshots, `TradingOS Signal Service` must produce **identical** direction / confidence / SL / TP / quantity to ubot_bingx's `_process_symbol()`.

### Method
1. **Snapshot test:** Capture uBot's `Decision` for one symbol @ one bar.
2. **Replay:** Feed identical FeatureVector into TradingOS Signal Service.
3. **Compare** outputs:
   - direction (BUY/SELL/NO_TRADE)
   - confidence
   - stop_loss
   - take_profit
   - quantity

### Test Case Setup
```python
# Test that uBot_decision_pipeline.py logic produces same Decision as tradingos/signals/pipeline.py
# Same input FeatureVector → same Decision object

test_input = {
    "symbol": "BTCUSDT",
    "fv": build_test_feature_vector(price=30000, rsi=45, ema20=30100, ...),
    "bar_idx": 1000
}

# Run uBot code (legacy path)
ubot_decision = ubot.signal_generator.decide(test_input)
assert ubot_decision in ("BUY", "SELL", None)

# Run new code (migrated path)
new_decision = tradingos_signals.signal_generator.decide(test_input)
assert new_decision == ubot_decision
```

### Acceptance Criteria
- Direction identical: required
- Confidence within 0.01: required
- SL within 0.0001 of price: required
- TP within 0.0001 of price: required
- Quantity within 1%: required

If any fails, **DO NOT proceed to archive legacy**. Fix code. Re-test.

### Validation Method
After code move:
1. Move files.
2. Run import sweep (verify all imports resolve).
3. Run existing uBot tests if any (`/opt/ubot_bingx/tests/`).
4. Run new unit test on decision.py with same inputs.
5. Confirm SHA match or accept identity up to floating point.
6. THEN archive legacy.


---

## TASK 8 — Cleanup Steps

### Pre-cleanup
- [ ] All tests PASS (`pytest /root/tradingos/signals/`)
- [ ] TradingOS Signal Service produces matching decision.json
- [ ] Executor Hardened processes decision.json successfully
- [ ] Manual verification: 1 paper signal → 1 dry-run → success

### Archive
```bash
TIMESTAMP=$(date +%Y%m%d)
ARCHIVE_DIR="/root/tradingos/ARCHIVE/ubot_bingx_legacy_${TIMESTAMP}"

mkdir -p "$ARCHIVE_DIR"

# Bulk move (NOT delete — preserve audit trail)
mv /opt/ubot_bingx "$ARCHIVE_DIR/code"

# Move logs and data
mv /opt/ubot_bingx_logs.tar.gz "$ARCHIVE_DIR/" 2>/dev/null || true

# Create marker file
cat > "$ARCHIVE_DIR/README.md" << EOF
# ubot_bingx Archive

Archived: $(date +%Y-%m-%d)
Reason: Absorbed into TradingOS at /root/tradingos/signals/
Validation: Decision equivalence verified
Restoration: see /root/tradingos/docs/restoration_guide.md
EOF
```

### Remove runtime dependencies
```bash
# Stop systemd services referencing legacy path
systemctl stop ubot-bingx.service 2>/dev/null
systemctl disable ubot-bingx.service 2>/dev/null

# Remove from crontab
crontab -l | grep -v "ubot_bingx" | crontab -

# Remove from PATH
# (No global PATH entries reference /opt/ubot_bingx per audit)

# Remove from systemd services list
rm -f /etc/systemd/system/ubot-bingx.service
systemctl daemon-reload
```

---

## TASK 9 — Estimated Time

| Phase | Time | Notes |
|-------|------|-------|
| Pre-flight: validate ubot_bingx is dead | 15 min | `ps aux`, `systemctl` |
| Move ~25 strategy files to /tradingos/signals | 60 min | `cp -r` then `sed` imports |
| Move 5 execution files to /tradingos/risk | 30 min | gate + calc_sl_tp |
| Move 10 core files to /tradingos/signals + /data | 60 min | |
| Move 5 features files | 30 min | features/store + features/indicators |
| Create signals/writer.py + signals/service.py | 30 min | NEW code, ~100 lines total |
| Update core/engine.py to call writer instead of router | 30 min | critical code change, minimal logic |
| Test imports + Decision equivalence test | 60 min | required before archive |
| Side-by-side signal comparison (1 cycle) | 30 min | manual verification |
| Archive legacy | 15 min | move, don't delete |
| Update AI_CONTEXT.md, README.md | 30 min | documentation |
| Remove runtime dependencies | 15 min | systemctl disable |
| **Total** | **6 hours** | |

---

## TASK 10 — Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| Import circular dependencies | Build fails | Medium | Use `tradingos.X` package structure |
| Decision object schema drift | Executor mismatch | Medium | Add schema validation test |
| Price feed change (BingX vs Bybit) | Different signal data | Medium | DataHub abstracts exchange |
| Behavior drift (Python path search) | Subtle decision differences | Medium | Run side-by-side validation test |
| Order of magnitude: 80 import statements to patch | Slip a file | Medium | `sed` automation |
| `strategy/diagnostic.py` imports | May have private module use | Low | Manual review |
| `core/micro_pro/` package private | Undetected imports | Low | Manual review |
| `strategy/v9_*` and `strategy/v10_*` files | Stub code, may import deleted modules | Low | Just delete |
| systemd service `ubot-bingx.service` still active | Race with old code | Medium | Explicit `systemctl stop` |
| bot_state.db SQLite lock | Old process still connected | Low | All processes confirmed dead |
| One archive would not preserve audit | Lost history | Low | Archive before delete |

### Risk Mitigation Pattern
1. **Before any change**: snapshot current behavior with frozen market snapshot
2. **After any change**: compare against snapshot, must be identical
3. **Archive, don't delete**: immediate rollback path
4. **Phase validation**: each phase must pass before next phase

---

## FIRST MIGRATION STEP

### STEP ZERO (pre-flight, 15 min)

```bash
# 1.1 Confirm ubot_bingx process is dead
ps aux | grep ubot_bingx | grep -v grep
# Expected: empty output

# 1.2 Snapshot current uBot Decision output for one symbol
cd /opt/ubot_bingx
python3 -c "
import sys
sys.path.insert(0, '.')
# Run one cycle of decision engine to capture baseline
# Save to /tmp/ubot_baseline.json
from core.decision_engine import DecisionEngine
# ... (one synthetic call)
print('Baseline saved')
"

# 1.3 Disable ubot-bingx.service  
sudo systemctl disable ubot-bingx.service 2>/dev/null
sudo systemctl stop ubot-bingx.service 2>/dev/null

# 1.4 Verify no API keys in PATH or active cron
crontab -l | grep ubot_bingx || echo "no ubot_bingx cron"
```

### STEP ONE (mechanical move, 60 min)

```bash
# Create target structure
mkdir -p /root/tradingos/signals/{strategies,}
mkdir -p /root/tradingos/risk
mkdir -p /root/tradingos/data
mkdir -p /root/tradingos/features

# Move signal logic (preserve timestamps + git history if any)
rsync -a /opt/ubot_bingx/strategy/ /root/tradingos/signals/ --exclude='__pycache__' --exclude='*.pyc'
rsync -a /opt/ubot_bingx/features/ /root/tradingos/features/ --exclude='__pycache__' --exclude='*.pyc'
rsync -a /opt/ubot_bingx/risk/ /root/tradingos/risk/ --exclude='__pycache__' --exclude='*.pyc'
rsync -a /opt/ubot_bingx/core/ /root/tradingos/core/ --exclude='__pycache__' --exclude='*.pyc' --exclude='exchanges' --exclude='bingx_executor.py' --exclude='bybit_executor.py' --exclude='base_executor.py' --exclude='order_tracker.py' --exclude='execution_trace.py' --exclude='executions_engine.py' --exclude='execution_quality.py' --exclude='v9_*.py' --exclude='v10_*.py'

# Verify with ls
ls /root/tradingos/signals/ | head -10
ls /root/tradingos/risk/ | head
```

### STEP TWO (import patches, 60 min)

```bash
# Apply sed import rewrites on moved files
cd /root/tradingos/signals

for f in *.py; do
    [ -f "$f" ] || continue
    sed -i \
        -e 's|^from strategy\.|from tradingos.signals.|g' \
        -e 's|^from features\.|from tradingos.features.|g' \
        -e 's|^from core\.|from tradingos.signals.|g' \
        -e 's|^from execution\.|from tradingos.risk.|g' \
        "$f"
done

cd /root/tradingos/risk
for f in *.py; do
    [ -f "$f" ] || continue
    sed -i \
        -e 's|^from execution\.|from tradingos.risk.|g' \
        -e 's|^from strategy\.|from tradingos.signals.|g' \
        "$f"
done

# Smoke test: try importing DecisionEngine
cd /root/tradingos
PYTHONPATH=/root/tradingos python3 -c "from signals.decision import DecisionEngine, Decision; print('OK')"
```

### STEP THREE (writer + service, 30 min)

Create files as shown in TASK 6.

### STEP FOUR (engine.py cut, 30 min)

Replace `core/engine.py` `router.execute(...)` call with `decision_writer.write(decision)`. Delete the `set_trading_stop` block.

### STEP FIVE (validation, 60 min)

Compare Decision outputs before/after for same FeatureVector. Confirm no drift.

### STEP SIX (archive, 15 min)

```bash
ARCHIVE=/root/tradingos/ARCHIVE/ubot_bingx_legacy_$(date +%Y%m%d)
mkdir -p "$ARCHIVE"
mv /opt/ubot_bingx "$ARCHIVE/code"
```

### End-state

```
/root/tradingos/
├── signals/         ← absorbed from ubot_bingx
├── risk/            ← absorbed from ubot_bingx
├── data/            ← absorbed from ubot_bingx
├── features/        ← absorbed from ubot_bingx
├── core/            ← partially absorbed (engine.py + decision.py)
├── execution/       ← unchanged (Executor Hardened)
├── guardian/
├── journal/
├── docs/
├── memory/
└── ARCHIVE/
    └── ubot_bingx_legacy_<date>/
        └── code/   ← preserved for audit/rollback
```

NO mention of `ubot`, `ubot_bingx`, or `/opt/ubot*` in runtime paths.

