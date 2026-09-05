# First 100 Trades Protocol

## Назначение
Протокол безопасного прохождения первых 100 реальных сделок.
Любая система ломается на первых 10 сделках. Этот протокол предотвращает это.

## Правила (абсолютные)

### Rule 1: Размер сделки
- Максимум 5 USDT на сделку (0.5% от капитала 1000 USDT)
- Max 2 позиции одновременно
- Только BTCUSDT и ETHUSDT

### Rule 2: Никаких изменений
- Запрещено менять веса (T7 freeze)
- Запрещено менять пороги (Governor thresholds)
- Запрещено менять strategy mode thresholds
- Единственное исключение: crash recovery

### Rule 3: Логирование
- Каждая сделка → Data Lake
- Каждый intent → T6 ledger
- Каждый drift → audit trail
- Каждый heartbeat → health monitor

### Rule 4: Остановочные условия (AUTOMATIC STOP)
```
IF consecutive_losses >= 3 → STOP, human review
IF daily_loss > 2% → STOP, human review
IF governor_level == ORANGE → STOP, human review
IF t6_drift_rate > 0.3 → STOP, human review
IF execution_errors > 5 → STOP, human review
```

### Rule 5: Анализ на 25/50/75/100 сделках
- На каждой границе: полный аудит
- Сравнение: backtest vs live
- Решение: продолжить / остановить / калибровать

## Метрики для отслеживания

| Метрика | Цель | Стоп |
|---|---|---|
| Win rate | > 45% | < 35% |
| Avg PnL | > 0 | < -0.5% |
| Max drawdown | < 3% | > 5% |
| T6 drift rate | < 10% | > 25% |
| Governor ORANGE | 0 | 2+ |
| Execution errors | 0 | 3+ |
| Slippage avg | < 5 bps | > 15 bps |

## Phase transitions

### Phase A: Trades 1-25 (Bootstrap)
- Размер: 5 USDT max
- Позиции: 1 одновременно
- Мониторинг: непрерывный
- Цель: проверить что система работает

### Phase B: Trades 26-50 (Calibration)
- Размер: 5 USDT max
- Позиции: 2 одновременно
- Мониторинг: hourly
- Цель: стабилизировать поведение

### Phase C: Trades 51-75 (Validation)
- Размер: 10 USDT max
- Позиции: 2 одновременно
- Мониторинг: hourly
- Цель: подтвердить статистику

### Phase D: Trades 76-100 (Confirmation)
- Размер: 10 USDT max
- Позиции: 2 одновременно
- Мониторинг: hourly
- Цель: финальное подтверждение

## Анализ на 100 сделках

### Отчёт включает:
1. Win/loss distribution
2. Regime breakdown (TREND vs RANGE vs IDLE)
3. T6 drift frequency
4. Slippage distribution
5. Governor activations
6. T7 weight changes
7. Correlation stability
8. Execution error log

### Решение:
- CONTINUE: metrics в target → масштабировать
- CALIBRATE: metrics близки к target → настроить пороги
- STOP: metrics далеко от target → остановить, разобраться

## Файлы

```
tradingos/
├── docs/
│   └── FIRST_100_TRADES_PROTOCOL.md  ← этот документ
├── core/
│   └── live/
│       ├── protocol.py               # протокол + проверки
│       └── tracker.py                # трекинг сделок
└── cli/
    └── tradingos.py                  # команды протокола
```
