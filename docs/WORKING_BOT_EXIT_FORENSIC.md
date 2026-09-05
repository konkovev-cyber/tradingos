# WORKING BOT EXIT FORENSIC — Reverse Engineering Result

*Read-only API forensic, 7 дней истории (69 orders, 127 fills, 8 символов, 49 закрытых round-trips). Никаких write-операций. TradingOS / P3 / R148 не тронуты. Credentials не сохранены (env only, удалены из процессов).*

---

## EXECUTIVE VERDICT

```
МЕХАНИЗМ:           Двусторонний MAKER LIMIT GRID (market-making), НЕ exit-intelligence.
                    Resting BUY limit ниже цены + paired SELL limit выше на фикс. спред,
                    одинаковый qty вход/выход. AKEUSDT: спред ровно +8.5% × 9 повторов.
ДЕНЬГИ ОТКУДА:      maker fees обеих сторон ($0.53 на $27.59 NET) + turnover (49 закрытий/7д)
                    + pre-committed exit (лимит выхода существует ДО fill входа).
DOWNNSIDE:          SL отсутствует алгоритмически (0 trigger-ордеров в истории).
                    CRCL shorts ×10: MAE −3.9%..−7.7%, −$10.2 = 37% gross-win отдано обратно.
                    USUSDT: MAE −26%. «Защита» = малый notional + спред субсидирует лузеров.
CAPTURE:            медиана 17% — ХУЖЕ нашего Guardian (~43% на AMZN-примере).
ПЕРЕНОС В TradingOS: НЕ «работа без SL» (одна CRCL-потеря пробьёт наш daily-guard $0.92).
                    Переносим только ПРИНЦИП: pre-committed maker exit ladder →
                    усиливает существующую maker-ветку (MAKER_SHADOW_GATE.md, stage 2 post-P3).
ВЕРДИКТ:            EXIT MECHANISM NOT PROVEN (нет exit-edge).
                    COST/FEE MECHANISM PROVEN (реальная система, реальные maker fills 60%).
```

---

## 1. POSITION LIFECYCLE (18 lifecycle, 49 closed legs)

Типичный winner (AKEUSDT, чистейший паттерн, 9 повторов):

```
resting BUY limit @ 0.008703 (maker, fill на откате)
→ PAIRED SELL limit @ 0.009439 уже стоит (+8.5%, тот же qty 4900)
→ цена возвращается вверх → SELL fill (maker)
→ round trip +8.5% gross, обе ноги maker, hold 0.1–1.3h
→ бот переставляет новую пару уровней и повторяет
```

Типичный loser (CRCLUSDT, 10 закрытий):

```
SELL grid @ 70.68–71.04 (усреднение вверх)
→ цена идёт ПРОТИВ (вверх)
→ закрытия по progressively худшим лимитам: 70.97 → 71.13 → 71.70 → 71.74 → 72.40 → 74.38 → 75.37
→ MAE −3.9%..−7.7%, суммарно −$10.2, hold 32–138h
→ НИКАКОГО стопа, НИКАКОГО reverse — просто закрытие, когда grid-уровень цены наконец достижим
```

## 2. LIMIT LADDER STRUCTURE

| Параметр | Значение | Статус |
|---|---|---|
| Уровней входа | 1–4 (усреднение против движения, BSP: 38.91→38.55→38.33→38.16) | FACT |
| Уровней выхода | 1 на пару (тот же qty) + ступеньки вверх при повторах | FACT |
| Спред winner-выхода | медиана +3.5%, AKE фикс +8.5% | FACT |
| Qty вход/выход | одинаковый (парный round-trip), НЕ частичная лестница | FACT |
| Перестановка уровней | да — после каждого round-trip новая пара (createdTime/updatedTime открытых ордеров это подтверждают) | FACT |
| Зависимость от volatility | не наблюдаема из доступных данных | UNKNOWN |
| reduceOnly | часть exit-ордеров true (QNTX, LITE), часть false (grid-уровни) | FACT |

## 3. ORDER REPLACEMENT LOGIC

