# TradingOS Decision Gate

## CURRENT PHASE

GUARDIAN VALIDATION SPRINT

Status: ACTIVE

Mode: OBSERVATION + SHADOW ONLY

---

## Main Question

Может ли Guardian увеличить реальный PnL существующей системы без увеличения риска?

---

## Allowed Actions

✅ анализировать сделки
✅ проверять качество данных
✅ улучшать отчеты
✅ искать технические ошибки
✅ улучшать наблюдаемость
✅ проводить shadow simulation

---

## Forbidden Until Validation

❌ новые стратегии
❌ новые индикаторы
❌ смена биржи
❌ изменение Entry логики
❌ изменение Risk Management
❌ включение автоматического Guardian
❌ MOVE_SL_BE execution

---

## Activation Conditions

MOVE_SL_BE может быть рассмотрен только после:

- [ ] минимум 30 закрытых сделок
- [ ] корректные MFE/MAE данные
- [ ] Guardian Report создан
- [ ] сравнение: NO_GUARDIAN vs LOCK vs TRAIL
- [ ] решение человека

---

## If Agent Wants To Build Something New

Сначала ответить:
1. Какая доказанная проблема?
2. Почему текущая система её не решает?
3. Какие данные подтверждают проблему?
4. Как измерим улучшение?

Если нет ответа: НЕ СТРОИТЬ.

---

## Allowed Quick Tasks (без изменения логики)

1. Guardian Readiness Check — SL/TP sync, reconciliation, data quality
2. Profit Capture Dashboard — визуализация MFE/giveback по открытым позициям
3. Failure Mode Review — список что может сломать систему

---

## Key Principle

Если нет достаточных данных — говори "недостаточно данных".
Если идея красивая, но не доказанная — отклоняй.
Если есть риск потерять капитал — останавливай.
