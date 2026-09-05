# PRECOMMITTED_LIMIT_EXIT — SHADOW DESIGN

*Read-only paired shadow profile. Production FROZEN, R148 unchanged, P3 live-shadow продолжается. Основа: reverse engineering рабочего бота (`docs/WORKING_BOT_EXIT_FORENSIC.md`) + PCE forensic на 34 сделках (`docs/PCE_VERDICT.md`) + HYB_B live-shadow backfill n=7 (FALSIFIED on live data).*

---

## 1. Что делает рабочий бот — FACTS

Из reverse engineering 7-дневной истории (49 закрытых round-trips, 127 fills, 8 символов):
- Resting BUY limit ниже цены + paired SELL limit выше на фиксированный спред
- Спред winner-выхода: медиана **+3.5%**, AKEUSDT ровно +8.5% × 9 повторов
- 60% fills — maker (fee ~0.02% vs 0.055% taker)
- Qty вход/выход парный same-qty (НЕ частичная лестница)
- Усреднение против движения (BSP ×4 уровня)
- 0 trigger/SL ордеров в истории — работает только на grid-levels

## 2. Какой exit mechanism можно скопировать

Переносим ТОЛЬКО принцип pre-committed reduce-only Limit, НЕ grid/averaging:
- В момент fill входа → place reduce-only limit на противоположной стороне
- Если цена достигает → забрать прибыль (maker fee)
- Если цена не достигает → catastrophic SL остаётся как защита
- НЕ пытаться предсказать разворот

## 3. Сводная таблица historical replay (n=34, единая cost-модель)

| R | NET | med | Δ vs REAL | TP fills | top1% | Verdict |
|---|---:|---:|---:|---:|---:|---|
| 0.50R | -$1.07 | -$0.02 | -$1.23 | 22/34 | 0% | REJECT |
| 0.75R | -$0.71 | +$0.15 | -$0.87 | 19/34 | 0% | REJECT |
| 0.90R | +$0.30 | +$0.18 | +$0.15 | 19/34 | **148%** | CONDITIONAL (FRAGILE) |
| 1.00R | +$0.59 | +$0.15 | +$0.44 | 18/34 | 84% | CONDITIONAL (FRAGILE) |
| 1.25R | -$0.66 | -$0.15 | -$0.82 | 14/34 | 0% | REJECT |
| 1.50R | -$0.55 | -$0.10 | -$0.70 | 13/34 | 0% | REJECT |
| 2.00R | -$2.75 | -$0.30 | -$2.91 | 9/34 | 0% | REJECT |
| **HYB_B (25/75 @ 0.5/1.0R)** | **+$4.75** | +$0.15 | +$4.59 | — | **9%** | PROMISING (IS) |

## 4. NET vs REAL и NET vs P3 на исторических 34 сделках

| Model | NET | Δ REAL | Δ P3 |
|---|---:|---:|---:|
| REAL | +$0.155 | — | +$0.83 |
| P3 static (T32.F) | -$0.67 | -$0.83 | — |
| WBE +3.5% (bot median) | +$1.38 | +$1.22 | +$2.05 |
| HYB_B (25/75 hybrid) | +$4.75 | +$4.59 | +$5.42 |
| HYB_C (25/75 @ 0.75/1.5) | +$4.63 | +$4.47 | +$5.30 |

## 5. GIVEBACK CAPTURE (PCE: exit at +X R)

| Level | TP fills | Avg captured R | Avg missed continuation beyond TP |
|---|---:|---:|---:|
| 0.75R | 19/34 (56%) | 0.75R | 0.61R (avg move beyond TP) |
| 0.9R | 19/34 (56%) | 0.90R | 0.41R |
| 1.0R | 18/34 (53%) | 1.00R | 0.27R |

**Pre-committed exit ЗАБИРАЕТ больше NET только при R ≤ 1.0R.** На 1.25R+ мы уже теряем (сделки с MFE 1.0-1.5R не дотягивают до TP, уходят в SL).

## 6. ANTI-CHERRY (PCE + HYB_B)

| Уровень | full | no-best | no-worst | no-both | LONG | SHORT | top1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.75R | -$0.71 | -$0.19 | -$1.08 | -$0.56 | +$0.41 | -$1.12 | 0% |
| 0.9R | +$0.30 | +$0.82 | -$0.14 | +$0.37 | +$0.92 | -$0.62 | **148%** ⚠ |
| 1.0R | +$0.59 | +$1.11 | +$0.10 | +$0.62 | +$1.27 | -$0.68 | 84% |
| 1.25R | -$0.66 | -$0.14 | -$1.28 | -$0.76 | -$0.13 | -$0.54 | 0% |
| **HYB_B** | **+$4.75** | (robust) | (robust) | (robust) | (откат) | (откат) | **9%** ✓ |