- FACT: после fill пары бот ставит новую пару лимитов (9 открытых ордеров на 4 позиции — живые grid-уровни).
- FACT: 46/127 fills имеют orderType=UNKNOWN (ордера старше 7д окна истории) — полная история перестановок недоступна.
- DATA GAP: точный триггер перестановки уровня (по времени или по событию) — не восстановим из 7-дневного окна.

## 4. ЭКОНОМИКА (49 закрытий, 7 дней)

| Метрика | Значение |
|---|---|
| NET | **+$27.59** |
| fees | $0.53 (76/127 maker = 60%) |
| wins / losses | 28 (+$42.41) / 21 (−$14.82) |
| median capture (MFE→realized) | **17%** |
| median MFE / MAE | +5.64% / −3.92% |
| hold winners / losers | 9.2h / 48.0h |
| Крупнейший источник убытка | CRCL shorts ×10 = −$10.2 (69% всех потерь) |

## 5. ПОЧЕМУ ОН РАБОТАЕТ БЕЗ SL (ответ на главный вопрос)

FACT-цепочка:
1. Входы maker (fee ~0.01–0.04/сделка против наших $2.00 на 34 сделки).
2. Выход pre-committed: лимит стоит ДО fill входа → exit-решение не требует предсказания.
3. Позиции малы ($40–140 notional) при 20x — множественные одновременные.
4. Проигрыши НЕ ограничены — они субсидируются спред-прибылями и закрываются когда цена ДОБРА доп. +5–8% (или нет: USUSDT MAE −26% закрыт в −$1.96).

→ **На наших guard'ах (daily 1.5% = $0.92) этот механизм НЕ ВЫЖИВЕТ**: одна CRCL-потеря (−$2.64) пробивает дневной лимит почти 3 раза.

## 6. ПСЕВДОКОД (только FACT-часть)

```
FOR each symbol in grid_universe:
    place BUY limit  @ bid_offset(spread_down)
    place SELL limit @ last_buy_fill × (1 + target_spread)   # same qty, exists BEFORE entry fills
    on BUY fill:   ensure paired SELL limit exists (maker)
    on SELL fill:  place new BUY limit below
    on price moving against entry:
        add grid BUY levels lower (averaging)   # BSP ×4
        NO stop loss                            # FACT: 0 triggers in history
    on losing side:
        close at progressively further limit    # CRCL pattern
        OR hold (USUSDT −26% MAE)
```
UNKNOWN: spread_down выбор, критерий перевыставления, символ-селекция, размер grid-уровня.

## 7. ЧТО ПЕРЕНОСИМО В TradingOS

| Элемент | Перенос? | Причина |
|---|---|---|
| Работа без SL | **НЕТ** | fatal на наших guards; MAE −7.7%/−26% неприемлемы |
| Averaging против движения | **НЕТ** | martingale-класс, R148 запрещает |
| **Pre-committed maker exit** | **ДА** (post-P3 gate) | exit-лимит до fill входа: убирает предсказание + maker fee. Усиливает MAKER_SHADOW_GATE stage 2 |
| Фикс. спред ~+3.5% TP | НЕТ как замена | capture 17% хуже нашего; но как maker-форма существующего TP — тестируемо в shadow |
| Двусторонний grid | НЕТ | это другая стратегия (market-making), не наш контур |

## 8. ГОТОВ ЛИ ДИЗАЙН К SHADOW

**NO.** Отдельного нового exit-engine из этого бота нет — его прибыль это fee-структура + turnover, не выход. Единственное действие: после P3-gate вернуться к maker forensic (уже задокументирован в MAKER_SHADOW_GATE.md §14 вариант A) с новым фактом: **реальная система доказывает, что resting-limit maker fills достижимы на 60%+ на наших типах символов**.

## Data gaps
- Полная история перестановок ордеров >7д (orderType=UNKNOWN на 46 fills)
- Equity/размер счёта бота (не запрашивали wallet-balance — не нужно для exit-механизма)
- Критерий выбора символов и размеров grid-уровней
