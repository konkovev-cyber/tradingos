# RFC-0001: TradingOS Vision

| Field | Value |
|---|---|
| **Status** | Draft |
| **Author** | Architecture team |
| **Created** | 2026-07-05 |
| **Supersedes** | — |
| **Related** | RFC-0002..0009 |

## 1. Problem Statement

Сегодня проект состоит из двух почти независимых кодовых баз:
- `/root/trading_brain_v4` — Python crypto pipeline
- `/root/mt5_trading_bot` — Python MT5 pipeline + MQL5 TradingOS

Обе решают одни и те же задачи: market data ingestion, regime detection, signal generation, decision routing, risk gates, position tracking, shadow validation, replay. Дублируется:
- EventBus (две реализации)
- PersistentEventStore (JSONL в обоих)
- StateStore (JSON atomic)
- Domain types (Position, Trade, Signal — определены по-разному)
- Risk pipeline (два kill-switch, разные уровни)
- Shadow/observation (три варианта)
- Snapshot pipeline (два)

При этом каждая новая фича (regime v2, edge discovery, knowledge graph) приносит дилемму: в какой кодовой базе жить и как избежать ещё одного дубликата.

## 2. Goals

TradingOS — это **операционная система для алгоритмической торговли**, а не ещё один бот.

### 2.1 Конституционные принципы

1. **One Core, Many Adapters.** Ядро платформы ничего не знает о MT5, Bybit или Binance. Адаптеры — единственное место, где живёт биржевая специфика.
2. **Event-first.** Любой факт — это событие. Любая связь между фактами — это trace_id. Никаких скрытых RPC.
3. **Schema-Versioned.** Каждая сущность и каждое событие имеет версию схемы. v2 не ломает v1.
4. **Reconstructible.** Полное состояние системы можно восстановить из Data Lake + Knowledge Graph + snapshot.
5. **Discoverable.** Любой edge, regime, anomaly, signal, decision — это сущность в Knowledge Graph, по которой можно делать запросы.
6. **Domain-Over-Infrastructure.** Сначала модель предметной области, потом хранилище, потом UI.
7. **No-Logic-In-Storage.** Data Lake не решает, что хорошо. Только хранит и отдаёт.
8. **No-Silent-Evolution.** Изменение любой доменной сущности требует новой версии и migration plan.

### 2.2 Не-цели (явно)

- Не заменяем существующие боты в моменте. Они становятся **клиентами** TradingOS.
- Не ломаем Phase 3 shadow процесс на BTC — он продолжает работать.
- Не пишем новый EventBus — переиспользуем существующий.
- Не заменяем MT5/Bybit — адаптируем их.

## 3. Архитектура (высокоуровнево)

```
                    ┌──────────────────────────────────┐
                    │            TradingOS             │
                    └──────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
   ┌────▼─────┐              ┌──────▼──────┐              ┌─────▼─────┐
   │  Core    │              │Intelligence │              │  Adapters │
   │          │              │             │              │           │
   │ EventBus │              │ RegimeAI    │              │ Bybit     │
   │ DataLake │              │ EdgeDiscov. │              │ Binance   │
   │ Knowledge│◄─────────────│ FeatureFact.│─────────────►│ MT5       │
   │  Graph   │              │ AnomalyDet. │              │ BingX     │
   │ QueryEng.│              │ Correlation │              │ IBKR      │
   │ SchemaReg│              │             │              │           │
   │ PluginMgr│              │             │              │           │
   └────┬─────┘              └──────┬──────┘              └─────┬─────┘
        │                           │                           │
        │                    ┌──────▼──────┐                    │
        │                    │  Decision   │                    │
        │                    │             │                    │
        │                    │ DecisionEng.│                    │
        │                    │ PortfolioAI │                    │
        │                    │ RiskEngine  │                    │
        │                    │ ExecOptimzr │                    │
        │                    └──────┬──────┘                    │
        │                           │                           │
        │                    ┌──────▼──────┐                    │
        └────────────────────┤  Research   ├────────────────────┘
                             │             │
                             │ Replay      │
                             │ Shadow      │
                             │ Evidence    │
                             │ Backtest    │
                             └─────────────┘
```

## 4. Roadmap (RFCs)

| ID | Title | Цель |
|:---|:---|:---|
| RFC-0001 | Vision (этот документ) | Определить границы, принципы, roadmap |
| RFC-0002 | Core Domain Model | Сущности + lifecycle + relations (Конституция) |
| RFC-0003 | Event Schema | Версионируемая схема всех событий |
| RFC-0004 | Module Contract | Обязательная структура и интерфейс любого модуля |
| RFC-0005 | Plugin Manager | Загрузка/выгрузка модулей без изменения ядра |
| RFC-0006 | Module Lifecycle | DISCOVERED → ... → STOPPED |
| RFC-0007 | Health Protocol | Унифицированный health-check |
| RFC-0008 | Metrics Standard | Внутренний стандарт метрик |
| RFC-0009 | Knowledge Objects | Граф знаний вместо сырых сущностей |
| RFC-0010 | Research Data Lake | Центральное хранилище с pluggable backends |
| RFC-0011 | Knowledge Graph | Связи между сущностями + query API |
| RFC-0012 | Replay Engine | Детерминированная среда воспроизведения |
| RFC-0013 | Evidence Engine | Доказательства статистического преимущества |
| RFC-0014 | Learning Engine | Auto-update model weights и signal filters |
| RFC-0015 | Evolution Engine | Замена устаревших edges новыми |

## 5. Migration Path

### Phase A — Подселение (текущая фаза)
- TradingOS поставляется как **библиотека + thin adapters**
- Существующие боты (`trading_brain_v4`, `mt5_trading_bot`) остаются работать
- Они пишут свои события в TradingOS Data Lake через адаптер
- TradingOS пока ничего не решает — только собирает данные

### Phase B — Подмена
- Новый код пишется только в TradingOS
- Адаптеры постепенно мигрируют на новые domain types
- Backwards-compatibility через schema versioning

### Phase C — Консолидация
- TradingOS становится единственным местом для новых стратегий
- Старые боты замораживаются или становятся read-only клиентами

## 6. Definition of Done (DoD)

Модуль считается готовым только если:
- ✅ Получает события через EventBus
- ✅ Валидирует схему через SchemaRegistry
- ✅ Поддерживает trace_id end-to-end
- ✅ Сохраняет в Data Lake
- ✅ Имеет Query API
- ✅ Покрыт тестами
- ✅ Имеет минимальный viewer
