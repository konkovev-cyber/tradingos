# WORKING_BOT_EXIT_CLONE_DESIGN — TradingOS implementation of a proven exit principle

*Read-only research artifact. Никакого deploy. R148/P3/production не тронуты. Источник принципа: docs/WORKING_BOT_EXIT_FORENSIC.md (7 дней API, 69 ордеров, 127 fills, 49 закрытых round-trips реального бота). Replay: /tmp/rebot/wbe_replay.py на наших 34 исторических сделках.*

---

## 1. Обнаруженный принцип (что реально работает у бота)

**Pre-committed maker exit**: выходной лимит существует ДО fill входа — решение о выходе не зависит от будущего движения цены, только от заранее выбранного спреда. Прибыль бота = maker-fee обеих сторон + turnover + отсутствие предсказания. НЕ «работа без SL» (его лузеры CRCL −7.7% MAE отдали 37% gross-win) и НЕ capture-интеллект (median capture 17% — хуже нашего Guardian).

Переносимый элемент — ровно один: **reduce-only TP-лимит, выставленный в момент fill входа, по фиксированному ценовому спреду, maker-fee на выходной ноге.**

## 2. State machine (TradingOS-версия)

```
S0 ENTRY_FILLED (taker, как сейчас)
   → немедленно (в том же цикле): выставить reduce-only TP Limit
     по entry × (1 + SPREAD) [LONG] / entry × (1 − SPREAD) [SHORT]
   → SL (initial) остаётся как catastrophic — ОТЛИЧИЕ от бота,
     обязательное для наших guards
S1 HOLD (ничего не делаем: ордера стоят)
   → TP fill (trade-through)      → S3 EXIT_DONE (maker fee)
   → SL hit (double-touch: SL-first) → S3 EXIT_DONE (taker)
   → 72h timeout                  → отмена TP, закрытие по рынку
S3 EXIT_DONE
```
Никаких промежуточных состояний, partial, trail, перестановок — в этом и есть принцип.

## 3–8. Правила (все FACT из бота, кроме отмеченного)

| Правило | Значение | Статус |
|---|---|---|
| SPREAD | +3.5% цены (медианный winner-spread бота); AKE-вариант 8.5% | FACT (бот) |
| Объём TP | 100% позиции (у бота парный same-qty round-trip) | FACT (бот) |
| Partial close | отсутствует | FACT (бот) |
| Continuation | ничего не делаем — TP уже стоит; сверх-движение НЕ захватываем | FACT (бот) |
| Retracement | ничего не делаем — TP не трогается, отдаём не более (MFE−SPREAD) | FACT (бот) |
| Adverse | наш initial SL (у бота — ничего, фатально для нас; сознательное отклонение) | DEVIATION (наш guard-стек) |
| Перестановка уровней | после exit — новая сделка (не внутри позиции) | FACT (бот) |

## 9. Catastrophic protection

Наш существующий initial SL + guardian-стек без изменений. Единственная новая механика — один reduce-only лимит. Убрать SL запрещено (R148; replay бота показал −7.7%..−26% MAE без него).

## 10. Комиссии/slippage (модель replay)

- Вход taker 0.055% (как сейчас), выход **maker 0.02%** на TP-ноге, без exit-слippage (лимитный fill).
- Fill-модель консервативная: TP исполняется только при trade-through (не касании) — per MAKER_SHADOW_GATE §6.
- SL-нога: taker + 2bps, как сейчас.

## 11. Данные, необходимые TradingOS

Все уже есть: entry/SL из генома сделки, M15-бары для replay; для live-shadow — только секунды на размещение reduce-only лимита после fill (уже есть в executor).

## 12. Псевдокод

```python
# WBE_EXIT_V1 — read-only paired shadow profile
on_position_opened(pos):
    tp_px = pos.entry * (1 -/+ SPREAD)      # SPREAD = 0.035
    place_limit(pos.symbol, opposite(pos.side),
                qty = pos.qty_full,           # 100%
                price = tp_px, reduce_only = True)
    # SL не трогаем. Никаких последующих действий.

on_tp_filled | on_sl_hit | on_timeout_72h:
    log(exit_type, exit_px, fees, NET, ΔvsREAL, ΔvsP3)
```

---

## REPLAY на наших 34 сделках (одинаковая cost-модель для всех трёх профилей)

| Model | NET | med NET | TP% | SL% | top1% |
|---|---:|---:|---:|---:|---:|
| **WBE +3.5% (bot median)** | **+$1.38** | −$0.020 | 47% | 50% | 41% |
| WBE +5% | −$0.99 | −$0.254 | 32% | 65% | 0% |
| WBE +8.5% (AKE) | −$3.34 | −$0.325 | 15% | 74% | 0% |
| REAL (guardian) | +$0.15 | −$0.005 | — | — | — |
| P3 static | −$0.67 | +$0.223 | — | — | — |

Anti-cherry (WBE 3.5%): median full −0.020 / no-best +0.103 / no-both −0.020 — не одна-сделочная.

**Честные оговорки:**
1. **Параметр-хрупкость**: знак результата переворачивается между 3.5% и 5% → эффект неустойчив к главному параметру. 3.5% и 8.5% взяты из бота ДО replay (не grid-search), но это не снимает хрупкость.
2. n=34, без OOS-разбиения в этом replay.
3. Fill-модель trade-through не валидирована на наших символах (DATA GAP — тот же, что в MAKER_SHADOW_GATE §6).
4. Cost-модель этого replay унифицирована для всех профилей — отличается от ранних T32.F/G прогонов (там cost-обработка была непоследовательна между скриптами); этому прогону доверять больше.

## 9. ГОТОВ ЛИ ДИЗАЙН К SHADOW

**YES — как read-only paired profile** (второй счётчик в exit-shadow, ноль изменений production), **НО запуск — только после P3 gate n=30 и по отдельному approval** (locked roadmap R149/R150: P3 → maker → …; WBE фактически и есть maker-ветка с pre-committed формой). Без gate не запускать: параметр-хрупкость требует OOS-подтверждения на live-выборке.
