# PROFIT PROTECTION: MOVE_SL_BE v1

**Дата:** 2026-07-22
**Статус:** DESIGN
**Цель:** Соединить PIE рекомендации с исполнением на BingX.

---

## 1. Проблема

```
Autopsy v2:
- 84% позиций достигали прибыли
- 68% теряли прибыль
- PIE рекомендовал MOVE_SL_BE 844 раза
- Исполнено: 0 раз

Результат:
- Real PnL: -24.87%
- Simulated PnL (с защитой): -4.86%
- Потенциальное улучшение: +20%
```

---

## 2. Решение

Соединить PIE → Executor для действия MOVE_SL_BE.

---

## 3. Правило

```
ЕСЛИ:
  PnL >= +0.3%
  MFE >= +0.3%
  Health >= 60
  Возраст позиции >= 5 минут
  SL не был перенесен ранее

ТО:
  Перевести SL на уровень входа (Break Even)

ИНАЧЕ:
  HOLD
```

---

## 4. Три обязательных проверки перед LIVE

### 4.1 Trigger Precision

Не использовать только PnL. Нужен комбинированный триггер:

| Параметр | Минимум | Источник |
|----------|---------|----------|
| PnL | +0.3% | `position_events.pnl_pct` |
| MFE | +0.3% | `position_events.max_profit_seen` |
| Health | 60 | `position_events.health_score` |
| Age | 5 min | `position_events.time_in_position` |
| SL not moved | true | `position state` |

### 4.2 Idempotency

Каждая позиция должна иметь флаг:

```
be_applied: true/false
be_price: float (уровень BE)
be_applied_at: timestamp
```

При повторном вызове:
- Если `be_applied == true` → SKIP
- Если `be_applied == false` → EXECUTE

### 4.3 BingX Mechanics

При изменении SL через `set_trading_stop`:

```
✅ SL меняется
✅ TP остаётся
❌ НЕ отменять TP
❌ НЕ менять positionSide
❌ НЕ менять reduceOnly
```

Проверка:
1. Вызвать `get_positions` → проверить текущий SL/TP
2. Вызвать `set_trading_stop` с новым SL
3. Еще раз `get_positions` → убедиться, что TP не изменился

---

## 5. Архитектура исполнения

### Phase 1.2a: Logging Only (1 день)

```
PIE рекомендует MOVE_SL_BE
  ↓
Executor логирует:
  PIE_ACTION_CANDIDATE
  symbol, old_sl, new_sl, reason
  ↓
Ничего не делает с позицией
```

### Phase 1.2b: Execution (после проверки)

```
PIE рекомендует MOVE_SL_BE
  ↓
Risk Gate: проверяет правила
  ↓
Executor: вызывает set_trading_stop
  ↓
Verification: проверяет результат
  ↓
Journal: записывает действие
```

---

## 6. Risk Gate

Перед исполнением проверить:

| Проверка | Условие | Действие |
|----------|---------|----------|
| SL уже перенесен | `be_applied == true` | SKIP |
| PnL недостаточен | `pnl < 0.3%` | SKIP |
| Позиция слишком свежая | `age < 5 min` | SKIP |
| Health низкий | `health < 60` | SKIP |
| Рынок вolatile | `atr > 3%` | SKIP |
| Другой ордер в работе | `pending_order` | SKIP |

---

## 7. Journal Entry

После каждого действия записать:

```json
{
  "action": "MOVE_SL_BE",
  "symbol": "BTC-USDT",
  "old_sl": 65900,
  "new_sl": 66020,
  "entry_price": 66000,
  "pnl_at_action": 0.45,
  "mfe_at_action": 0.82,
  "health_at_action": 71,
  "result": "SUCCESS/FAILED/SKIPPED",
  "reason": "PIE_MOVE_SL_BE"
}
```

---

## 8. Метрики успеха

После 20-30 сделок с MOVE_SL_BE:

| Метрика | Без защиты | С защитой | Цель |
|---------|-----------|-----------|------|
| Avg winner | ? | ? | >= |
| Avg loser | ? | ? | <= |
| Profit retention | ? | ? | >= |
| PF | ? | ? | >= |
| Max DD | ? | ? | <= |

---

## 9. Заморозки

❌ Новые стратегии
❌ Mean Reversion
❌ Усреднение
❌ TAKE_PARTIAL (Phase 2)
❌ TRAIL (Phase 3)

---

## 10. Следующий шаг

Phase 1.2a: добавить логирование `PIE_ACTION_CANDIDATE` в Executor.
Без исполнения. 1 день сбора данных.
