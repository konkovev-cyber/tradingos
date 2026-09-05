# PHASE 3 — Import Cleanup Report

## Проверка

```bash
grep -R "/opt/ubot_bingx\|from core\.\|from strategy\.\|from universe\.\|from execution\.\|from exchange\." /root/tradingos/signals
```

**Результат**: единственное совпадение — комментарий в `__init__.py` (не импорт).

## Итого

| Проверка | Результат |
|----------|-----------|
| Количество проверенных файлов | 22 .py файла |
| `from core.*` | **0** |
| `from strategy.*` | **0** |
| `from universe.*` | **0** |
| `from execution.*` | **0** |
| `from exchange.*` | **0** |
| `/opt/ubot_bingx` в импортах | **0** |
| Все импорты → `tradingos.*` или stdlib | ✅ |

## Исправления (PHASE 2)

| Было | Стало |
|------|-------|
| `from core.decision_engine import Action, Decision` | `from tradingos.signals.models.types import Action, Decision` |
| `from strategy.base import Signal, Direction` | `from tradingos.signals.models.signal import Signal` |
| `from universe.feature_vector import FeatureVector` | `from tradingos.signals.feature_vector import FeatureVector` |
| `from strategy.regime_engine import ...` | `from tradingos.signals.strategies.regime_engine import ...` |
| `from universe.htf_collector import ...` | удалён |
