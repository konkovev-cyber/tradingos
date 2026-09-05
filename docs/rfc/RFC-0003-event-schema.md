# RFC-0003: Event Schema

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0001, RFC-0002 |
| **Blocks** | RFC-0004, RFC-0005 |

## 1. Цель

Определить **каноническую схему всех событий** TradingOS. Каждое событие:
- имеет жёсткий envelope
- имеет типизированный payload (per version)
- имеет trace_id для сквозной связи
- публикуется в EventBus и сохраняется в Data Lake

## 2. Event Envelope (обязательная обёртка)

```json
{
  "event_id": "uuid-v4",
  "event_type": "SignalGenerated",
  "schema_version": "1.0.0",
  "timestamp": "2026-07-05T12:00:00.123456Z",
  "source_module": "strategies.liquidity_sweep",
  "trace_id": "uuid-v4",
  "correlation_id": "uuid-v4|null",
  "parent_event_id": "uuid-v4|null",
  "symbol": "BTCUSDT",
  "timeframe": "5m|null",
  "severity": "info|warning|error",
  "payload": { ... }
}
```

### 2.1 Поля envelope

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `event_id` | UUID | ✅ | Уникальный id события |
| `event_type` | string | ✅ | Имя из реестра (см. §3) |
| `schema_version` | semver | ✅ | Версия схемы payload'а |
| `timestamp` | datetime UTC | ✅ | Когда произошло |
| `source_module` | string | ✅ | Кто сгенерировал |
| `trace_id` | UUID | ✅ | Сквозная связь сценария |
| `correlation_id` | UUID | ❌ | Связь со связанным сценарием |
| `parent_event_id` | UUID | ❌ | Прямой родитель в causal chain |
| `symbol` | string | ❌ | Если привязано к рынку |
| `timeframe` | string | ❌ | Если привязано к TF |
| `severity` | enum | ✅ | `info` / `warning` / `error` |
| `payload` | object | ✅ | Типизированное тело |

### 2.2 Trace / Correlation правила

- **`trace_id`** — один сценарий от начала до конца. Например, `trace_id` живёт с момента `CandleClosed` до `TradeRecorded` одной сделки.
- **`correlation_id`** — связь между сценариями. Например, все события одного бара имеют `correlation_id = bar_id`.
- **`parent_event_id`** — прямой причинный родитель. `SignalGenerated` → `DecisionCreated` → `OrderSubmitted` → `OrderFilled` — это цепочка.

## 3. Каталог событий (по доменам RFC-0002)

### 3.1 Market events

#### `MarketConfigured` (v1.0.0)
```json
{
  "market_id": "uuid",
  "symbol": "BTCUSDT",
  "exchange": "bybit",
  "venue_type": "spot",
  "tick_size": 0.01,
  "lot_size": 0.0001
}
```

#### `MarketSuspended` (v1.0.0)
```json
{"market_id": "uuid", "reason": "delisted|maintenance|halt"}
```

### 3.2 Market data events

#### `TickReceived` (v1.0.0)
```json
{
  "market_id": "uuid",
  "price": 62595.90,
  "volume": 0.001,
  "side": "buy|sell"
}
```

#### `CandleClosed` (v1.0.0)
```json
{
  "market_id": "uuid",
  "timeframe": "5m",
  "open_ts": "2026-07-05T12:00:00Z",
  "close_ts": "2026-07-05T12:05:00Z",
  "open": 62590.0, "high": 62600.0, "low": 62580.0, "close": 62595.9,
  "volume": 12.5, "quote_volume": 781948.75, "trades_count": 1452
}
```

#### `OrderBookUpdated` (v1.0.0)
```json
{
  "market_id": "uuid",
  "bids": [[62595.5, 1.2], [62595.0, 0.8]],
  "asks": [[62596.0, 0.5], [62596.5, 1.0]],
  "depth": 50
}
```

#### `FundingUpdated` (v1.0.0)
```json
{"market_id": "uuid", "rate": 0.0001, "next_ts": "..."}
```

