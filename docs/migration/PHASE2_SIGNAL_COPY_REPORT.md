# PHASE 2 — Signal Layer Copy Report

## Цель
Перенести Signal Intelligence слой uBot_bingx внутрь TradingOS без execution-зависимостей.

## Результат

### Скопировано (20 файлов)

```
tradingos/signals/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── signal.py          ← from /opt/ubot_bingx/models/signal.py
│   └── types.py           ← NEW (Action, Decision, Direction)
│
├── signal_types.py         ← from strategy/signal_types.py
├── signal_scoring.py       ← from strategy/signal_scoring.py
├── signal_diagnostic.py    ← from strategy/signal_diagnostic.py
├── signal_generator.py     ← from strategy/signal_generator.py
├── signal_scorer.py        ← from strategy/signal_scorer.py
├── feature_vector.py       ← from universe/feature_vector.py
├── decision_engine_v2.py   ← from core/decision_engine_v2.py
│
└── strategies/ (12 modules)
    ├── regime_engine.py
    ├── execution_engine.py
    ├── portfolio_risk.py
    ├── adaptive_learning.py
    ├── meta_risk.py
    ├── stability_guard.py
    ├── audit_layer.py
    ├── coherence_layer.py
    ├── optimizer_v8.py
    ├── stability_compression.py
    ├── execution_truth.py
    └── self_model.py
```

### Удалённые зависимости

| Было | Стало |
|------|-------|
| `from core.decision_engine import Action, Decision` | `from tradingos.signals.models.types import Action, Decision` |
| `from strategy.base import Signal, Direction` | `from tradingos.signals.models.signal import Signal` |
| `from universe.feature_vector import FeatureVector` | `from tradingos.signals.feature_vector import FeatureVector` |
| `from strategy.regime_engine import ...` | `from tradingos.signals.strategies.regime_engine import ...` |
| `from strategy.signal_types import ...` | `from tradingos.signals.signal_types import ...` |
| `from universe.htf_collector import ...` | удалён (не используется в сигнальной части) |

### НЕ скопировано

- `execution/` (весь — старый execution layer)
- `exchange/` (весь — адаптеры бирж)
- `core/lot_sizes.py` (Bybit-specific sizing)
- `core/decision_engine.py` (нужны были только Action/Decision — вынесены в types.py)
- `config.py` (будет адаптирован под TradingOS конфиг)

### Импорт тест

```
$ python -c "from tradingos.signals.signal_generator import SignalGenerator"
✅ signal_generator.py

$ python -c "from tradingos.signals.decision_engine_v2 import DecisionEngineV2"
✅ decision_engine_v2.py
```

### Архитектурный статус

```
                TradingOS

Data Collectors
       ↓
Feature Layer
       ↓
🆕 Signal Engine (20 modules, clean imports)
       ↓
Decision Queue
       ↓
Executor Hardened
       ↓
Guardian
       ↓
Bybit
```

### Файлы отчёта

- `/root/tradingos/signals/` — перенесённый Signal Layer
- `/opt/ubot_bingx/` — НЕ тронут (архив-донор)
