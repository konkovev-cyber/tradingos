# RFC-0011: Module SDK

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0000, RFC-0002, RFC-0003 |
| **Blocks** | RFC-0012, RFC-0013 |

## 1. Цель

Дать разработчику модуля **минимальный, приятный, полный** SDK. Чтобы можно было написать новый модуль за 5 минут, не читая исходный код TradingOS.

**«Hello World» модуль должен быть 30 строк.**

## 2. Принципы SDK

1. **Declarative > Imperative.** Разработчик объявляет что модуль делает, а не как.
2. **Conventions > Configuration.** Разумные defaults; конфиг — только когда нужно.
3. **Strong typing.** Все события, payload'ы, config — type-checked.
4. **One base class, no magic.** `TradingModule` — единственный базовый класс. Без metaclass хаков.
5. **Async-native.** Полная поддержка `async/await`.
6. **Hot-reloadable.** SDK поддерживает reload без потери state.

## 3. Минимальный модуль (Hello World)

```python
# modules/echo/module.py
from tradingos.sdk import TradingModule, event, payload
from dataclasses import dataclass


@dataclass
class EchoConfig:
    prefix: str = "[ECHO]"


class EchoModule(TradingModule):
    name = "echo"
    version = "1.0.0"
    subscribes_to = ["CandleClosed"]

    config: EchoConfig = EchoConfig()

    @event("EchoReceived")
    async def on_candle(self, candle: dict) -> dict:
        return {
            "echo": f"{self.config.prefix} {candle['symbol']} @ {candle['close']}"
        }
```

**Всё.** Kernel сам:
- загрузит модуль;
- подпишется на `CandleClosed`;
- вызовет `on_candle` при получении;
- сериализует результат в envelope (RFC-0003);
- опубликует `EchoReceived` event;
- зарегистрирует Schema;
- подключит Health/Metrics/Logging.

## 4. Базовый класс `TradingModule`

```python
# tradingos/sdk/module.py
from abc import ABC
from typing import Any, ClassVar
from dataclasses import dataclass
import semver


class TradingModule(ABC):
    # === Identity (обязательно) ===
    name: ClassVar[str]
    version: ClassVar[str]  # semver

    # === Capabilities (обязательно, но с defaults) ===
    subscribes_to: ClassVar[list[str]] = []
    publishes: ClassVar[list[str]] = []

    depends_on: ClassVar[list[str]] = []  # semver ranges
    config_schema: ClassVar[type | None] = None

    # === Runtime state (НЕ переопределять) ===
    kernel: "KernelAPI"
    logger: "ModuleLogger"
    metrics: "MetricsCollector"
    health: "HealthChecker"

    # === Public API для модулей (Kernel вызывает) ===
    async def on_event(self, event: dict) -> None:
        """Routing в @event-decorated handlers."""
        ...

    async def publish(self, event_type: str, payload: dict) -> None:
        """Публикация события в EventBus."""
        ...

    # === Hot-reload hooks (опционально) ===
    async def on_load(self) -> None: ...
    async def on_init(self) -> None: ...
    async def on_start(self) -> None: ...
    async def on_stop(self) -> None: ...
    async def on_unload(self) -> None: ...
    async def on_reload(self) -> None: ...
```

## 5. Декораторы SDK

### 5.1 `@event(event_type)` — handler для подписки

```python
@event("CandleClosed")
async def on_candle(self, candle: dict) -> dict | None:
    """payload-аргумент — это payload (без envelope).
    Возврат — payload нового события (если None — событие не публикуется)."""
    ...
```

### 5.2 `@config` — типизированный конфиг

```python
from dataclasses import dataclass

@config
@dataclass
class MyConfig:
    lookback: int = 100
    threshold: float = 0.5
    symbols: list[str] = field(default_factory=list)
```

Kernel автоматически:
- читает `config/<module_name>.yaml`;
- валидирует против схемы;
- инжектит в `self.config`.

### 5.3 `@health_check(name)` — добавить кастомный health check

```python
@health_check("bybit_api_reachable")
async def check_bybit(self) -> HealthResult:
    ok = await self.bybit.ping()
    return HealthResult(status="ok" if ok else "error", latency_ms=42)
```

### 5.4 `@metric(name, type)` — добавить кастомную метрику

```python
@metric("patterns_found_total", "counter")
def patterns_found(self): ...
```

### 5.5 `@periodic(seconds)` — фоновый таск

```python
@periodic(seconds=60)
async def cleanup_old_data(self):
    await self.data_lake.cleanup(retention_days=7)
```

## 6. Payload-типизация (через dataclasses / pydantic)

```python
from dataclasses import dataclass

@dataclass
class CandleClosed:
    market_id: str
    timeframe: str
    open_ts: str
    close_ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float

@event("CandleClosed")
async def on_candle(self, candle: CandleClosed) -> dict:
    # candle.market_id, candle.close, etc. — type-safe
    ...
```

## 7. Kernel API (что доступно модулю)

