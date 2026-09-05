# RFC-0016: Trading Query Language (TQL)

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0000, RFC-0014 |
| **Blocks** | RFC-0017 (Query Engine) |

## 1. Цель

Определить **синтаксис и семантику** языка запросов TradingOS. TQL — это язык, через который исследователь, трейдер или AI-агент спрашивает систему о том, что произошло на рынке.

## 2. Принципы TQL

1. **Declarative.** Описываем **что** хотим, не **как** искать.
2. **Human-readable.** Запросы читаются как английские предложения.
3. **Composable.** Вложенные запросы разрешены.
4. **Traceable.** Результат всегда содержит `trace_id` и `timeline`.
5. **Schema-aware.** TQL знает о сущностях из RFC-0002.

## 3. Синтаксис

### 3.1 Базовый

```
SELECT <what>
FROM <source>
WHERE <conditions>
[ORDER BY <field>]
[LIMIT <n>]
```

### 3.2 Примеры

#### Все сделки BTC за 90 дней

```
SELECT trades
FROM trades
WHERE symbol == "BTCUSDT"
  AND closed_at >= "2026-04-05"
ORDER BY closed_at DESC
LIMIT 100
```

#### Сделки с убытком, где Regime == TREND

```
SELECT trades
WHERE symbol == "BTCUSDT"
  AND net_pnl < 0
  AND regime_at_open == "TREND"
  AND closed_at >= NOW - 90d
```

#### Edge, использующие VWAP и умершие менее чем за 30 дней

```
SELECT edges
WHERE uses_feature == "vwap"
  AND status == "decaying"
  AND (promoted_to_live - discovered_at) < 30d
```

#### Топ-5 сделок по R-multiple с Evidence

```
SELECT trades
WHERE r_multiple > 2.0
  AND evidence_ref IS NOT NULL
ORDER BY r_multiple DESC
LIMIT 5
```

#### Trace — полная цепочка одной сделки

```
TRACE trades/{trade_id}
```

#### Timeline — хронология события

```
TIMELINE trades/{trade_id}
  INCLUDE features, signals, decision, execution, evidence
```

#### Агрегация — winrate по режимам

```
SELECT 
  regime_at_open,
  COUNT(*) AS total,
  SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
  AVG(r_multiple) AS avg_r
FROM trades
WHERE symbol == "BTCUSDT"
  AND closed_at >= NOW - 90d
GROUP BY regime_at_open
```

## 4. Источники данных (FROM)

| Источник | Описание |
|---|---|
| `events` | Все события Data Lake |
| `trades` | Закрытые сделки |
| `signals` | Сгенерированные сигналы |
| `decisions` | Принятые решения |
| `orders` | Ордера |
| `features` | Вычисленные признаки |
| `regimes` | Определённые режимы |
| `edges` | Найденные преимущества |
| `evidence` | Статистические доказательства |
| `anomalies` | Обнаруженные аномалии |

## 5. Операторы

| Оператор | Значение | Пример |
|---|---|---|
| `==` | Равно | `regime == "TREND"` |
| `!=` | Не равно | `exit_reason != "take_profit"` |
| `>` / `<` | Больше / меньше | `r_multiple > 2.0` |
| `>=` / `<=` | Больше/меньше или равно | `confidence >= 0.82` |
| `IN` | В списке | `regime IN ["TREND", "RECOVERY"]` |
| `LIKE` | Паттерн | `name LIKE "sweep_*"` |
| `IS NULL` | Null | `evidence_ref IS NULL` |
| `BETWEEN` | Диапазон | `pnl BETWEEN -100 AND 100` |
| `NOW` | Текущее время | `NOW - 7d` |
| `NOW - <n><unit>` | Время назад | `closed_at >= NOW - 30d` |

## 6. Специальные команды

| Команда | Описание |
|---|---|
| `TRACE <type>/<id>` | Полная цепочка по ID |
| `TIMELINE <type>/<id>` | Хронология с контекстом |
| `GRAPH <type>/<id>` | Subgraph из Knowledge Graph |
| `AGGREGATE <field>` | Агрегация по полю |
| `COMPARE <type1> <type2>` | Сравнение двух объектов |

## 7. Результат