#### `OpenInterestUpdated` (v1.0.0)
```json
{"market_id": "uuid", "oi": 12345.67, "delta_24h": 0.05}
```

### 3.3 Intelligence events

#### `FeatureComputed` (v1.0.0)
```json
{
  "feature_id": "uuid",
  "market_id": "uuid",
  "name": "rsi_14",
  "version": "1.0.0",
  "value": 62.4,
  "params": {"period": 14}
}
```

#### `RegimeDetected` (v1.0.0)
```json
{
  "regime_id": "uuid",
  "market_id": "uuid",
  "regime_type": "TREND",
  "confidence": 0.87,
  "model_id": "uuid",
  "features_ref": ["uuid", "uuid"]
}
```

#### `RegimeChanged` (v1.0.0)
```json
{"market_id": "uuid", "from_regime": "RANGE", "to_regime": "TREND", "regime_id": "uuid"}
```

#### `RegimeExpired` (v1.0.0)
```json
{"regime_id": "uuid", "lifetime_seconds": 3600}
```

#### `AnomalyDetected` (v1.0.0)
```json
{
  "anomaly_id": "uuid",
  "market_id": "uuid",
  "anomaly_type": "volume_spike",
  "severity": 0.92,
  "baseline": 12.5, "observed": 145.3
}
```

### 3.4 Signal/Decision events

#### `SignalGenerated` (v1.0.0)
```json
{
  "signal_id": "uuid",
  "market_id": "uuid",
  "source": "sweep_v1",
  "direction": "BUY",
  "confidence": 0.83,
  "expected_apy": 0.0,
  "features_ref": ["uuid"],
  "regime_ref": "uuid",
  "ttl_seconds": 300,
  "metadata": {"sweep_level": 62600.0}
}
```

#### `SignalRouted` (v1.0.0)
```json
{"signal_id": "uuid", "router": "core.signal_router", "raw": true}
```

#### `SignalAccepted` (v1.0.0)
```json
{"signal_id": "uuid", "decision_id": "uuid"}
```

#### `SignalRejected` (v1.0.0)
```json
{
  "signal_id": "uuid",
  "reasons": ["low_confidence", "kill_switch"],
  "details": {"confidence": 0.42, "kill_level": "FREEZE"}
}
```

#### `SignalExpired` (v1.0.0)
```json
{"signal_id": "uuid", "ttl_exceeded_seconds": 600}
```

#### `DecisionCreated` (v1.0.0)
```json
{
  "decision_id": "uuid",
  "signal_id": "uuid",
  "verdict": "APPROVE|REJECT|MODIFY",
  "reasons": [],
  "modifications": {},
  "portfolio_state_ref": "uuid"
}
```

#### `DecisionApplied` (v1.0.0)
```json
{"decision_id": "uuid", "order_id": "uuid|null"}
```

### 3.5 Execution events

#### `OrderCreated` (v1.0.0)
```json
{
  "order_id": "uuid",
  "client_order_id": "ord-...",
  "decision_id": "uuid",
  "market_id": "uuid",
  "side": "buy",
  "type": "market|limit",
  "quantity": 0.001,
  "price": null,
  "time_in_force": "GTC"
}
```

#### `OrderSubmitted` (v1.0.0)
```json
{"order_id": "uuid", "exchange_order_id": "bybit-..."}
```

#### `OrderFilled` (v1.0.0)
```json
{"order_id": "uuid", "execution_id": "uuid", "filled_qty": 0.001}
```

#### `OrderPartiallyFilled` (v1.0.0)
```json
{"order_id": "uuid", "execution_id": "uuid", "filled_qty": 0.0005, "remaining": 0.0005}
```

#### `OrderRejected` (v1.0.0)
```json
{"order_id": "uuid", "reason": "insufficient_margin|invalid_price|exchange_kill"}
```

#### `OrderCancelled` (v1.0.0)
```json
{"order_id": "uuid", "reason": "user|risk|timeout"}
```

