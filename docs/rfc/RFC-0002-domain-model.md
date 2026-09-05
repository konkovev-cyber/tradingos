# RFC-0002: Core Domain Model (Конституция)

| Field | Value |
|---|---|
| **Status** | Draft |
| **Author** | Architecture team |
| **Created** | 2026-07-05 |
| **Depends on** | RFC-0001 |
| **Blocks** | RFC-0003, RFC-0004, RFC-0005 |

## 1. Цель

Определить **канонические сущности** TradingOS. Каждая сущность:
- имеет UUID
- имеет lifecycle (стадии + переходы)
- имеет owner (какой модуль её создаёт/изменяет)
- имеет набор events (что она порождает/потребляет)
- имеет state (текущее состояние + история)
- имеет schema version
- имеет relations (с какими сущностями связана)

## 2. Доменные области

```
Market → Feature → Signal → Decision → Order → Execution → Position → Trade → Portfolio
                                                                                  ↓
                                                                            Research → Knowledge → Learning
```

## 3. Сущности (канонические)

### 3.1 Market (Рынок)

Описывает торгуемый инструмент на конкретной бирже.

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | Уникальный идентификатор |
| `symbol` | str | `BTCUSDT`, `XAUUSD`, `EURUSD` |
| `exchange` | str | `bybit`, `binance`, `mt5` |
| `venue_type` | enum | `spot`, `futures`, `options`, `cfd` |
| `tick_size` | float | Минимальный шаг цены |
| `lot_size` | float | Минимальный объём |
| `contract_size` | float | Размер контракта (для фьючерсов) |
| `quote_currency` | str | Валюта котировки |
| `base_currency` | str | Базовая валюта |
| `metadata` | dict | Биржевая специфика |

**Lifecycle:** `discovered → configured → active → suspended → deprecated`

**Owner:** `adapters/*` (при регистрации инструмента)
**Events:** `MarketConfigured`, `MarketSuspended`
**Relations:** → `Feature`, `Signal`, `Trade`

### 3.2 Candle (Свеча)

Атомарный факт о цене за период.

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | |
| `market_id` | UUID | FK → Market |
| `timeframe` | enum | `M1`, `M5`, `H1`, `D1` |
| `timestamp_open` | datetime | UTC |
| `timestamp_close` | datetime | UTC |
| `open`, `high`, `low`, `close` | float | |
| `volume` | float | Базовый объём |
| `quote_volume` | float | Объём в котировке |
| `trades_count` | int | Число сделок |

**Lifecycle:** immutable (append-only)
**Owner:** `adapters/*` (data feed)
**Events:** `CandleClosed`
**Relations:** → Market

### 3.3 Tick (Тик)

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | |
| `market_id` | UUID | |
| `timestamp` | datetime | UTC, ms precision |
| `price` | float | |
| `volume` | float | |
| `side` | enum | `buy`, `sell` |

**Lifecycle:** immutable
**Owner:** `adapters/*`
**Events:** `TickReceived`
**Relations:** → Market

### 3.4 OrderBookSnapshot

| Поле | Тип |
|---|---|
| `id` | UUID |
| `market_id` | UUID |
| `timestamp` | datetime |
| `bids` | list[(price, volume)] |
| `asks` | list[(price, volume)] |
| `depth` | int |

**Lifecycle:** immutable
**Owner:** `adapters/*`
**Events:** `OrderBookUpdated`

### 3.5 Feature (Признак)

Вычисляемый числовой признак рынка в конкретный момент.

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | |
| `market_id` | UUID | |
| `timestamp` | datetime | |
| `name` | str | `rsi_14`, `atr_20`, `funding_rate`, `vwap` |
| `version` | str | Семантическая версия формулы |
| `value` | float | |
| `params` | dict | Параметры вычисления |

**Lifecycle:** `computed → consumed → aged`
**Owner:** `intelligence/feature_factory`
**Events:** `FeatureComputed`
**Relations:** ← Candle, → Signal

