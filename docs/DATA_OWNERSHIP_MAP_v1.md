# TradingOS Data Ownership Map v1

**Дата:** 2026-07-22
**Статус:** DRAFT
**Цель:** Определить владельцев каждой сущности данных и разделить Control Plane от Data Plane.

---

## 1. Два плоскости

### Control Plane (реальное время)

```
Market
  ↓
Engine
  ↓
PIE (решения)
  ↓
Executor
  ↓
Exchange
```

**Никакая SQLite не должна быть критическим звеном.**
Если БД зависла — торги продолжаются.

### Data Plane (история)

```
Executor
  ↓
Observer
  ↓
tradingos_data.db
  ↓
Analytics / Autopsy / Dashboard / Research
```

**Вся история живет здесь.**
Если БД недоступна — история не записывается, но торги не останавливаются.

---

## 2. Ownership Map

| Сущность | Owner | Readers | Writers | Can others write? |
|----------|-------|---------|---------|-------------------|
| Position (live) | Exchange | PIE, Observer | Exchange only | NO |
| Order | Exchange | Executor | Exchange only | NO |
| Signal | Engine | PIE, Observer | Engine only | NO |
| Decision | PIE | Analytics, Executor | PIE only | NO |
| Execution | Executor | Analytics, Observer | Executor only | NO |
| MFE | Observer | Analytics, PIE | Observer only | NO |
| MAE | Observer | Analytics, PIE | Observer only | NO |
| Health | PIE | Analytics, Observer | PIE only | NO |
| Recommendation | PIE | Executor (live_assist) | PIE only | NO |
| Outcome | Observer | Analytics | Observer only | NO |
| Analytics | — | Dashboard, Research | Analytics only | NO |

---

## 3. Ownership Map (сущности)

| Сущность | Текущий Owner | Источник истины | Формат |
|----------|---------------|-----------------|--------|
| Position (live) | Exchange | BingX API `get_positions()` | JSON |
| Position (state) | Executor | `bot_state.db` | SQLite |
| Trade | Exchange | BingX API `trade_history` | JSON |
| Trade Journal | Executor | `bot_state.db.trade_journal` | SQLite |
| MFE | Observer | `tradingos_data.db.position_events` | SQLite |
| MAE | Observer | `tradingos_data.db.position_events` | SQLite |
| Health | PIE | `tradingos_data.db.position_events` | SQLite |
| Recommendation | PIE | `tradingos_data.db.live_assist_log` | SQLite |
| Outcome | Observer | `tradingos_data.db.live_assist_log` | SQLite |
| Preflight | Executor | `bingx.py` (in-memory) | dict |
| Risk State | RiskManager | `bot_state.db` (risk tables) | SQLite |

---

## 4. Кто что хранит

### Executor (`ubot_bingx`)

| Файл | Назначение | Owner? |
|------|-----------|--------|
| `bot_state.db` | Operational state (positions, risk, settings) | ✅ YES |
| `ubot_bingx.log` | Runtime logs | ✅ YES |
| `position_timeline.jsonl` | Observer timeline (BROKEN) | ❌ Should be Observer |
| `position_observations.jsonl` | Observer observations (EMPTY) | ❌ Should be Observer |

### Observer (`pie-observer`)

| Файл | Назначение | Owner? |
|------|-----------|--------|
| `tradingos_data.db.position_events` | MFE/MAE/Health snapshots | ✅ YES |
| `tradingos_data.db.live_assist_log` | Recommendations + outcomes | ✅ YES |
| `tradingos_data.db.position_summary` | Closed position summaries | ✅ YES |
| `pie_observer.log` | Runtime logs | ✅ YES |

---

## 5. Текущие нарушения Ownership

### Нарушение 1: Executor пишет в Observer-пространство

```
position_timeline.jsonl  → должен писать Observer
position_observations.jsonl → должен писать Observer
```

**Решение:** Удалить эти файлы из Executor. Observer пишет в `tradingos_data.db`.

### Нарушение 2: Observer не имеет данных об исходе сделки

```
live_assist_log.pnl_after_15m = NULL для большинства записей
```

**Решение:** Observer должен читать цену через 15/30 минут и записывать исход.

### Нарушение 3: Executor не читает Recommendation из Observer

```
Executor: нет вызова "какую позицию закрыть?"
Observer: есть рекомендация MOVE_SL_BE, но Executor ее не видит
```

**Решение:** Executor должен читать Recommendation из `tradingos_data.db` в режиме `live_assist`.

---

## 6. Текущие активные сервисы

| Сервис | Статус | Owner данных | Может удалить? |
|--------|--------|-------------|----------------|
| `ubot-bingx.service` | ACTIVE | Executor | ❌ NO |
| `pie-observer.service` | ACTIVE | Observer | ❌ NO |
| `ubot-watchdog.service` | FAILED | — | ⚠️ DISABLED |
| `ubot.service` | RESTARTING | — | ⚠️ DISABLED |

---

## 7. Рекомендации

### Сейчас (Sprint 1)

1. ✅ Зафиксировать Ownership Map
2. ❌ НЕ чинить `core/position_observer.py` (Observer уже работает в pie-observer)
3. ❌ НЕ добавлять новые функции
4. 📊 Использовать `tradingos_data.db` для анализа

### Ближайшее будущее (Sprint 2)

1. Связать Executor с Recommendation из `tradingos_data.db`
2. Убедиться, что `live_assist_log` записывает исходы (15m/30m)
3. Удалить дублирующие файлы из Executor

### Далее (Sprint 3)

1. Объявить `tradingos_data.db` единственным источником истории
2. Архивировать `bot_state.db.trade_journal` в `tradingos_data.db`
3. Удалить legacy Observer из `core/position_observer.py`

---

## 8. Правило

> **Каждая сущность — один владелец.**
> **Ни один сервис не должен писать в пространство другого.**
> **Если два сервиса считают одно и то же — один из них лишний.**