#### `ExecutionRecorded` (v1.0.0)
```json
{
  "execution_id": "uuid",
  "order_id": "uuid",
  "price": 62596.0,
  "quantity": 0.001,
  "fee": 0.0125,
  "fee_currency": "USDT",
  "liquidity": "taker",
  "slippage_bps": 0.8,
  "latency_ms": 145
}
```

### 3.6 Position/Trade events

#### `PositionOpened` (v1.0.0)
```json
{
  "position_id": "uuid",
  "market_id": "uuid",
  "side": "long",
  "quantity": 0.001,
  "avg_entry_price": 62596.0,
  "stop_loss": 62400.0,
  "take_profit": 62900.0,
  "strategy_ref": "uuid"
}
```

#### `PositionUpdated` (v1.0.0)
```json
{"position_id": "uuid", "unrealized_pnl": 1.5, "quantity": 0.001}
```

#### `PositionClosed` (v1.0.0)
```json
{"position_id": "uuid", "trade_id": "uuid", "realized_pnl": 4.2}
```

#### `TradeRecorded` (v1.0.0)
```json
{
  "trade_id": "uuid",
  "position_ref": "uuid",
  "direction": "long",
  "entry_price": 62596.0, "exit_price": 62645.0,
  "quantity": 0.001,
  "gross_pnl": 0.049, "fees": 0.0125, "net_pnl": 0.0365,
  "r_multiple": 1.8,
  "exit_reason": "take_profit",
  "duration_seconds": 1800,
  "regime_at_open": "uuid", "regime_at_close": "uuid"
}
```

### 3.7 Portfolio/Risk events

#### `PortfolioUpdated` (v1.0.0)
```json
{
  "portfolio_id": "uuid",
  "equity": 10000.0, "balance": 10004.2, "margin_used": 100.0,
  "unrealized_pnl": 1.5, "daily_pnl": 12.3
}
```

#### `RiskStateChanged` (v1.0.0)
```json
{"from": "NORMAL", "to": "DE_RISK", "reason": "daily_loss_threshold"}
```

### 3.8 Research events

#### `ShadowResult` (v1.0.0)
```json
{
  "shadow_id": "uuid",
  "signal_ref": "uuid",
  "would_have_entered": true,
  "simulated_entry_price": 62596.0,
  "simulated_exit_price": 62620.0,
  "simulated_pnl": 0.024,
  "horizon_bars": 6
}
```

#### `EdgeDiscovered` (v1.0.0)
```json
{
  "edge_id": "uuid",
  "name": "sweep_btc_5m_trend_funding_neg",
  "hypothesis": "After 3 red candles + neg funding + rising OI in TREND regime, BTC tends to bounce within 5m with 72% probability",
  "sample_size": 245,
  "winrate": 0.72, "expectancy": 0.012, "profit_factor": 1.8
}
```

#### `EdgeValidated` (v1.0.0)
```json
{"edge_id": "uuid", "evidence_id": "uuid", "status": "validated|live|decaying|retired"}
```

#### `ReplayStarted` / `ReplayFinished` (v1.0.0)
```json
{"session_id": "uuid", "market_id": "uuid", "period_start": "...", "period_end": "..."}
```

## 4. Schema versioning policy

- **Major** (1.0.0 → 2.0.0) — breaking: новое обязательное поле, удаление, изменение типа
- **Minor** (1.0.0 → 1.1.0) — additive: новое опциональное поле
- **Patch** (1.0.0 → 1.0.1) — typo fix, clarification

В Data Lake хранилище держит последние 2 мажорные версии читаемыми.

## 5. Валидация

Каждое событие при публикации проходит через `SchemaRegistry.validate(event_type, schema_version, payload)`. Невалидное → `ERROR` event + reject.

## 6. Storage

- **Hot (последние 7 дней):** SQLite + JSON для быстрых запросов
- **Warm (до 90 дней):** Parquet по датам
- **Cold (>90 дней):** тот же Parquet, сжатый

## 7. Что дальше

- RFC-0004: Research Data Lake — физическая реализация
- Реализация `core/event_bus/` с поддержкой этого envelope
- Реализация `core/schema_registry/` с JSON Schema для каждого типа