### 3.6 Regime (Режим рынка)

Классификация текущего состояния рынка.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `market_id` | UUID |
| `timestamp` | datetime |
| `regime_type` | enum | `TREND`, `RANGE`, `CRASH`, `RECOVERY`, `HIGH_VOL`, `CHAOS`, `COMPRESSION` |
| `confidence` | float | [0..1] |
| `model_id` | UUID | Какая модель поставила диагноз |
| `features_ref` | list[UUID] | На каких признаках основан |

**Lifecycle:** `detected → active → expired`
**Owner:** `intelligence/regime_ai`
**Events:** `RegimeDetected`, `RegimeChanged`, `RegimeExpired`
**Relations:** → Market, ← Features, → Signal

### 3.7 Signal (Сигнал)

Указание от стратегии, что есть гипотеза для входа.

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | |
| `market_id` | UUID | |
| `timestamp` | datetime | |
| `source` | str | `sweep_v1`, `funding_v1`, `grid_v1` |
| `direction` | enum | `BUY`, `SELL`, `EARN_LONG`, `EARN_SHORT`, `FLAT` |
| `confidence` | float | [0..1] |
| `expected_apy` | float | Для earn-стратегий |
| `features_ref` | list[UUID] | На каких признаках основан |
| `regime_ref` | UUID | При каком режиме |
| `metadata` | dict | Специфика стратегии |
| `ttl` | int | Секунд жизни до истечения |

**Lifecycle:** `generated → routed → accepted|rejected|expired`
**Owner:** `strategies/*`
**Events:** `SignalGenerated`, `SignalRouted`, `SignalAccepted`, `SignalRejected`, `SignalExpired`
**Relations:** ← Feature, ← Regime, → Decision

### 3.8 Decision (Решение)

Финальное разрешение/отказ на основе сигналов после всех gate'ов.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `signal_ref` | UUID | |
| `timestamp` | datetime |
| `verdict` | enum | `APPROVE`, `REJECT`, `MODIFY` |
| `reasons` | list[str] | `low_confidence`, `kill_switch`, `max_positions` |
| `modifications` | dict | `reduce_size`, `change_direction` |
| `portfolio_state_ref` | UUID |

**Lifecycle:** `created → applied`
**Owner:** `decision/decision_engine`
**Events:** `DecisionCreated`, `DecisionApplied`
**Relations:** ← Signal, → Order

### 3.9 Order (Ордер)

Заявка на биржу.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `client_order_id` | str | Idempotency key |
| `decision_ref` | UUID | |
| `market_id` | UUID | |
| `timestamp` | datetime |
| `side` | enum | `buy`, `sell` |
| `type` | enum | `market`, `limit`, `stop`, `stop_limit` |
| `quantity` | float |
| `price` | float | None для market |
| `time_in_force` | enum | `GTC`, `IOC`, `FOK` |
| `status` | enum | `pending`, `submitted`, `filled`, `partial`, `rejected`, `cancelled` |
| `exchange_order_id` | str | ID на бирже |

**Lifecycle:** `created → submitted → filled|partial|rejected|cancelled`
**Owner:** `decision/execution_optimizer` + `adapters/*`
**Events:** `OrderCreated`, `OrderSubmitted`, `OrderFilled`, `OrderPartiallyFilled`, `OrderRejected`, `OrderCancelled`
**Relations:** ← Decision, → Execution, → Position

### 3.10 Execution (Исполнение)

Факт частичного/полного исполнения ордера.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `order_ref` | UUID |
| `timestamp` | datetime |
| `price` | float | Цена исполнения |
| `quantity` | float | |
| `fee` | float |
| `fee_currency` | str |
| `liquidity` | enum | `maker`, `taker` |
| `slippage_bps` | float |
| `latency_ms` | float |

**Lifecycle:** immutable
**Owner:** `adapters/*`
**Events:** `ExecutionRecorded`