```python
# В self.kernel:
async def publish(self, event_type: str, payload: dict) -> None: ...
async def subscribe(self, event_type: str) -> None: ...  # динамическая подписка
async def get_state(self) -> dict: ...
async def set_state(self, state: dict) -> None: ...
async def call_kernel(self, method: str, **kwargs): ...  # kernel services

# В self.logger:
self.logger.info("...")
self.logger.warning("...")
self.logger.error("...")

# В self.metrics:
await self.metrics.counter("events_total", 1)
await self.metrics.gauge("queue_size", 14)
await self.metrics.histogram("latency_ms", 12.5)

# В self.health:
await self.health.check("custom_check", ok=True, value=42)

# В self.data_lake:
async def data_lake.write(event): ...
async def data_lake.query(...): ...
```

## 8. Lifecycle (автоматический)

Kernel вызывает в порядке:

1. `__init_subclass__()` — SDK проверяет обязательные атрибуты
2. `__init__()` — конструктор модуля
3. `on_load()` — read config, validate
4. `on_init()` — connect to deps
5. `health()` — pass check
6. `on_start()` — start loops
7. **runtime: on_event()** routing
8. `on_stop()` — drain queues
9. `on_unload()` — close connections

SDK гарантирует, что `kernel`, `logger`, `metrics`, `health` доступны **до** `on_load()`.

## 9. Auto-generated manifest

SDK генерирует `manifest.yaml` из декларативного описания:

```python
class MyModule(TradingModule):
    name = "my_module"
    version = "1.0.0"
    subscribes_to = ["CandleClosed"]
    publishes = ["MySignal"]
    depends_on = ["event_bus >= 2.0.0"]
```

→

```yaml
# Автогенерируется при build
name: my_module
version: 1.0.0
entry_point: "my_module.module:MyModule"
subscribes: [CandleClosed]
publishes: [MySignal]
depends_on: [event_bus >= 2.0.0]
```

## 10. Тестирование модулей

SDK даёт утилиты для тестов:

```python
# tests/test_my_module.py
from tradingos.sdk.testing import ModuleTestHarness

async def test_my_module():
    harness = ModuleTestHarness(MyModule)
    
    # Отправить fake event
    response = await harness.send("CandleClosed", {
        "market_id": "uuid", "timeframe": "5m", "close": 100
    })
    
    # Проверить emitted events
    assert "MySignal" in [e.event_type for e in harness.emitted]
    
    # Проверить metrics
    assert harness.metrics.gauges["queue_size"] == 0
```

## 11. Что НЕ входит в SDK

- ❌ Сетевой код (HTTP/WS) — это в Kernel
- ❌ DB-доступ напрямую — только через Data Lake
- ❌ Multiprocessing/threading — Kernel сам управляет concurrency
- ❌ Глобальные переменные — Kernel запрещает

## 12. Структура модуля с SDK

```
modules/<name>/
    module.py        # единственный обязательный файл
    config.yaml      # опционально
    schemas.py       # опционально (если есть свои типы payload'ов)
    tests/
    README.md
```

(Упрощено по сравнению с RFC-0004 благодаря SDK.)

## 13. Пример полного модуля

```python
# modules/whale_detector/module.py
from tradingos.sdk import TradingModule, event, config, metric
from dataclasses import dataclass, field


@config
@dataclass
class WhaleConfig:
    min_size_usd: float = 1_000_000
    lookback_bars: int = 20


class WhaleDetector(TradingModule):
    name = "whale_detector"
    version = "1.0.0"

    subscribes_to = ["OrderBookUpdated"]
    publishes = ["WhaleOrderDetected"]

    depends_on = ["event_bus >= 2.0.0", "data_lake >= 1.0.0"]

    config: WhaleConfig = WhaleConfig()

    @metric("whales_detected_total", "counter")
    def whales_detected(self): ...

    async def on_init(self):
        self._notional_history = []

    @event("OrderBookUpdated")
    async def on_orderbook(self, ob: dict) -> dict | None:
        top_bid = ob["bids"][0]
        notional = top_bid[0] * top_bid[1]
        self._notional_history.append(notional)

        if len(self._notional_history) > self.config.lookback_bars:
            self._notional_history.pop(0)

        if notional < self.config.min_size_usd:
            return None

        avg = sum(self._notional_history) / len(self._notional_history)
        if notional > avg * 5:
            return {
                "side": "bid",
                "price": top_bid[0],
                "size": top_bid[1],
                "notional_usd": notional,
                "avg_notional_usd": avg,
                "spike_ratio": notional / avg
            }
        return None
```

**Этот модуль:**
- Загружается через Plugin Manager
- Подписывается на `OrderBookUpdated`
- Публикует `WhaleOrderDetected` при аномалии
- Ведёт счётчик `whales_detected_total`
- Имеет конфиг через YAML
- Имеет health-check, metrics, logging
- Hot-reloadable

**Без единой строки boilerplate.**

## 14. Что дальше

- RFC-0012: Plugin Manager — загрузка этих модулей
- RFC-0013: Kernel Lifecycle — state machine в Kernel
- RFC-0014: Research Data Lake — где модули хранят данные
- Реализация `tradingos/sdk/` в коде
