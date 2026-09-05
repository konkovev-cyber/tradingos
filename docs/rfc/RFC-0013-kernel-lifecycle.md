# RFC-0013: Kernel Lifecycle

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0011, RFC-0012 |
| **Blocks** | — |

## 1. Цель

Определить **жизненный цикл Kernel и его компонентов** как целого. Если Plugin Manager управляет lifecycle модулей, то Kernel Lifecycle управляет lifecycle всей платформы.

## 2. Kernel state machine (по аналогии с Kubernetes)

```
BOOTING
   ↓ Загрузка config
STARTING
   ↓ Подключение к DB, schema registry init
INITIALIZING
   ↓ Регистрация встроенных модулей
DISCOVERING_PLUGINS
   ↓ Сканирование plugins/
RESOLVING_DEPENDENCIES
   ↓ Топосорт, валидация
STARTING_MODULES
   ↓ По одному (или батчами)
HEALTH_CHECK
   ↓ Все модули прошли health()
RUNNING
   ↓ Нормальная работа
   ↕  reload модуля → ModuleReloaded
DEGRADED
   ↑ Один или несколько модулей degraded
DRAINING
   ↓ SIGTERM получен
STOPPING
   ↓ Остановка модулей в обратном порядке
UNLOADING
   ↓ Закрытие соединений
SHUTDOWN_COMPLETE
```

## 3. Kernel startup sequence

```python
class Kernel:
    async def boot(self):
        self._set_state(BOOTING)
        await self._load_config()              # 1. config/
        
        self._set_state(STARTING)
        await self._init_logging()            # 2. logger
        await self._init_event_bus()           # 3. event_bus (kernel service)
        await self._init_data_lake()           # 4. data_lake (kernel service)
        await self._init_schema_registry()     # 5. schema_registry
        
        self._set_state(INITIALIZING)
        await self._register_kernel_modules()  # 6. event_bus, data_lake, etc.
        
        self._set_state(DISCOVERING_PLUGINS)
        manifests = await self._plugin_manager.discover()
        
        self._set_state(RESOLVING_DEPENDENCIES)
        order, errors = await self._plugin_manager.resolve_dependencies(
            self._config.plugins.enabled
        )
        if errors:
            await self._abort_startup(errors)
        
        self._set_state(STARTING_MODULES)
        for manifest in order:
            try:
                await self._plugin_manager.load(manifest)
                await self._plugin_manager.initialize(manifest)
                await self._plugin_manager.health_check(manifest)
                await self._plugin_manager.start(manifest)
            except Exception as e:
                await self._handle_module_failure(manifest, e)
        
        self._set_state(HEALTH_CHECK)
        health = await self._plugin_manager.health()
        if health.overall_score < 0.5:
            await self._abort_startup(health)
        
        self._set_state(RUNNING)
        self._emit(KernelStarted)
        self._start_health_loop()              # periodic health checks
        self._start_metrics_loop()             # periodic metrics emit
```

## 4. Kernel shutdown sequence

```python
async def shutdown(self, signal: int):
    self._set_state(DRAINING)
    
    # Перестать принимать новые события
    await self.event_bus.pause()
    
    self._set_state(STOPPING)
    
    # Остановить модули в обратном порядке
    for manifest in reversed(self._start_order):
        try:
            await self._plugin_manager.stop(manifest)
        except Exception as e:
            self._log.error(f"Failed to stop {manifest.name}: {e}")
    
    self._set_state(UNLOADING)
    
    # Закрыть kernel services
    await self._plugin_manager.unload_all()
    await self._data_lake.close()
    await self._event_bus.close()
    
    self._set_state(SHUTDOWN_COMPLETE)
    self._emit(KernelStopped)
```

## 5. Health monitoring loop

```python
async def _start_health_loop(self):
    self._health_task = asyncio.create_task(self._health_loop())

async def _health_loop(self):
    while self._state in (RUNNING, DEGRADED):
        health = await self._plugin_manager.health()
        await self._data_lake.write(ModuleHealth, health)
        
        if health.overall_score < 0.8:
            self._set_state(DEGRADED)
        else:
            if self._state == DEGRADED:
                self._set_state(RUNNING)
        
        await asyncio.sleep(30)
```

## 6. Module failure handling

```python
async def _handle_module_failure(self, manifest, error):
    self._log.error(f"Module {manifest.name} failed: {error}")
    await self._data_lake.write(ModuleFailed, {
        "module": manifest.name,
        "error": str(error)
    })
    
    # Зависимые модули тоже останавливаются
    dependents = self._get_dependents(manifest.name)
    for dep in dependents:
        await self._plugin_manager.stop(dep)
    
    # Если kernel-critical модуль упал — переход Kernel в DEGRADED
    if manifest.is_critical:
        self._set_state(DEGRADED)
```

## 7. Сигналы

Kernel обрабатывает:

| Сигнал | Действие |
|---|---|
| `SIGTERM` | graceful shutdown (drain → stop → unload) |
| `SIGINT` | same as SIGTERM |
| `SIGHUP` | reload config (без рестарта модулей) |
| `SIGUSR1` | dump state to log |
| `SIGUSR2` | force health check |

## 8. Kernel health (aggregated)

```json
{
  "kernel_state": "running",
  "uptime_seconds": 3600,
  "overall_score": 0.92,
  "modules_total": 12,
  "modules_ok": 11,
  "modules_degraded": 1,
  "modules_failed": 0,
  "modules_stopped": 0,
  "kernel_services": {
    "event_bus": {"status": "ok", "queue_size": 0},
    "data_lake": {"status": "ok", "writes_per_sec": 145, "latency_ms": 1.2},
    "schema_registry": {"status": "ok", "schemas_loaded": 47}
  },
  "system": {
    "memory_mb": 1024,
    "cpu_pct": 18.5,
    "open_files": 234
  }
}
```

## 9. Restart policy

| Событие | Действие |
|---|---|
| Module упал (не critical) | Auto-recovery 3 раза, потом остаётся STOPPED |
| Module упал (critical) | Kernel DEGRADED, алерт |
| Data Lake недоступен | Kernel SHUTDOWN (нельзя работать) |
| Event Bus упал | Kernel SHUTDOWN |
| Schema Registry упал | Kernel DEGRADED (можно работать, но без schema validation) |

## 10. Что дальше

- RFC-0014: Research Data Lake
- Реализация `core/kernel.py` с этим state machine
- CLI `tradingos start` / `tradingos stop` / `tradingos status`