### 3.11 Position (Позиция)

Текущее состояние владения инструментом.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `market_id` | UUID |
| `opened_at` | datetime |
| `closed_at` | datetime | None если открыта |
| `side` | enum | `long`, `short` |
| `quantity` | float |
| `avg_entry_price` | float |
| `realized_pnl` | float |
| `unrealized_pnl` | float |
| `stop_loss` | float | None |
| `take_profit` | float | None |
| `strategy_ref` | UUID |

**Lifecycle:** `opened → updated → closed`
**Owner:** `decision/risk_engine`
**Events:** `PositionOpened`, `PositionUpdated`, `PositionClosed`
**Relations:** → Market, ← Order/Execution

### 3.12 Trade (Сделка)

Закрытая позиция с результатом.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `position_ref` | UUID |
| `opened_at`, `closed_at` | datetime |
| `direction` | enum |
| `entry_price`, `exit_price` | float |
| `quantity` | float |
| `gross_pnl` | float |
| `fees` | float |
| `net_pnl` | float |
| `r_multiple` | float | PnL в единицах риска |
| `exit_reason` | enum | `take_profit`, `stop_loss`, `time_exit`, `signal_reversal`, `risk_kill` |
| `duration_seconds` | int |
| `regime_at_open`, `regime_at_close` | UUID |
| `signals_ref` | list[UUID] |

**Lifecycle:** immutable (создаётся при закрытии Position)
**Owner:** `decision/risk_engine`
**Events:** `TradeRecorded`
**Relations:** → Position, → Portfolio

### 3.13 Portfolio (Портфель)

Агрегированное состояние счёта.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `timestamp` | datetime |
| `equity` | float |
| `balance` | float |
| `margin_used` | float |
| `margin_available` | float |
| `unrealized_pnl` | float |
| `daily_pnl` | float |
| `positions_ref` | list[UUID] |
| `risk_state` | enum | `NORMAL`, `DE_RISK`, `FREEZE`, `FLATTEN`, `SHUTDOWN` |
| `daily_loss_limit_pct` | float |
| `max_drawdown_pct` | float |

**Lifecycle:** continuously updated
**Owner:** `decision/portfolio_ai`
**Events:** `PortfolioUpdated`, `RiskStateChanged`
**Relations:** → Positions

### 3.14 Anomaly (Аномалия)

Обнаруженное аномальное состояние рынка.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `market_id` | UUID |
| `timestamp` | datetime |
| `anomaly_type` | enum | `volume_spike`, `spread_explosion`, `funding_extreme`, `oi_jump`, `liquidity_vacuum` |
| `severity` | float | [0..1] |
| `baseline` | float | Нормальное значение |
| `observed` | float | Наблюдаемое |
| `features_ref` | list[UUID] |

**Lifecycle:** `detected → acknowledged → expired`
**Owner:** `intelligence/anomaly_detector`
**Events:** `AnomalyDetected`

### 3.15 Edge (Статистическое преимущество)

Найденная закономерность с доказанным edge.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `name` | str | `sweep_btc_5m_trend_funding_neg` |
| `version` | str | |
| `hypothesis` | str | Описание на естественном языке |
| `conditions` | dict | Feature conditions |
| `sample_size` | int |
| `winrate` | float |
| `expectancy` | float |
| `profit_factor` | float |
| `max_drawdown` | float |
| `lifetime_pnl` | float |
| `status` | enum | `discovered`, `validated`, `live`, `decaying`, `retired` |
| `discovered_at` | datetime |
| `last_validated_at` | datetime |
| `evidence_ref` | UUID | Ссылка на Evidence |

**Lifecycle:** `discovered → validated → live → decaying → retired`
**Owner:** `intelligence/edge_discovery`
**Events:** `EdgeDiscovered`, `EdgeValidated`, `EdgeRetired`

### 3.16 Evidence (Доказательство)