```json
{
  "query": "SELECT trades WHERE symbol == 'BTCUSDT' AND net_pnl < 0",
  "status": "ok",
  "execution_time_ms": 12,
  "total": 47,
  "limit": 1000,
  "data": [
    {
      "trade_id": "uuid-1",
      "symbol": "BTCUSDT",
      "direction": "long",
      "entry_price": 62596.0,
      "exit_price": 62450.0,
      "net_pnl": -1.46,
      "r_multiple": -1.8,
      "regime_at_open": "TREND",
      "evidence_ref": "uuid-ev1",
      "trace_id": "uuid-tr1"
    }
  ],
  "aggregations": null
}
```

## 8. Валидация

- Все имена полей проверяются Against RFC-0002 Domain Model
- Невалидный синтаксис → ошибка `SyntaxError`
- Неизвестное поле → ошибка `FieldNotFound`
- Невалидный тип сравнения → ошибка `TypeMismatch`

## 9. Реализация

TQL парсится в **Query AST**, затем выполняется Query Engine (RFC-0017):

```
TQL string
    ↓
TQL Parser (RFC-0017)
    ↓
Query AST
    ↓
Query Executor (RFC-0017)
    ↓
Data Lake / Knowledge Graph / Metrics
    ↓
JSON result
```

## 10. CLI interface

```bash
$ tradingos query "SELECT trades WHERE symbol == BTCUSDT AND r_multiple > 2 LIMIT 5"
┌──────────┬────────┬────────┬───────┬────────┬─────────┐
│ trade_id │ symbol │ dir    │ entry │ exit   │ r_mult  │
├──────────┼────────┼────────┼───────┼────────┼─────────┤
│ uuid-1   │ BTC    │ long   │ 62596 │ 62800  │ 2.4     │
│ uuid-2   │ BTC    │ short  │ 62900 │ 62650  │ 3.1     │
└──────────┴────────┴────────┴───────┴────────┴─────────┘
2 rows (4ms)
```

## 11. Vertical Slice (RFC-0000 Rule 16)

**Цепочка:** CandleClosed event → EventBus → Data Lake → TQL query → CLI result.

### Что делаем

1. Берём существующий BTC Phase3 heartbeat (уже пишется в heartbeat_btc.jsonl)
2. Конвертируем каждую строку в Data Lake event (CandleClosed)
3. Пишем в SQLite через Data Lake API
4. Запрашиваем через TQL: `SELECT events WHERE event_type == "CandleClosed" LIMIT 5`
5. Выводим в CLI

### Что НЕ делаем

- ❌ Regime detection
- ❌ Signal generation
- ❌ Trading logic
- ❌ ML / AI
- ❌ Replay
- ❌ UI

### Результат

```
$ tradingos query "SELECT events WHERE event_type == 'CandleClosed' LIMIT 3"
┌──────────────┬──────────┬────────────┬───────────┐
│ event_id     │ symbol   │ close      │ timestamp │
├──────────────┼──────────┼────────────┼───────────┤
│ uuid-1       │ BTCUSDT  │ 62595.90   │ 12:31:10  │
│ uuid-2       │ BTCUSDT  │ 62598.40   │ 12:32:11  │
│ uuid-3       │ BTCUSDT  │ 62601.20   │ 12:33:12  │
└──────────────┴──────────┴────────────┴───────────┘
3 rows (2ms)
```

### Файлы для реализации

```
tradingos/
├── core/
│   ├── data_lake/
│   │   ├── sqlite.py          # SQLiteBackend
│   │   ├── collector.py       # EventCollector
│   │   └── __init__.py
│   └── tql/
│       ├── parser.py           # TQLParser
│       ├── ast.py              # QueryAST
│       ├── executor.py         # QueryExecutor
│       └── __init__.py
├── adapters/
│   └── phase3_adapter.py      # BTC Phase3 → Data Lake bridge
└── cli/
    └── tradingos.py            # CLI entry point
```

### Верификация

```bash
# Записать данные
$ python -m tradingos adapters.phase3_adapter --once
Written 3 events to Data Lake

# Запросить
$ python -m tradingos query "SELECT events WHERE event_type == 'CandleClosed' LIMIT 3"
┌──────────┬────────┬──────────┬────────────────────┐
│ event_id │ symbol │ close    │ timestamp          │
├──────────┼────────┼──────────┼────────────────────┤
│ abc-123  │ BTCUSDT│ 62595.90 │ 2026-07-05T12:31:10│
└──────────┴────────┴──────────┴────────────────────┘
1 rows (3ms)

# Trace
$ python -m tradingos query "TRACE events/abc-123"
[12:31:10] CandleClosed BTCUSDT close=62595.90
```
