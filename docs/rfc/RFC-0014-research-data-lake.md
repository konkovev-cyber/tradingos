# RFC-0014: Research Data Lake

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0011, RFC-0013 |
| **Blocks** | RFC-0015 (Knowledge Graph) |

## 1. Цель

Определить **физическое хранилище** всех событий TradingOS. Data Lake — это **append-only журнал**, через который проходят все значимые факты системы. Никакой бизнес-логики, только хранение и запросы.

## 2. Архитектура

```
       Module.publish(event)
              │
              ▼
    ┌─────────────────────┐
    │   DataLake.collect  │  ← валидация, envelope, dedup
    └─────────┬───────────┘
              │
       ┌──────┴──────┐
       ▼             ▼
  HotStore       WarmStore
  (SQLite)       (Parquet)
       │             │
       └──────┬──────┘
              ▼
    ┌─────────────────────┐
    │   Query API         │  ← universal interface
    └─────────────────────┘
              │
       ┌──────┴──────┐
       ▼             ▼
    Module        Web Viewer
    queries       (Timeline, Graph, Filters)
```

## 3. Публичный API (kernel service)

```python
class DataLakeService:
    # === Write ===
    async def write(self, event: dict) -> UUID: ...
    async def write_batch(self, events: list[dict]) -> list[UUID]: ...
    
    # === Read by id ===
    async def get_by_id(self, event_id: UUID) -> dict | None: ...
    
    # === Read by trace ===
    async def get_trace(self, trace_id: UUID) -> list[dict]: ...
    async def get_trace_summary(self, trace_id: UUID) -> dict: ...
    
    # === Read by filters ===
    async def query(
        self,
        event_types: list[str] | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        source_module: str | None = None,
        trace_id: UUID | None = None,
        correlation_id: UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
        order: str = "asc"
    ) -> list[dict]: ...
    
    # === Aggregations ===
    async def count(self, **filters) -> int: ...
    async def timeline(
        self, event_type: str, bucket: str = "1h", since: datetime | None = None
    ) -> list[TimeBucket]: ...
    async def stats(self, event_type: str, field: str) -> dict: ...
    
    # === Subscriptions (live) ===
    async def subscribe(
        self, event_types: list[str]
    ) -> AsyncIterator[dict]: ...
```

## 4. Event envelope (повтор RFC-0003)

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

## 5. HotStore schema (SQLite v1)

```sql
CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    timestamp       TEXT NOT NULL,        -- ISO 8601 UTC
    source_module   TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    correlation_id  TEXT,
    parent_event_id TEXT,
    symbol          TEXT,
    timeframe       TEXT,
    severity        TEXT NOT NULL,
    payload         TEXT NOT NULL,        -- JSON
    ingested_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_events_trace        ON events(trace_id);
CREATE INDEX idx_events_correlation  ON events(correlation_id);
CREATE INDEX idx_events_symbol_ts    ON events(symbol, timestamp);
CREATE INDEX idx_events_type_ts      ON events(event_type, timestamp);
CREATE INDEX idx_events_source_ts    ON events(source_module, timestamp);
CREATE INDEX idx_events_timestamp    ON events(timestamp);
```

WAL mode обязателен.

## 6. WarmStore (Parquet, Phase B)

```
warm/
  year=2026/
    month=07/
      day=05/
        hour=12/
          events_20260705T1200.parquet
          events_20260705T1201.parquet
```

Partitioned by time, columnar compression. Read через PyArrow.

## 7. ColdStore (Phase C)

Тот же Parquet, gz-сжатие, перенос на slow storage (S3 Glacier, etc).

## 8. Retention policy

| Tier | Retention | Backend |
|---|---|---|
| Hot | 7 days | SQLite |
| Warm | 90 days | Parquet |
| Cold | indefinite | Parquet gz |

Auto-promotion: каждый час данные старше 7 дней переносятся Hot → Warm.

## 9. Pluggable backends

```python
class StorageBackend(ABC):
    @abstractmethod
    async def write(self, event: dict) -> bool: ...
    @abstractmethod
    async def write_batch(self, events: list[dict]) -> int: ...
    @abstractmethod
    async def get_by_id(self, event_id: UUID) -> dict | None: ...
    @abstractmethod
    async def query(self, filter: QueryFilter) -> list[dict]: ...
    @abstractmethod
    async def count(self, filter: QueryFilter) -> int: ...
    @abstractmethod
    async def close(self) -> None: ...
```

Реализации:
- `SQLiteBackend` (MVP)
- `ParquetBackend` (Phase B)
- `PostgresBackend` (Phase C)
- `ClickHouseBackend` (Phase D)

## 10. SchemaRegistry integration

```python
async def write(self, event: dict):
    # 1. Validate
    if not self.schema_registry.validate(
        event["event_type"], 
        event["schema_version"], 
        event["payload"]
    ):
        raise SchemaValidationError(...)
    
    # 2. Fill defaults
    if "event_id" not in event:
        event["event_id"] = uuid4()
    if "timestamp" not in event:
        event["timestamp"] = datetime.utcnow().isoformat()
    
    # 3. Persist
    await self.hot.write(event)
    
    # 4. Async promote (background)
    asyncio.create_task(self._maybe_promote_to_warm(event))
```

## 11. Performance targets

- Write throughput: ≥ 5000 events/sec на single thread
- Read by trace_id: < 10ms (для trace с <100 events)
- Read by range (1 day): < 100ms
- Storage: ~500B per event → 1M events = ~500MB

## 12. Idempotency

`event_id` — primary key. Повторная запись того же `event_id` — no-op (не ошибка, не дубликат).

## 13. Что НЕ делает Data Lake

- ❌ Не принимает решений
- ❌ Не вычисляет агрегаты автоматически (только по запросу)
- ❌ Не содержит бизнес-логику
- ❌ Не удаляет события (только retention policy)
- ❌ Не модифицирует payload

## 14. Что входит в MVP

- SQLiteBackend
- Hot retention 7 дней
- API: write, get_by_id, get_trace, query, count
- SchemaRegistry integration
- CLI: `tradingos-lake write` / `query`
- Адаптер BTC Phase3 → Data Lake (E2E proof)

## 15. Что дальше

- RFC-0015: Knowledge Graph (построен поверх Data Lake)
- Реализация `core/data_lake/sqlite.py`
- Web Viewer: timeline по trace_id
