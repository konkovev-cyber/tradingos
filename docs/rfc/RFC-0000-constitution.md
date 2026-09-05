# RFC-0000: TradingOS Constitution

| Field | Value |
|---|---|
| **Status** | Active (highest priority) |
| **Author** | Architecture team |
| **Created** | 2026-07-05 |
| **Supersedes** | — |
| **Amends** | — |

> Это главный документ платформы. Все остальные RFC обязаны соответствовать. Изменение Конституции требует новой версии этого RFC.

## 1. Назначение

Constitution — это 15 правил, которые **нельзя нарушить** ни в одном RFC, модуле или адаптере. Если конфликт — Конституция побеждает.

## 2. Правила

### Rule 1 — RFC First
Любое изменение начинается с RFC. Никакого кода, документа, схемы или комментария в коде, которого нет в утверждённом RFC. RFC → Review → Approved → Code.

### Rule 2 — Kernel Knows Nothing About Modules
Kernel (`core/`) не импортирует ни один модуль, плагин, адаптер, intelligence-компонент или domain-specific код. Kernel знает только: события, схемы, lifecycle, health, metrics.

### Rule 3 — Modules Know Only EventBus
Модули общаются **только** через EventBus. Прямой импорт другого модуля запрещён. Исключение: kernel-модули (EventBus, Data Lake, Schema Registry) — это фундамент.

### Rule 4 — No Circular Dependencies
Запрещены циклические зависимости. Граф зависимостей — DAG. Plugin Manager разрешает зависимости топологически.

### Rule 5 — Kernel Never Imports Plugin
Kernel никогда не импортирует код из `plugins/`, `adapters/`, `intelligence/`, `decision/`, `research/`. Только наоборот.

### Rule 6 — Events Are Immutable
Все события immutable. Никто не имеет права менять опубликованное событие. Коррекция = новое событие с `parent_event_id`.

### Rule 7 — TraceID Mandatory
Каждое событие обязано содержать `trace_id`. Без trace_id событие не принимается EventBus'ом.

### Rule 8 — Every Decision Is Replayable
Любое решение должно быть воспроизводимо через Replay Engine. Если решение нельзя replay'нуть — оно недопустимо в production.

### Rule 9 — Every Trade Has Evidence
Каждая сделка (Trade) обязана иметь `evidence_ref` — набор статистических подтверждений. Без evidence — trade в shadow-only режиме.

### Rule 10 — Every Edge Has Statistical Proof
Любой Edge (Knowledge Object типа `edge`) обязан иметь статистическое подтверждение: sample_size >= 100, p-value <= 0.05, evidence_ref.

### Rule 11 — Every Module Passes Health Check
Любой модуль обязан реализовать `health()` по RFC-0007 и проходить его при `start()`. FAIL → модуль не запускается.

### Rule 12 — Every API Has Schema Version
Любой публичный API, event payload, Knowledge Object имеет `schema_version` (SemVer). Breaking change → major bump + новая сущность.

### Rule 13 — No Business Logic In Storage
Data Lake, Knowledge Graph, Knowledge Files — не принимают решений и не содержат бизнес-логики. Только хранение и запросы.

### Rule 14 — All Data Through Data Lake
Все значимые данные (events, snapshots, knowledge state, evidence) проходят через Data Lake. Прямая запись в файлы/БД в обход — запрещена.

### Rule 15 — Kernel Is The Only Extension Point
Единственная точка расширения TradingOS — kernel API + EventBus. Не plugins, не monkey-patching, не import hooks.

### Rule 16 — Every RFC Ends With a Vertical Slice
Каждый RFC должен завершаться **рабочей вертикалью** — минимальной полностью функционирующей цепочкой от события до результата. Без этого RFC не утверждается.

Формат вертикали: `event → processing → storage → query → result`. Без ML, без AI, без Replay. Просто путь «событие пришло → система показала результат».

## 3. Иерархия документов

```
1. RFC-0000 Constitution     ← этот документ (нельзя нарушать)
2. RFC-0001..0099 Kernel     ← определяют kernel
3. RFC-0100..0999 Intelligence
4. RFC-1000..1999 Adapters
5. RFC-2000..2999 UI
6. RFC-9000..9999 Commercial / Licensing
```

При конфликте: меньший номер побеждает.

## 4. Amendment process

Изменение Конституции требует:
1. Новый RFC с тегом `amends: RFC-0000`
2. Минимум 2 RFC в статусе Approved от любых других категорий, ссылающихся на это правило
3. 7 дней обсуждения
4. Одобрение архитектурного совета

Без выполнения всех 4 пунктов amendment считается отклонённым.

## 5. Enforcement

Нарушение Конституции = автоматический CI failure:
- Прямой импорт модуля в kernel → `lint:constitution` rule
- Отсутствие trace_id в событии → `eventbus:validate` runtime check
- Отсутствие evidence у trade → `risk:evidence_check` runtime check
- Circular dependency → `plugin:dep_check` runtime check

## 6. Принципы (production-ready extensions)

Эти правила — не soft preferences, а hard constraints. Они проверены годами эксплуатации аналогичных систем (Kubernetes, Linux Kernel, Erlang/OTP, Apache Kafka).

| Правило | Аналог |
|---|---|
| Rule 2, 3, 5 | Erlang OTP, microkernel design |
| Rule 4, 15 | Kubernetes API, Unix philosophy |
| Rule 6, 7, 12 | Event sourcing, Apache Kafka schema registry |
| Rule 8 | Erlang/OTP, deterministic systems |
| Rule 9, 10 | Scientific method, hypothesis testing |
| Rule 11, 14 | Twelve-Factor App, observability |

## 7. Что дальше

- RFC-0011: Module SDK (реализация Rule 1, 3, 4, 5, 11)
- RFC-0012: Plugin Manager (реализация Rule 4, 5, 15)
- RFC-0013: Kernel Lifecycle (реализация Rule 11)
- RFC-0014: Research Data Lake (реализация Rule 14)
- RFC-0015: Knowledge Graph (реализация Rule 13)
