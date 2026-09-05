# TRADINGOS PROFITABILITY DEVELOPMENT MAP

*RESEARCH / FORENSIC ONLY. R148 FROZEN. NO DEPLOY. NO RISK/ENTRY/EXIT/CAP CHANGES. P3-shadow продолжает независимо. Данные: 34 реальные закрытые AUTO CRYPTO сделки (/tmp/exit_sim/dataset.pkl, M15 path 72ч, факт. costs). n=34 — МАЛО, все выводы — гипотезы с пометкой силы.*

---

## 1. Current profitability bottleneck

**Bottleneck = реализация прибыли и costs, а НЕ входы и НЕ размер.**

- Entry pipeline работает (correlation fix live, крипто-входы возобновлены).
- **Costs 13× NET**: gross +$1.14 → fees $0.99 + slippage $1.00 → NET +$0.155.
- **expected_R +23.2R vs realized_R −1.3R (gap −24.5R)** — сигнал обещает движение, exit забирает ~ничего.
- Медианный MFE 2.17R доступен, система монетизирует ~0%.
- Размер НЕ ограничитель (cap forensic: KEEP 20% — увеличение масштабирует убыток).

## 2. What is already solved

| Проблема | Статус |
|---|---|
| Крипто-входы не проходят (correlation блокировка MANUAL STOCK) | ✅ исправлено, live подтверждено (2 OPEN после фикса) |
| MARGIN_BUFFER фантомный ноль (availableToWithdraw) | ✅ исправлено (totalAvailableBalance, $61.28) |
| Escalation в ручном контуре | ✅ shadow-audit внедрён |
| MT5 kill-switch писал в мёртвый путь | ✅ исправлено (portable) |

## 3. What is still losing NET

1. **Exit-монетизация**: 6/14 сделок с MFE ≥ +1R вернули прибыль (VET +1.36R → −0.48R). Ранние TP/BE, giveback.
2. **Fees+slippage на малоликвидных small-cap** (медианный slippage 20bps, one-way fees 8-30bps).
3. **SELL-сторона** (n=19, NET −$0.52) хуже BUY (+$0.68).
4. **ADX ≥ 25 сделки** (−$2.53) — противоположность ADX<25 (+$2.69).
5. **Сделки < 4ч** (n=20, NET −$3.35) — доминируют по количеству, все убыточны.

## 4. Top 10 measurable opportunities (по силе evidence)

| # | Кандидат | IS NET | OOS | Evidence | Комплексность | Риск |
|---|---|---|---|---|---|---|
| 1 | **ADX<25 как концентратор edge** | +$2.69 (17) | не проверено | сильное | низкая | низкий |
| 2 | **Holding 4-12ч (не выходить рано)** | +$3.76 (12) | не проверено | сильное | средняя | низкий |
| 3 | **P3 exit (уже в shadow)** | +$1.21 (34) | +$0.73..1.16 | среднее | средняя | низкий |
| 4 | BUY > SELL (не брать SELL при ADX<25?) | BUY +$0.68 | — | среднее | низкая | низкий |
| 5 | score ≥65 / quality=GOOD / prob≥0.58 | +$0.76 (3) | нет (n=3) | слабое, DATA GAP | низкая | — |
| 6 | RSI 40-49 (не 60+) | +$1.31 (16) | — | среднее | низкая | низкий |
| 7 | Снизить cost drag (maker) | +$0.67 потенц. | — | кап-модель | средняя | низкий |
| 8 | Избегать VET-типа (суб-1% SL, thin) | −$0.50 worst | — | среднее | низкая | низкий |
| 9 | Winner management (не давать +1R→0) | +$0.50 (P3-оценка) | — | среднее | — | — |
| 10 | Убрать giveback через trail | часть P3 | — | сильное | средняя | низкий |

## 5-8. Evidence / OOS / NET impact / Risk (см. таблицу выше; детали forensic в секциях ниже)

