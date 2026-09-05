# Live Failure Prediction Map

## Назначение
Где система скорее всего сломается в реальности.
Какие сценарии наиболее вероятны и как к ним готовиться.

## Tier 1: Вероятность 80-95% (почти наверняка произойдёт)

### 1. Slippage surprise
**Что:** Реальное проскальзывание будет хуже чем в paper
**Где:** Т5 → T6 цепочка
**Почему:** Paper модель не учитывает рыночный импакт
**Митигация:** T6 Slippage Guardian с порогом 2x от paper

### 2. Latency spike при волатильности
**Что:** Задержка вырастет при сильном движении
**Где:** Live Execution → T6
**Почему:** Биржа замедляется при高的 нагрузке
**Митигация:** Circuit Breaker на latency > 5000ms

### 3. Regime mismatch
**Что:** Режим в live будет отличаться от paper
**Где:** T3 Features → T4 Signals → T5 Policy
**Почему:** Исторические данные ≠ live данные
**Митигация:** T7 learning freeze на первых 50 сделках

## Tier 2: Вероятность 50-80% (вероятно произойдёт)

### 4. Governor false positive
**Что:** Governor сработает в нормальных условиях
**Где:** Governor → T5
**Почему:** Пороги настроены на paper, не на live
**Митигация:** Постепенная калибровка порогов

### 5. Execution drift accumulation
**Что:** T6 drift будет慢慢的 расти
**Где:** T6 → T7 → Stability
**Почему:** Мелкие расхождения копятся
**Митигация:** Hourly reconciliation, auto-rollback при drift > 0.3

### 6. Correlation regime shift
**Что:** Корреляции изменятся при стрессе
**Где:** Portfolio Intelligence
**Почему:** Корреляции стабильны в normal, ломаются в crisis
**Митигация:** Portfolio Governor с dynamic correlation threshold

## Tier 3: Вероятность 20-50% (возможно произойдёт)

### 7. Partial fill handling failure
**Что:** Система неправильно обработает partial fill
**Где:** Live Execution → T6
**Почему:** Paper не генерирует partial fills
**Митигация:** Тестирование с fault injection перед live

### 8. Exchange API error cascade
**Что:** Ошибки API приведут к каскадным проблемам
**Где:** Live Execution → Hardening
**Почему:** Биржа может вернуть unexpected responses
**Митигация:** Circuit Breaker + retry с backoff

### 9. T7 learning instability
**Что:** T7 начнёт "плыть" в live
**Где:** T7 → Stability
**Почему:** Live data noisy, paper data clean
**Митигация:** T7 freeze на первых 50 сделках

## Tier 4: Вероятность 5-20% (маловероятно, но критично)

### 10. Full system freeze
**Что:** Система полностью остановится
**Где:** Hardening → GracefulShutdown
**Почему:** Непредвиденная комбинация факторов
**Митигация:** CrashRecovery + systemd watchdog

### 11. Portfolio allocation collapse
**Что:** Все активы получат 0% allocation
**Где:** Portfolio Intelligence → Capital Allocator
**Почему:** Все активы в NO_TRADE regime одновременно
**Митигация:** Minimum allocation floor (5% per active asset)

### 12. Data corruption
**Что:** Data Lake повредится
**Где:** T2 → Data Lake
**Почему:** Disk full, power loss, concurrent writes
**Митигация:** WAL mode, atomic writes, backup rotation

## Предсказание по фазам

### Фаза 1 (Trades 1-25): Tier 1 проблемы
Ожидаемо: slippage surprise, latency spikes
Действие: калибровка порогов

### Фаза 2 (Trades 26-50): Tier 2 проблемы
Ожидаемо: Governor false positives, execution drift
Действие: стабилизация поведения

### Фаза 3 (Trades 51-100): Tier 3 проблемы
Ожидаемо: partial fills, API errors
Действие: fault tolerance verification

### Фаза 4 (100+ trades): Tier 4 проблемы
Ожидаемо: system freeze, data corruption
Действие: production hardening verification
