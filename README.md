# TradingOS

> Операционная система для алгоритмической торговли. Не бот — платформа.

## Архитектура

```
TradingOS
├── Kernel          # ядро: EventBus, Data Lake, Plugin Manager, Health, Metrics
├── Modules         # всё, что не в ядре
│   ├── Intelligence    # Regime AI, Edge Discovery, Feature Factory, ...
│   ├── Decision        # Decision Engine, Portfolio AI, Risk Engine, ...
│   ├── Research        # Replay, Shadow, Evidence, Backtest, Statistics
│   ├── Adapters        # Bybit, Binance, MT5, BingX, IBKR
│   └── UI              # Telegram, Web, CLI
└── Plugins         # пользовательские модули
```

## Принцип

> **Ни один Python-класс не появляется, пока его нет в RFC.**
> Цепочка: RFC → Review → Approved → Code.

## Двухуровневая модель (Kernel vs Modules)

**Kernel (`core/`):**
- EventBus
- Data Lake
- Plugin Manager
- Schema Registry
- Health Protocol
- Metrics Standard
- Knowledge Graph
- Config
- Logger

**Modules (`{intelligence,decision,research,adapters,ui}/`):**
- Каждый модуль реализует `TradingModule` ABC (RFC-0004)
- Каждый модуль имеет `manifest.yaml`
- Каждый модуль проходит load → init → start → stop → unload

## Статус

**Stage 0 — Foundation (RFC Phase).** Код не пишется до утверждения Kernel RFC.

## Roadmap (RFC)

### Конституция
| ID | Документ | Статус |
|---|---|---|
| RFC-0000 | [Constitution](docs/rfc/RFC-0000-constitution.md) | ✅ |
| RFC-0001 | [Vision](docs/rfc/RFC-0001-vision.md) | ✅ |
| RFC-0002 | [Core Domain Model](docs/rfc/RFC-0002-domain-model.md) | ✅ |
| RFC-0003 | [Event Schema](docs/rfc/RFC-0003-event-schema.md) | ✅ |

### Kernel (Линия А)
| ID | Документ | Статус |
|---|---|---|
| RFC-0011 | [Module SDK](docs/rfc/RFC-0011-module-sdk.md) | ✅ |
| RFC-0012 | [Plugin Manager](docs/rfc/RFC-0012-plugin-manager.md) | ✅ |
| RFC-0013 | [Kernel Lifecycle](docs/rfc/RFC-0013-kernel-lifecycle.md) | ✅ |
| RFC-0014 | [Research Data Lake](docs/rfc/RFC-0014-research-data-lake.md) | ✅ |

### Intelligence (Линия Б — после Data Lake)
| ID | Документ | Статус |
|---|---|---|
| RFC-0015 | [Knowledge Graph](docs/rfc/RFC-0015-knowledge-graph.md) | ✅ |
| RFC-0016 | Query Engine | ⏳ |
| RFC-0017 | Timeline Engine | ⏳ |
| RFC-0018 | Evidence Engine | ⏳ |
| RFC-0019 | Learning Engine | ⏳ |
| RFC-0020 | Evolution Engine | ⏳ |

**Линия А (Kernel)** завершена. **Линия Б (Intelligence)** — следующая фаза.

## Структура

```
tradingos/
├── core/                 # Kernel
│   ├── event_bus/
│   ├── data_lake/
│   ├── plugin_manager/
│   ├── schema_registry/
│   ├── health/
│   ├── metrics/
│   ├── knowledge_graph/
│   ├── config/
│   └── logger/
├── intelligence/         # Intelligence modules
│   ├── regime_ai/
│   ├── edge_discovery/
│   ├── feature_factory/
│   ├── anomaly_detector/
│   └── correlation_engine/
├── decision/             # Decision modules
│   ├── decision_engine/
│   ├── portfolio_ai/
│   ├── risk_engine/
│   └── execution_optimizer/
├── research/             # Research modules
│   ├── replay/
│   ├── shadow/
│   ├── statistics/
│   ├── backtest/
│   └── evidence/
├── adapters/             # Exchange adapters
│   ├── bybit/
│   ├── binance/
│   ├── bingx/
│   └── mt5/
├── storage/              # Storage backends
│   ├── sqlite/
│   ├── parquet/
│   └── clickhouse/
├── api/                  # API layer
│   ├── grpc/
│   ├── rest/
│   └── websocket/
├── ui/                   # UI
│   ├── telegram/
│   └── web/
├── plugins/              # User plugins
├── tests/                # Tests
├── docs/
│   └── rfc/              # RFC documents
└── scripts/              # Utility scripts
```

## Текущие боты (становятся клиентами)

- `/root/trading_brain_v4` — Python crypto pipeline
- `/root/mt5_trading_bot` — Python MT5 pipeline + MQL5

Оба остаются работать. Они подключаются к TradingOS через thin adapters, не импортируя ядро напрямую.

## PIE v1.1 — Position Intelligence Engine

**Режим observe** — наблюдает за позициями, анализирует, записывает события. Не управляет.

```bash
# Запуск A/B наблюдения с PIE
python3 observe_crypto.py --symbol BTCUSDT --timeframe 5m

# С PIE в режиме observe (по умолчанию)
python3 observe_crypto.py --pie-mode observe

# Отчёт по собранным данным
python3 scripts/pie_report.py
python3 scripts/pie_report.py --last 50
```

### События позиций
- `ENTRY` — позиция открыта
- `PROFIT_STARTED` — позиция впервые в профите
- `NEW_MFE` — новый максимум прибыли
- `PROFIT_DECAY` — прибыль откатилась от пика
- `RISK_WARNING` — порог риска достигнут
- `THESIS_BROKEN` — тезис сделки нарушен
- `EXIT` — позиция закрыта

### MFE/MAE Память
- `max_profit_seen` — максимальная прибыль
- `max_loss_seen` — максимальный убыток
- `profit_retracement_pct` — % отданной прибыли от пика
- `time_to_first_profit` — время до первого профита
- `health_score` — состояние позиции (0-100)

## Следующие шаги

### Первый Vertical Slice ✅ (работает)
```bash
# Ingest Phase3 BTC events → Data Lake
python3 cli/tradingos.py ingest

# Query via TQL
python3 cli/tradingos.py query "SELECT events WHERE event_type == 'CycleCompleted' LIMIT 3"
python3 cli/tradingos.py query "SELECT events WHERE event_type == 'RegimeDetected' LIMIT 5"
python3 cli/tradingos.py stats
```

### RFC-0017 Query Engine — следующий
После TQL RFC-0016 и рабочего vertical slice, следующий шаг — RFC-0017 (полноценный Query Engine с цепочкой по сущностям RFC-0002). Тогда:
- `SELECT trades WHERE r_multiple > 2.0` — читает реальные сделки
- `TRACE trades/uuid-1` — восстанавливает полную цепочку
- `TIMELINE trades/uuid-1` — показывает хронологию