## 9. Implementation complexity & 10. Priority score

| Кандидат | NET upside | Evidence | OOS | Complexity | Risk | **Priority** |
|---|---|---|---|---|---|---|
| ADX<25 | средний | сильное | НУЖЕН | низкая | низкий | **1** |
| Holding 4-12ч | средний | сильное | НУЖЕН | средняя | низкий | **2** |
| P3 | средний | среднее | live | средняя | низкий | **3** (уже идёт) |
| BUY/SELL асимметрия | низкий-средний | среднее | НУЖЕН | низкая | низкий | 4 |
| Maker | +$0.67 | модель | нужен | средняя | низкий | 5 |

---

## Forensic details

### A. Entry quality (n=34, малые подгруппы — DATA GAP)
- **score ≥65 / prob≥0.58 / quality=GOOD: n=3, NET +$0.76** — но n=3, не доказательство.
- **ADX 20-24: n=17, NET +$2.69 (avg +$0.158)** vs ADX 25-29: n=10, NET −$1.56. **Самый сильный разделитель.**
- RSI 40-49: +$1.31 vs RSI 60+: −$0.93.
- BUY +$0.68 (15) vs SELL −$0.52 (19).

### B. Exit (P3 — единственный live-кандидат, structural REJECT)
- См. EXIT_MECHANICS_FORENSIC + /tmp/structural_exit/report.md (REJECT: top2=118%, чувствительность).

### C. Winner management
- 20/34 сделок достигли +0.5R, 14/34 +1R, 4/34 +1.5R, 2/34 +2R.
- **6/14 достигших +1R не удержали** (VET +1.36→−0.48, ARIA +1.10→+0.47, BIGTIME +1.13→+0.35, LSK +1.26→+0.33, ZORA +1.09→+0.46, CASHCAT +1.05→+0.11).
- P3-консервативная оценка: +$0.50 на 34 сделках от фиксации 33%@+1R — ровно этот giveback-канал.

### D. Trade selection (не менять фильтры, только evidence)
- Устойчиво отрицательные группы: SELL, ADX≥25, RSI≥60, holding<4ч, thin-символы (VET-тип суб-1% SL).

### E. Holding time
- **4-12ч: n=12, NET +$3.76 (avg +$0.314)** — единственный прибыльный диапазон.
- <30m: −$0.46; 30m-1h: −$1.46; 1-4h: −$1.42; 12-24h: −$0.26.
- **Вывод: ранние выходы (guardian BE/TIGHT на <4ч) убивают прибыль; движение реализуется на 4-12ч.**

### F. Symbol economics
- Лучшие: FHE +$1.27 (2), MMT +$1.05 (2), BTR +$0.97 (2), BRETT +$0.70. Худшие: CC −$1.08 (2), SAGA −$0.55, GWEI −$0.52.
- Повторные символы дают смешанный результат (CC ×2 оба убыточны, FHE/MMT/BTR оба прибыльны).

### G. Cost forensic
- gross +$1.14, fees $0.99, slippage $1.00, NET +$0.155. **Costs ≈ 13× NET**.
- На 20bps-медианном slippage каждый лишний $ notional покупает edge в ~2× fee+slip.

### H. Opportunity cost
- 4-12ч сделки — единственный NET-позитивный слот; ранние закрытия занимают слоты впустую.
- NET/occupied-hour: baseline ≈ $0.001/ч; SMART/P3-профили до $0.006-0.009/ч.

### I. Execution quality
- **expected_R sum +23.2R vs realized_R sum −1.3R (gap −24.5R)** — обещание сигнала почти полностью теряется на выходе, не на исполнении (entry=fill ≈ совпадают).

### J. Market regime (ADX-proxy)
- **ADX<25: +$2.69 (n=17) | ADX≥25: −$2.53 (n=17)** — стратегия прибыльна именно в низкотрендовом режиме; высокий ADX = убыток.

