# grid_v3_pro Audit — задача для следующей сессии

**Mode:** R106 (use, don't build) + Senior MQL5 Quant Developer
**Цель:** убедиться, что реальный EA выполняет именно ту логику, которая дала backtest PF=1.55
**Запрещено:** создавать новую стратегию, менять идею grid_v3_pro, оптимизировать параметры

## Context

- Strategy: grid_v3_pro
- Logic: XAUUSD H4, ATR>0.5, ADX>25, risk 1%, MaxLot=0.01, MaxDD=7%, MaxMargin=65%
- Backtest: 669 сделок, 7 мес, PF=1.55
- Current: EA запущен на demo, сделок мало/нет, требуется диагностика

## Stage 1 — Аудит кода

Проверить:

1. **OnInit:** успешно ли создаются индикаторы? Ошибки CopyBuffer? Параметры?
2. **OnTick:** вызывается ли логика? Правильно ли определяется новый H4 бар?
3. **ATR:** период? buffer? символ? timeframe?
4. **ADX:** период? линии ADX/+DI/-DI? Совпадает с backtest?
5. **Сигнал входа:** полностью сравнить с правилами grid_v3_pro
6. **Исполнение:** OrderSend ошибки, volume, SL/TP, ограничения брокера, min lot, digits/point

## Stage 2 — Добавить диагностику (DEBUG_LOG)

Каждый H4 бар писать:
```
TIME:    YYYY-MM-DD HH:MM
ATR:     <value>
ADX:     <value>
Signal:  BUY|SELL|NONE
Reason:  filters_failed|max_spread|spread|...
Position count: N
Margin:  %
Risk check: PASS/FAIL
Decision: ENTER|SKIP
```

Пример:
```
2026.07.21 20:00
ATR=0.82  ADX=31  SIGNAL=SELL  BLOCKED=MaxSpread
2026.07.21 00:00
ATR=0.31  ADX=18  SIGNAL=NONE  Reason=filters_failed
```

## Stage 3 — Исправления (только подтверждённые проблемы)

Каждое изменение оформить:
```
BUG:        ...
Причина:    ...
Изменение:  ...
Риск:       ...
Проверка:   ...
```

## Stage 4 — Validation

1. Компиляция без ошибок
2. EA запускается
3. Все индикаторы работают
4. Логи показывают причины решений
5. Поведение соответствует backtest

## Final report

```
GRID_V3_PRO AUDIT
STATUS: PASS / BUG FIXED / BACKTEST MISMATCH
Найдено: N
Исправлено: N
Осталось: ...
Следующее действие: ...
```

## Три возможных сценария

- **A — всё нормально:** EA просто редко торгует. Ждём статистику.
- **B — ошибка реализации:** исправляем, повторяем.
- **C — backtest не соответствует реальности:** STOP, Edge Cemetery.

## Pipeline state (2026-07-21)
- grid_v3_pro: FORWARD (active, awaiting diagnostic)
- Funding Rate BTC: INVESTMENT_MEMO (GO conditional, awaiting grid_v3_pro result)
- New candidates: BLOCKED (R105, R106)

## Rules active
- R105: один кандидат на forward
- R106: использовать, не строить
- R107: отделять факты от выводов

---

## Exit Criteria

Аудит считается завершённым только если:

1. Известно: почему EA открыл сделку ИЛИ почему EA не открыл сделку.
2. Реальная логика совпадает с backtest.
3. Все блокировки имеют причину в логах.
4. Есть решение:
   - **PASS:** Forward продолжается.
   - **BUG:** исправление + повторная проверка.
   - **BACKTEST MISMATCH:** стратегия отправляется на пересмотр.

---

## Разделение изменений

### Запрещено менять (это уже новая стратегия):

❌ ATR 0.5 → 0.7
❌ ADX 25 → 30
❌ Таймфрейм
❌ Правила входа
❌ Логику сетки
❌ Выходы

### Разрешено исправлять (баги реализации):

✅ Ошибки MQL5
✅ Неправильное чтение индикаторов
✅ Несоответствие backtest/live
✅ Ошибки расчёта лота
✅ Ошибки исполнения ордеров
✅ Отсутствие логирования

---

## Главный вопрос следующей сессии

**Когда рынок дал условия grid_v3_pro, EA сделал то же самое, что делал в тестере — да или нет?**

Не "сделать прибыльнее". А "сделать идентичным backtest'у".
