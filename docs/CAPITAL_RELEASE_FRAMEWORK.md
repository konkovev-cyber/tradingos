# Capital Release Framework

## Назначение
Точная формула сколько денег и когда можно добавлять в систему.

## Фундаментальное правило

> **Capital release is a function of demonstrated stability, not time.**

## Формула释放

```text
Max_Capital = Base × Stability_Multiplier × Performance_Multiplier × Time_Multiplier
```

### 1. Base (базовый размер)
- MICRO: 10 USDT
- SMALL: 100 USDT
- NORMAL: 1000 USDT
- FULL: определяется внешними ограничениями

### 2. Stability_Multiplier (0.0 - 1.0)
```
IF governor_orange_count == 0 AND t6_drift_rate < 0.10 → 1.0
IF governor_orange_count <= 2 AND t6_drift_rate < 0.20 → 0.7
IF governor_orange_count <= 5 OR t6_drift_rate < 0.30 → 0.4
ELSE → 0.0 (no capital release)
```

### 3. Performance_Multiplier (0.0 - 1.0)
```
IF win_rate > 0.55 AND sharpe > 1.5 → 1.0
IF win_rate > 0.45 AND sharpe > 1.0 → 0.7
IF win_rate > 0.40 AND sharpe > 0.5 → 0.4
ELSE → 0.0
```

### 4. Time_Multiplier (0.0 - 1.0)
```
IF trades >= 100 AND days >= 30 → 1.0
IF trades >= 50 AND days >= 14 → 0.7
IF trades >= 25 AND days >= 7 → 0.4
ELSE → 0.0
```

## Примеры расчёта

### Пример 1: Система на 50 сделках, 14 дней
- Win rate: 52%, Sharpe: 1.2
- Governor orange: 1
- T6 drift: 15%
- Max_Capital = 1000 × 0.7 × 0.7 × 0.7 = **343 USDT**

### Пример 2: Система на 100 сделках, 30 дней
- Win rate: 58%, Sharpe: 1.8
- Governor orange: 0
- T6 drift: 5%
- Max_Capital = 1000 × 1.0 × 1.0 × 1.0 = **1000 USDT**

### Пример 3: Система нестабильна
- Win rate: 38%, Sharpe: 0.3
- Governor orange: 4
- T6 drift: 35%
- Max_Capital = 1000 × 0.0 × 0.0 × 0.4 = **0 USDT**

## Scaling ladder (порядок увеличения)

```
Stage 0: PAPER          (0 USDT)
    ↓ gate: 25 trades, 7 days, no crashes
Stage 1: MICRO          (10-50 USDT)
    ↓ gate: 50 trades, 14 days, stable metrics
Stage 2: SMALL          (100-300 USDT)
    ↓ gate: 100 trades, 30 days, performance targets
Stage 3: NORMAL         (500-2000 USDT)
    ↓ gate: 200 trades, 60 days, portfolio stable
Stage 4: FULL SCALE     (рассчитывается по формуле)
```

## Anti-regression rules

### При каждом увеличении капитала:
1. Governor thresholds НЕ меняются
2. T7 learning冻结 на 48 часов
3. T6 reconciliation frequency = каждый час (вместо каждые 6 часов)
4. SL/TP guardian = каждый ордер (вместо sampling)

### При каждом уменьшении капитала:
1. Автоматический rollback к предыдущей стадии
2. T7 learning unfreeze
3. Human review обязателен

## Stop-loss для системы (не для сделки)

### Автоматическая остановка системы:
```
IF system_drawdown > 5% → downgrade to previous stage
IF system_drawdown > 10% → flatten all, freeze, human review
IF 3 consecutive downgrades → full stop, architectural review
```