**SHORT-side убыточен на всех уровнях.** Вся дельта приходит из BUY-стороны.

## 7. PARAMETER FRAGILITY (HYB_B robustness ±10/20%)

| perturbation | результат |
|---|---|
| -20% (0.4R + 0.8R) | -$2.10 ✗ |
| -10% (0.45R + 0.9R) | -$1.07 ✗ |
| 0% (0.5R + 1.0R) | +$4.75 ✓ |
| +10% (0.55R + 1.1R) | -$1.28 ✗ |
| +20% (0.6R + 1.2R) | -$0.66 ✗ |

**Устойчивость узкая — только базовые параметры.** ±20% ломает преимущество.

## 8. OOS (chronological 2/3 train, 1/3 test)

| Model | train | OOS |
|---|---:|---:|
| HYB_B full | +$5.40 | **-$0.65** ⚠ |
| 0.9R first/second half | -$0.03 | +$0.33 |
| 1.0R first/second half | -$0.06 | +$0.65 |

**OOS не подтверждает ни одного фиксированного уровня.**

## 9. LIVE-SHADOW BACKFILL n=7 (HYB_B)

| Model | NET (n=7) | Δ vs REAL |
|---|---:|---:|
| REAL | -$0.297 | — |
| P3 | -$0.404 | -$0.108 |
| **HYB_B** | **-$0.683** | **-$0.386** |

**FALSIFICATION:** исторический replay обещал +$4.75, live-shadow даёт -$0.683. Гипотеза опровергнута на live-данных.

## 10. Главный DATA GAP

**ТОЛЬКО ОДНОГО критического параметра не хватает:**
**fill-rate maker-TP limit orders на наших символах и наших условиях (postOnly reduce-only).**

Это тот же gap, что в MAKER_SHADOW_GATE §6. Без его ответа гибридный NET — верхняя граница.

## 11. FINAL VERDICT

**CONDITIONAL на historical данных, FALSIFIED на live-shadow n=7.**

Pre-committed reduce-only Limit принципиально переносим:
- Гибрид B (25% @ 0.5R + 75% @ 1.0R) — лучший исторический кандидат
- **НЕ оправдал себя на live-shadow n=7** (HYB_B backfill дал -$0.68 vs исторических +$4.75)
- Главная причина: runner-portion 75% не получает больше движения, цена уходит обратно через catastrophic SL

**Shadow profile HYB_B уже интегрирован** в `exit_shadow_engine.py` (read-only, 4-й профиль). Продолжает собираться с каждой новой сделкой автоматически. **Никаких изменений production.**

## 12. Минимальный read-only shadow profile (УЖЕ РЕАЛИЗОВАН)

```
file: /root/tradingos/research/exit_shadow/exit_shadow_engine.py
function: simulate_hyb_b(side, entry, sl0, tp0, bars)
constants:
  HYB_TP1_R = 0.5     # partial TP1 at +0.5R
  HYB_TP2_R = 1.0     # partial TP2 at +1.0R
  HYB_FRAC1 = 0.25    # qty at TP1
  catastrophic = 3R
  timeout = 72h
```

`simulate_hyb_b()`:
- 25% позиции @ +0.5R (maker reduce-only)
- 75% позиции @ +1.0R (maker reduce-only)
- SL-first intrabar (conservative)
- Catastrophic 3R boundary (если TP не достигнуты)
- 72h timeout
- T72 fallback на последний close

`econ_hyb_b()`:
- gross per leg + fees per leg
- TP legs = maker fee (0.02%)
- SL/CAT/T72 legs = taker fee (0.055%)
- funding per leg (через `funding_cost`)

**Запись в ledger:** `D_hyb_b` field + `delta_D` (vs REAL), read-only, не модифицирует существующие P3/B/C записи.

## 13. Главный принцип следующего шага

Не "пере-изобретать exit engine". Сейчас продолжаем собирать live-shadow n=10-15. С HYB_B профилем в shadow, у нас теперь есть:
- REAL (то что было в момент закрытия)
- P3 (текущий shadow-кандидат)
- HYB_B (альтернативный кандидат, принципиально другой механизм)

**Решение принимается на основе накопленных live-данных**, не на основе historical replay (который уже один раз обманул на HYB_B).

Production FROZEN, R148 unchanged, P3 unchanged, shadow-engine работает, 28/28 тестов PASS.