Статистический отчёт о проверке edge.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `edge_ref` | UUID |
| `period_start`, `period_end` | datetime |
| `trades_count` | int |
| `sharpe` | float |
| `sortino` | float |
| `calmar` | float |
| `var_95` | float |
| `expected_shortfall_95` | float |
| `p_value` | float |
| `stability_window` | list[float] | Метрика по окнам |
| `replay_results_ref` | UUID |
| `shadow_results_ref` | UUID |

**Lifecycle:** immutable
**Owner:** `research/evidence`

### 3.17 Shadow Result (Результат симуляции)

| Поле | Тип |
|---|---|
| `id` | UUID |
| `signal_ref` | UUID |
| `timestamp` | datetime |
| `would_have_entered` | bool |
| `simulated_entry_price` | float |
| `simulated_exit_price` | float |
| `simulated_pnl` | float |
| `horizon_bars` | int |
| `actual_outcome_ref` | UUID | None если будущее |

### 3.18 Replay Session

| Поле | Тип |
|---|---|
| `id` | UUID |
| `name` | str |
| `market_id` | UUID |
| `period_start`, `period_end` | datetime |
| `data_source` | str |
| `strategy_versions` | dict |
| `results_ref` | UUID |
| `deterministic` | bool |

### 3.19 Knowledge (Знание)

Извлечённый урок из данных.

| Поле | Тип |
|---|---|
| `id` | UUID |
| `timestamp` | datetime |
| `type` | enum | `pattern`, `anti_pattern`, `regime_rule`, `edge_decay_rule` |
| `statement` | str | Естественно-языковое описание |
| `supporting_evidence_refs` | list[UUID] |
| `confidence` | float |
| `applicable_scope` | dict | Когда применимо |

**Lifecycle:** `hypothesized → validated → incorporated → deprecated`
**Owner:** `intelligence/learning_engine`

## 4. Матрица relations

| From | To | Cardinality | Описание |
|---|---|---|---|
| Market | Candle | 1:N | Рынок порождает свечи |
| Market | Feature | 1:N | Признаки рынка |
| Candle | Feature | 1:N | Из свечей считаются признаки |
| Feature | Signal | N:1 | Из признаков рождается сигнал |
| Regime | Signal | 1:N | Сигналы в рамках режима |
| Signal | Decision | 1:1 | Сигнал → одно решение |
| Decision | Order | 1:1 | Решение → один ордер |
| Order | Execution | 1:N | Ордер → несколько исполнений |
| Execution | Position | N:1 | Исполнения формируют позицию |
| Position | Trade | 1:1 | Закрытая позиция = сделка |
| Trade | Portfolio | N:1 | Сделки обновляют портфель |
| Edge | Signal | 1:N | Edge порождает сигналы |
| Edge | Evidence | 1:N | Edge имеет доказательства |
| Evidence | Replay Session | N:N | |
| Anomaly | Signal | 1:N | Аномалии могут инвалидировать сигналы |
| Knowledge | Edge | N:N | Знание подтверждает/опровергает edge |

## 5. Conventions

- **UUID** — RFC 4122 v4, string в lower-case с дефисами
- **Timestamp** — ISO 8601 UTC с микросекундами, suffix `Z`
- **Money** — float, всегда в `quote_currency` market'а
- **Volume** — float, в `base_currency`
- **Precision** — хранить в исходной точности, округлять только при выводе
- **Naming** — snake_case везде (Python, SQL, JSON)

## 6. Backwards compatibility

Любое изменение сущности требует:
1. Инкремент `schema_version`
2. Запись в `docs/rfc/RFC-NNNN-migrations.md`
3. Migration function в `core/data_lake/migrations/`
4. Старая версия читаема минимум 2 мажорных релиза

## 7. Что дальше

- RFC-0003 — Event Schema: типы событий и их payload для каждой сущности
- RFC-0004 — Research Data Lake: физическое хранение с учётом этой модели
- RFC-0005 — Knowledge Graph: реализация матрицы relations как графа
