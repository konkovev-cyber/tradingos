# CLEAN_AUTO_EPOCH_V1 — строгий forward-observation

**Статус: RUNNING** (n=4 CLEAN на 2026-08-14 13:30Z) · **PRODUCTION НЕ МЕНЯТЬ**

## Цель
Честный ответ: имеет ли TradingOS AUTO положительное NET expectancy при
`risk_per_trade=$0.50` после F7 + T84 + T87? Не оптимизация, не поиск стратегии.

## Замороженный конфиг
risk $0.50 · lev 5x · max_pos 4 · MAX_NOTIONAL_PCT 20% · daily 1.5% · total 4%
direction/scoring/threshold/gate/TP/SL/Guardian/BE/PARTIAL/TIGHT/TRAIL/funding/π-TP/фильтры — НЕ менять.

## Классификация (immutable)
- `source`: AUTO / MANUAL / PAPER / MT4 / MT5
- `execution`: AUTO_EXECUTED / MANUAL_EXECUTED / TECHNICAL / EMERGENCY
- `eligibility`: CLEAN / EXCLUDED
- CLEAN ⇔ source=AUTO + execution=AUTO_EXECUTED + 0 вмешательств + 0 emergency/technical/phantom +
  реальный entry/exit + initial SL из Guardian OPEN-лога + fees + funding(0 ок) + UTC-консистентность
- MANUAL/shared/amount-based (SQQQ-тип) → НЕ CLEAN даже при совпадении символа
- timezone: все расчёты в UTC; локальный +3h только для отображения (регрессия в test_epoch_timezone.py)

## Долларовый риск (никогда MAE/MFE/reconstructed)
`actual_initial_risk = |entry − initial_SL| × quantity` (из Guardian OPEN detected)
`NET_R = NET / actual_initial_risk` · `CAP_LIMITED` = notional ≥ 20% equity (или lot/min-notional force)

## Запуск
```bash
python3 research/clean_epoch_v1/epoch_classifier.py   # классифицирует закрытия → epoch_v1.jsonl
python3 research/clean_epoch_v1/epoch_checkpoint.py   # метрики + вердикт → checkpoints/<n>.json
```

## Чекпоинты (каждые 5 CLEAN)
N/WR/Gross/Fees/Funding/Slippage/NET/NET-per-trade(avg+med)/NET/R(avg+med)/Expectancy/PF/
top-1&top-2 % NET / NET без top-1 / без top-2 / MaxDD / CAP_LIMITED count / manual / tech-excl /
exit split / streaks. Вердикт: INSUFFICIENT(n<20) · EARLY_*(20-29) · POSITIVE/NEGATIVE/OUTLIER_DEPENDENT/INCONCLUSIVE(≥30).

## Критерий положительного verdict (n≥30, желательно n≥50)
NET>0 · Expectancy>0 · PF>1 · NET/R>0 · не объясняется top-1/top-2 · costs не съедают gross ·
0 manual contamination · 0 attribution defects.

## Market-class сплит (T97) — обязательный контроль
- Каждая запись тегается `market_class`: CRYPTO / STOCK (STOCK-сет: AAPL/TSLA/NVDA/AMZN/MSFT/
  META/GOOGL/NFLX/AMD/INTC/SOXL/SOXS/KORU/SNXX/SPCX/TQQQ/SQQQ USDT).
- Чекпоинт печатает и сохраняет class_split (CRYPTO vs STOCK: n, NET, NET/R avg+med, WR, PF,
  fees, MFEmed, MAEmed, CAP) — сравнение классов строго на одинаковой основе.
- **Правило первой STOCK AUTO сделки:** первая настоящая CLEAN STOCK AUTO запись не просто
  добавляется в общий счётчик — она немедленно фиксируется отдельно
  `STOCK → entry → initial_risk → MFE/MAE → Guardian → exit → fees → NET` и сравнивается с
  crypto-потоком на равных метриках. Цель: к n=30 не оказаться с выборкой, не содержащей
  нужного рынка (если AUTO почти не даёт акций — это сам по себе результат: crypto-оценка
  есть, stock-оценки нет, и никаких stock-аллокаций не делать).
- ⚠️ «Акции дают лучший $/R» пока = наблюдение ручного контура (SQQQ/NVDA исключены как
  MANUAL_OR_SHARED_PATH), НЕ чистая AUTO-оценка. Сейчас STOCK n=0 в эпохе.

## Решения эпохи (таблица, binding)
| Сценарий | Действие |
|---|---|
| CRYPTO остаётся положительной | копим выборку, не масштабируем |
| CRYPTO уходит в минус | принимаем результат, не подкручиваем вход |
| Появляются STOCK AUTO | сразу CRYPTO vs STOCK на равной основе |
| STOCK заметно лучше | набираем независимую STOCK-выборку |
| Оба отрицательные | механизм без доказанного edge |
| Оба положительные | только тогда обсуждаем увеличение $/trade |

Любое изменение EMA/TP/SL/leverage/фильтров для «спасения» отрицательной серии —
запрещено (подгонка отрицательного двигателя).

## Текущий чекпоинт (n=4, 2026-08-14)
NET +$1.05 · WR 50% · PF 7.56 · NET/R med +0.52 · top-2 = 115% NET (outlier) · no_top2 −$0.16 ·
CAP_LIMITED 4/4 (реальный риск $0.13-0.49, медиана $0.35) · manual 0 · **INSUFFICIENT (n<20)**