---

## TOP 3 NEXT MOVES

### MOVE 1: ADX-режимная концентрация (evidence-led)
- **WHY**: ADX<25 даёт +$2.69/17 против ADX≥25 −$2.53/17. Разделитель сильнейший из всех, не требует новых индикаторов (ADX уже в pipeline).
- **EXPECTED BENEFIT**: если 2/3 ADX≥25 сделок не исполнять → экономия ~$1.5-2 NET на 34 сделках без изменения позитивной части.
- **EVIDENCE**: IS сильное (n=17/17), OOS НЕ проверен.
- **RISK**: низкий (ничего не удаляется из production; только исследование/потом gate).
- **TEST REQUIRED**: OOS на ≥30 новых сделках (через funnel-метку ADX), day-by-day stability, не top-2 зависимость.
- **SUCCESS**: ADX<25 подгруппа сохраняет NET>0 OOS и >50% дней.

### MOVE 2: Holding 4-12ч (не закрывать рано)
- **WHY**: 4-12ч = +$3.76/12 (avg +$0.31); <4ч = −$3.35/20. Guardian BE/TIGHT на <4ч режет прибыль.
- **EXPECTED BENEFIT**: если держать winners дольше → захват движения, которое сейчас теряется (gap −24.5R).
- **EVIDENCE**: IS сильное, OOS НЕ проверен; пересекается с P3 (P3 и есть «не выходить рано»).
- **RISK**: средний (увеличение holding = больше exposure, но cap 20% ограничивает).
- **TEST REQUIRED**: offline на путях (уже частично в smart_exit: 24h-оптимум), затем shadow.
- **SUCCESS**: NET/occupied-hour растёт при сохранении DD.

### MOVE 3: P3 exit (уже в live-shadow)
- **WHY**: единственный кандидат с live-точкой (TUT +$0.271); чинит giveback 6/14 +1R-сделок.
- **EXPECTED BENEFIT**: +$0.50 (консервативно) на 34; target +$0.5-1.5 после gate.
- **EVIDENCE**: forensic +$1.21 (34), live n=1.
- **RISK**: низкий (shadow до gate).
- **TEST REQUIRED**: набрать 10-15 → промежуточный, 30 → gate.
- **SUCCESS**: gate 10/10 проверок.

---

## NEXT DEVELOPMENT PRIORITY

**ПРИОРИТЕТ: P3 exit (MOVE 3) — продолжается, ничего не менять.**

Он уже в правильном статусе: live-shadow набирает точки, R148 frozen. Параллельно — **подготовить MOVE 1 (ADX-режимную evidence-карту)**: не менять фильтры, а собирать OOS-данные по метке ADX на новых AUTO-сделках (это read-only, funnel уже считает ADX SKIP/CANDIDATE).

**Почему P3, а не ADX сразу:** P3 — это «больше денег из уже правильных сделок» без отсечения входов (риск потери edge из-за малой выборки n=34). ADX-фильтр меняет поведение входа — это следующий шаг ПОСЛЕ подтверждения exit-механики, иначе смешаем два эффекта.

**Порядок:** P3 gate (10-15/30) → затем controlled-тест ADX<25 концентрации → затем holding/P3-комбинированный профиль → maker. Ничего не внедрять без отдельного approval.

---

## FINAL VERDICT

- **Production: НЕ менять.**
- **P3-shadow: продолжает сбор (приоритет №1).**
- **ADX<25: сильнейший новый сигнал — готовить OOS-evidence пассивно (funnel уже метит ADX), НЕ фильтровать.**
- **Holding 4-12ч: подтверждает exit-направление — встроено в P3-гипотезу.**
- **SELL/RSI60+/thin-символы: убыточные подгруппы — evidence зафиксирован, фильтровать только после OOS.**
- **Risk/cap: не увеличивать (KEEP 20%, $0.50).**
