# RFC-0012: Plugin Manager

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0011 |
| **Blocks** | — |

## 1. Цель

Определить систему **загрузки, инициализации и управления жизненным циклом** модулей TradingOS. Plugin Manager — это оркестратор, который превращает декларативное описание модуля в работающий сервис.

## 2. Архитектура

```
                ┌────────────────────┐
                │  plugins.yaml      │
                │  enabled: [...]    │
                └────────┬───────────┘
                         │
                         ▼
                ┌────────────────────┐
                │  PluginManager     │
                └────────┬───────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   discover()      resolve_deps()      load()
        │                │                │
        ▼                ▼                ▼
   [manifests]    [topo order]    [TradingModule]
        │                │                │
        └────────────────┴────────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │   Kernel     │
                  │              │
                  │ event_bus    │
                  │ data_lake    │
                  │ schema_reg   │
                  └──────────────┘
```

## 3. Конфигурация

`config/plugins.yaml`:

```yaml
kernel:
  auto_start: true
  parallel_init: false   # sequential init для предсказуемости

enabled:
  - "regime_ai >= 1.0.0, < 2.0.0"
  - "feature_factory"
  - "edge_discovery"
  - "replay"
  - "shadow"
  - "bybit_adapter"
  - "telegram_ui"

disabled:
  - "experimental_news_ai"
  - "experimental_gpt_analyzer"

repos:
  builtin: "tradingos.modules"
  plugins: "tradingos.plugins"
  pip:
    - "tradingos-plugin-mt5"
    - "tradingos-plugin-ibkr"
```

## 4. Plugin Manager API

```python
class PluginManager:
    # === Discovery ===
    async def discover(self) -> list[ModuleManifest]: ...
    async def list_available(self) -> list[str]: ...

    # === Resolution ===
    async def resolve_dependencies(
        self, requested: list[str]
    ) -> tuple[list[ModuleManifest], list[DependencyError]]: ...

    # === Lifecycle ===
    async def load(self, name: str) -> TradingModule: ...
    async def initialize(self, name: str) -> None: ...
    async def start(self, name: str) -> None: ...
    async def stop(self, name: str) -> None: ...
    async def unload(self, name: str) -> None: ...
    async def reload(self, name: str) -> None: ...   # hot reload

    # === Introspection ===
    async def list_running(self) -> list[TradingModule]: ...
    async def get(self, name: str) -> TradingModule | None: ...
    async def status(self, name: str) -> ModuleStatus: ...
    async def health(self) -> SystemHealth: ...

    # === Dynamic ===
    async def install(self, name: str, version: str = "latest") -> None: ...
    async def enable(self, name: str) -> None: ...
    async def disable(self, name: str) -> None: ...
```

## 5. Manifest discovery

PluginManager ищет модули в 3 местах:

| Source | Где | Пример |
|---|---|---|
| **builtin** | `tradingos/{core,intelligence,decision,research,adapters,ui}/<name>/` | `tradingos.intelligence.regime_ai` |
| **plugin** | `tradingos/plugins/<name>/` | `tradingos.plugins.my_strategy` |
| **external** | pip package с `tradingos.plugins` entry point | `tradingos-plugin-mt5` |

Каждый модуль декларирует через SDK (RFC-0011) свою метаинформацию. Plugin Manager **не парсит** `manifest.yaml` руками — он берёт метаинформацию из класса модуля.

## 6. Dependency resolution

```python
# Алгоритм
def resolve_dependencies(requested: list[str]) -> Resolved:
    # 1. Парсим semver ranges
    # 2. Строим граф зависимостей
    # 3. Топологическая сортировка
    # 4. Проверка циклов → DependencyError
    # 5. Возвращаем порядок загрузки
    pass
```

Ошибки:
- `VersionNotFoundError` — нет версии, удовлетворяющей constraint
- `CircularDependencyError` — цикл в графе
- `ConflictingConstraintsError` — два модуля требуют несовместимых версий

## 7. Lifecycle orchestration

```python
async def start_all(self):
    for manifest in self._topo_order:
        module = await self.load(manifest)
        await self.initialize(module)
        await self.health_check(module)
        await self.start(module)
        self._log_emitted(ModuleStarted, module)
```

Kernel регистрирует каждый `ModuleStarted/ModuleStopped` event в Data Lake для аудита.

## 8. Hot Reload

```python
async def reload(self, name: str):
    old = self._modules[name]
    new = await self.load(name)              # load new code
    
    state = await old.state()                # save state
    await old.stop()
    await old.unload()
    
    await new.initialize()
    await new.restore(state)                 # restore state
    await new.start()
    
    self._modules[name] = new
    self._emit(ModuleReloaded, name)
```

Hot reload разрешён только при:
- Module version не меняется
- Schema не меняется
- Module не имеет активных external connections

Иначе требуется full restart TradingOS.

## 9. Failure handling

| Ошибка | Действие |
|---|---|
| `load()` fail | module → FAILED, остальные продолжают |
| `initialize()` fail | module → FAILED, retry через 30s, 3 попытки |
| `health()` fail | module → STOPPED, ERROR event |
| `on_event()` exception (1-2 раза) | module → DEGRADED, warning |
| `on_event()` exception (3+ раза) | module → FAILED, auto-recovery или unload |
| Kernel service unavailable | affected modules → DEGRADED |

## 10. Resource isolation

Каждый модуль работает в **собственном asyncio task**. Исключение в одном не валит другие.

Лимиты (через Kernel):
- `max_memory_mb` (по умолчанию 256MB)
- `max_cpu_pct` (по умолчанию 50%)
- `max_queue_size` (по умолчанию 10000)
- `max_event_rate` (events/sec, по умолчанию unlimited)

## 11. CLI

```bash
$ tradingos plugin list
regime_ai           1.2.0    RUNNING
feature_factory     2.1.0    RUNNING
edge_discovery      0.9.0    DEGRADED   # latency_ms=120
replay              1.0.0    STOPPED
bybit_adapter       1.5.0    RUNNING
telegram_ui         0.3.0    RUNNING

$ tradingos plugin install tradingos-plugin-mt5 --version 1.0.0
$ tradingos plugin enable mt5_adapter
$ tradingos plugin reload regime_ai
$ tradingos plugin disable experimental_news_ai
$ tradingos plugin health
```

## 12. Security

Plugin Manager **не имеет** прав:
- ❌ Читать файлы вне `plugins/`
- ❌ Выполнять произвольный код (только задекларированные entry points)
- ❌ Изменять kernel modules

Sandboxing (Phase B): отдельный Python process с ограничениями.

## 13. Что дальше

- RFC-0013: Kernel Lifecycle — state machine
- Реализация `core/plugin_manager.py`
- CLI `tradingos` команда `plugin`
