# PHASE 4 — Money Validation Report

## Результат

| Шаг | Статус |
|-----|--------|
| Signal creation (TradingOS native) | ✅ |
| Decision JSON schema | ✅ |
| `source = tradingos_signal_engine` | ✅ |
| Executor dry-run (Guardian ALLOWED) | ✅ |
| No /opt/ubot_bingx dependency | ✅ |
| No bridge | ✅ |

## Gap: live signal generation

SignalGenerator **НЕ может генерировать сигналы без live data pipeline**:

| Компонент | Статус |
|-----------|--------|
| `signals/signal_generator.py` | ✅ код готов |
| `signals/decision_engine_v2.py` | ✅ код готов |
| `signals/feature_vector.py` | ✅ data class |
| **Live OHLCV → FeatureVector pipeline** | ❌ отсутствует |
| **Strategy loader + manager** | ❌ отсутствует |
| **HTF context collector** | ❌ отсутствует |

SignalGenerator требует FeatureVector (28 полей OHLCV + индикаторы).  
Без data pipeline он отвергает synthetic данные (final_probability=0.34 < threshold=0.55).

## Pipeline validation (synthetic Signal → Executor)

```
Manually created Signal
    ↓
decision.json {source: tradingos_signal_engine}
    ↓
Executor v0 --dry-run → Guardian ALLOWED ✅
```

## Reality Ledger создан

```
/root/tradingos/memory/reality_trades.jsonl
```

## Что нужно для live signal generation

1. **Data pipeline**: скопировать из uBot_bingx `core/datahub.py` + `features/feature_store.py` (без execution deps)
2. **ИЛИ** подключить к нашему orderbook_collector → OHLCV converter

Без этого — сигналы не генерируются.
