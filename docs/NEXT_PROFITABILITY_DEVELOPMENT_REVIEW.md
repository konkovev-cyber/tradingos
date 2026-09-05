# NEXT PROFITABILITY DEVELOPMENT REVIEW

*DEVELOPMENT REVIEW / PLAN ONLY. R148 = FROZEN. NO DEPLOY. NO CONFIG / RISK / CAP / ENTRY / P3 / GUARDS CHANGES. Production не трогать. Документ ищет самый короткий доказуемый путь к заметному положительному NET PnL — не "лучший Sharpe" в абстракции.*

---

## 5-line verdict

```
CURRENT BOTTLENECK:   giveback $9.89 (87% of MFE) + costs $2.00 (13× NET). Entry НЕ bottleneck — MFE есть. P3 n=4, 4/4 > REAL, INSUFFICIENT SAMPLE.
BEST NEXT MOVE:       WAIT for P3 n=10–15 → interim forensic. P3 уже удерживает Δ>0 на 4 сделках; P3+adaptive giveback (E1) — единственный новый параметр, который выдержал anti-cherry на 34 путях (offline).
SECOND BEST MOVE:     pass-readiness maker fill-model instrumentation (offline, на данных с microstructure 15 символов). Не запускать live maker.
BIGGEST MISTAKE:      запустить maker-live или увеличить risk ДО exit-gate. Один пропущенный good P3-fill стирает ~20-50 сделок fee-экономии.
ROADMAP to +$0.05/day → +$0.50/day: ничего одно не даёт; нужна композиция (P3 adaptive + cap 30% + maker) → ~$0.10/сделка → +$0.50-1.00/день. Без edge увеличение risk только ускоряет потери.
```

---

## A. Что мы уже знаем (доказательная база)

| Факт | Evidence | Confidence |
|---|---|---|
| MFE суммарно 26.5R на 34 сделках | forensic 16.08 на реальных путях | HIGH |
| Giveback 23.15R (87% MFE) | forensic 16.08 | HIGH |
| Realized R суммарно = −1.28R | forensic 16.08 | HIGH |
| Gross +$1.14 / fees $0.99 / slip $1.00 / NET +$0.155 | data (n=34) | HIGH |
| Costs 13× NET — **основной cost-drag** | data | HIGH |
| 11/12 SL-выходов после закрытия продолжали движение против | forensic 16.08 | HIGH |
| 4-12h holding дал +$3.76 / 12 (IS only) | data 16.08 | MEDIUM (IS, нет OOS) |
| ADX<25: +$2.69 (n=17) vs ≥25: −$2.53 (n=17) | data 16.08 | MEDIUM (IS only, вероятен прокси) |
| P3 static 0.5/0.75R: anti-cherry проваливает (top-1 137-140%) | final review | MEDIUM (n=4 live-shadow) |
| P3+adaptive 0.5→0.3→0.15 (E1): anti-cherry переживает (top-1 85%) | final review | MEDIUM (n=34 IS, n=4 live-shadow частично) |
| Structural exit / aggressive 0.25R trail: REJECT | forensic 17.08 | HIGH |
| Cap >20% масштабирует убыток убыточного контура | cap forensic 18.08 | HIGH (но valid only пока edge не доказан) |
| First-touch impulse persistence FALSIFIED | final review | HIGH (n=34, corr -0.13) |
| Median holding 3.3h × 5-7 сделок/день ≈ 0.7 параллельных | n=34 | HIGH (position competition — не рычаг) |
| Shadow n=4 (live): 4/4 P3>REAL, Δ суммарно +$0.30, anti-cherry +$0.042, MaxDD P3 < REAL | live-shadow | MEDIUM (n=4 — не gate) |

## B. Чего мы НЕ знаем (DATA GAP / не доказано)

| Unknown | Что нужно |
|---|---|
| **Gate-able P3 (n=30)** | продолжить live-shadow |
| **OOS-стабильность adaptive giveback** | n≥10 paired, OOS-блок ≥1/3 |
| **Эффективность maker** | нет fill-модели, нет trade-through-данных на 34 путях, нет microstructure для 19 из 34 символов |
| **Maker на $61 экономика** | total-эффект <$0.01/сделка на текущей выборке (замерено в forensic 18.08) — не material |
| **Структура entry: timing / regime / lead-lag** | correlation forensic на 34 показал first-touch FALSIFIED, остальные гипотезы из IDEA CATALOG на IS-only |
| **Entry-filters (ADX / vol / momentum) не помогут** | ADX<25 эффект IS-only; OOS не проверен |
| **Position-competition рычаг** | median holding 3.3h × 5-7 сделок/день = 0.7 параллельных, max=4 не лимитирует |
| **Live-effect maker (adverse selection, partial fill, missed)** | ни одной live maker-сделки нет |
| **Можно ли перейти на большее universe (high-cap)** | весь n=34 = small-cap < $1, high-cap = 0 (DATA GAP) |
| **Эффект cap 30% на доказанном edge** | cap forensic на текущем (недоказанном) контуре: NET падает |
| **Эффект risk $0.60-1.00 на доказанном edge** | логически линейный, но нужен доказанный edge |

## C. Реальный bottleneck (декомпозиция, falsification-режим)

| Стадия | $\ потерь (n=34) | Доля | Доказано | Falsification |
|---|---|---|---|---|
| SIGNAL | — | — | n/a | сигнал существует: 850 raw / 14 ALLOW на 48ч, не bottleneck |
| ENTRY | низкая | — | HIGH | timing forensic не показывает устойчивого улучшения |
| INITIAL MOVE (MFE) | 0 (MFE 26.5R) | — | HIGH | **НЕ bottleneck — движение есть** |
| **GIVEBACK** | **23.15R (87% MFE)** | **~95% потерь** | **HIGH** | **ГЛАВНЫЙ** |
| FEES | $0.99 | 6% | HIGH | фиксированные, нельзя устранить без maker |
| **SLIPPAGE** | **$1.01** | **6%** | **HIGH** | **20bps median — устраняемо через maker/symbol-shift** |
| NET | $0.15 | — | HIGH | недовыбр = (giveback + costs) |

**Фальсификация гипотезы "bottleneck = exit":**
- Если бы entry был bottleneck — MFE был бы низким. MFE = 26.5R (vs realized R −1.28R) — движение есть, система НЕ УМЕЕТ его забирать.
- Если бы costs были bottleneck — давали бы $2, giveback=$9.89. Costs 13× NET — существенны, но вторичны.
- Если бы size/universe были bottleneck — universe сжат (small-cap, slippage 20bps) — но forensic 17.08 (cap 30-50%) показал, что увеличение cap только масштабирует убыток **при недоказанном edge**.

**Гипотеза пользователя подтверждена: bottleneck = giveback / exit-capture.** Но это **симптом**, а не root cause. Корневая причина — текущая exit-logic **не знает, когда отдать прибыль** (выбор травы делает решения в чужом exit-engine).

## D. Рычаги (17 штук)

Скоринг: доказательность (HIGH/MEDIUM/LOW/DATA GAP) × размер (SMALL/MEDIUM/LARGE) × capital-compat × implementation-safety.

| # | Рычаг | Доказательность | NET impact | Risk переобучения | Стоимость эксп | Можно иссл. сейчас | Можно внедрять | Что должно быть доказанно |
|---|---|---|---|---|---|---|---|---|
| 1 | **P3 (база)** | MEDIUM (n=4 live + 34 IS) | SMALL+ (≈+$0.05/сделку IS) | mid (top-1 90% конц.) | low (live-shadow) | да | нет | gate на n=30 |
| 2 | **P3+adaptive E1 (0.5→0.3→0.15)** | MEDIUM (n=34 IS, n=4 live shadows partial) | MEDIUM (≈+$0.07/сделку IS) | high (IS-размывание) | low (parallel profile) | да | нет | paired OOS на n≥10 |
| 3 | **Замена жёсткого SL** | HIGH (REJECT в прошлой серии) | NEGATIVE (NE делать) | n/a | n/a | нет | нет | ничего — SL-расширение не работает |
| 4 | Structural exit | HIGH (REJECT 17.08) | NEGATIVE | n/a | low (offline) | нет | нет | уступил P3 adaptive |
| 5 | Time-based exit | HIGH (REJECT на isol) | NEGATIVE | n/a | low (offline) | нет | нет | integrated в P3 hold-time, не нужен отдельно |
| 6 | **Maker execution** | LOW (DATA GAP, 0 fill-model) | SMALL на $61 (<$0.01/сделку IS) | mid | medium (offline sim + live A/B 5-20 трейдов) | да (pass-readiness) | нет | fill-rate ≥60% / ΔNET ≥+$0.01/сделку OOS |
| 7 | **Notional cap 20%→30%** | HIGH (n=34 IS) | NONE при current edge (масштабирует убыток) | high | low | да (offline sim) | нет | сначала доказанный edge |
| 8 | **Risk ladder $0.50→$1.00** | LOW (логически линейный) | NONE при current edge | high | low | да (offline sim) | нет | сначала доказанный edge |
| 9 | **Entry quality** | MEDIUM (ADX<25 IS, first-touch FALSIFIED) | SMALL (entry есть) | high (IS-фит) | low (offline) | да | нет | OOS на ADX/entry; сначала P3+adaptive |
| 10 | **ADX (proxy regime)** | MEDIUM (IS +$2.69) | SMALL | high (вероятен прокси) | low (offline) | да (passive OOS на funnel) | нет | OOS-подтверждение (≥30 сделок) |
| 11 | **Holding 4-12h** | MEDIUM (IS +$3.76) | SMALL+ | high (прокси) | low (offline) | да (passive OOS) | нет | OOS-подтверждение |
| 12 | **BUY vs SELL** | LOW (n=4, SELL=2) | NONE | high | low | да | нет | n≥10 по side |
| 13 | **Symbol/liquidity selection** | LOW (все small-cap, 0 high-cap) | SMALL | high | low (offline) | да | нет | high-cap data (DATA GAP) |
| 14 | **Costs/slippage reduction** | HIGH (наблюдаемо) | SMALL-IMMATERIAL на $61 | low | low (offline) | да | нет | n≥30 с реальной maker-data |
| 15 | **Position competition** | HIGH (median hold 3.3h, 0.7 парал.) | NONE на текущем масштабе | n/a | n/a | нет | нет | не рычаг — max=4 не лимитирует |
| 16 | **Trade frequency** | MEDIUM (5-7/день даёт 0.07-0.13 max concurrent) | NONE | mid | low (offline) | да | нет | capacity-scale test, не main driver |
| 17 | **Capital scaling $200-500** | LOW (DATA GAP) | MEDIUM-LARGE при confirmed edge | depends on edge | low (offline sim) | да (offline) | нет | post edge-gate; новые rules о max-drawdown по capital |

## E. Hard SL replacement — отдельный вопрос (8 альтернатив)

| Механизм | Защищает catastrophic? | Не выбрасывает на шуме? | Сохраняет upside? | Реализуемо? | Проверяемо без prod? | Вердикт |
|---|---|---|---|---|---|---|
| Catastrophic boundary (-3R) | ДА | ДА (не срабатывает в обычной торговле) | ДА | ДА (уже есть) | ДА (cat forensic 18.08) | **KEEP** |
| Structural invalidation | ДА (через anchor) | ЗАВИСИТ (anchor чувствителен) | ДА | medium (offline) | ДА (n=34) | REJECT (17.08, anti-cherry) |
| Time-stop | ДА (если long enough) | ЗАВИСИТ (если короткий — выбрасывает) | ДА | low | ДА (n=34) | REJECT (n=4) |
| Profit-state exit | ДА (есть catastrophic внутри state) | ДА (зависит от state-machine) | ДА | high | ДА | **P3 / adaptive — это уже profit-state exit на практике** |
| Adaptive giveback | ДА (cat внутри) | ДА (если lower-than-0.5R giveback) | ДА | low (1 параметр) | ДА | **PROMISING (E1 IS, anti-cherry OK)** |
| Partial protection | ДА (cat внутри) | ДА | ДА (фиксированная partial) | low | ДА | P3 (уже есть) |
| Trigger / order-based | ДА (cat-ордер) | ДА (если trigger не задерживает) | ДА | medium | только live | **Создаёт prod-cost, не пока нужен** |
| Hybrid (cat + adaptive + P3) | ДА | ДА | ДА | low | ДА (offline) | **Это естественное расширение E1** |

**Главное:** уже существующая комбинация "catastrophic 3R + P3 partial + adaptive giveback" — это **уже hybrid exit-engine**. Не нужно строить новый engine.

**Гипотеза конкретная для future-research:** добавить P3+adaptive 0.5→0.3→0.15 (E1) в shadow как parallel profile. Cost: низкий (одна функция в `simulate_p3`). Условие: ОК после n=10–15 P3-gate.

## F. Hard SL Replacement + Hybrid Protection (falsification-честный)

**Минимальная hybrid-композиция, которая уже есть в текущей системе:**
- Catastrophic boundary 3R (hard SL, встроен в simulate_p3 + cat-test в trade_executor)
- P3 partial 33% @ +1R
- Runner с фиксированным giveback 0.75R (текущее P3) **или** adaptive 0.5→0.3→0.15 (E1, IS-tested)

**Это уже реализуемая и проверяемая комбинация.** Не нужно строить "Exit Engine v2" — нужно только A/B-тест "P3 static 0.75" vs "P3 adaptive E1" на live-shadow n≥10.

**Falsification, который нужно сделать ДО внедрения E1:**
- Является ли adaptive giveback IS-overfit? На n=34, P3 static 0.5/0.75 проваливают anti-cherry, E1 переживает — но **adaptive tight 0.5→0.3→0.15 был подобран под IS-данные**. OOS-проверка обязательна.
- Два разных band-настройки (0.5→0.3→0.15 vs 0.5→0.5) дают +$3.55 vs +$1.66 на n=34 — **114% разница** в IS. Это сильный сигнал IS-overfit, и нужна OOS-валидация.

**Что защищает капитал, если рынок идёт против:** catastrophic boundary 3R (жёсткий stop, всегда срабатывает).

**Механизм, который одновременно:**
1. Ограничивает catastrophic loss — ДА (cat 3R, проверено в final review)
2. Не выбрасывает позицию из-за шума — ДА (cat 3R, не выбивает на малых движениях)
3. Сохраняет upside — ДА (P3/E1 adaptive giveback)
4. Реализуемо технически — ДА (уже реализовано в shadow-engine)
5. Проверяемо без prod — ДА (read-only forensic)

**Гипотеза для OOS-проверки:** "P3+adaptive E1 на live-shadow даёт лучший ΔNET чем P3 static, при n=10–15 live точек". Условие внедрения: paired Δ(P3+E1) > Δ(P3 static), n≥10, anti-cherry устойчиво, OOS подтверждает.

## G. Economic priority + уровни месячного NET

Чтобы ответить на "+$0.05/день → +$0.25/день → +$0.50/день → +$1/день → +$3/день" — сначала перевод в $/месяц:
- +$0.05/день = +$1.10/месяц (22 trading days)
- +$0.25/день = +$5.50/месяц
- +$0.50/день = +$11.00/месяц
- +$1/день = +$22/месяц
- +$3/день = +$66/месяц

При 5.5 сделках/день (текущая частота), это значит per-trade targets:

| /месяц | /день | $\Delta$/сделку | Реалистичность на $61 |
|---|---|---|---|
| $1.10 | $0.05 | **$0.009** | сейчас: P3+adaptive IS=$0.07 (ЕСЛИ passes OOS) |
| $5.50 | $0.25 | $0.045 | нужна композиция |
| $11.00 | $0.50 | $0.091 | нужна композиция + scale |
| $22.00 | $1.00 | $0.182 | нужна confirmed edge + cap 30% + maker |
| $66.00 | $3.00 | $0.545 | ТОЛЬКО при capital scaling $200+ |

**Ключевое:** ни один одиночный рычаг не даёт $0.045+/сделку на $61. Нужен composition:
- P3 adaptive: +$0.07 (если passes OOS)
- Cap 30%: даёт медианный риск $0.50, но +$0.025/сделку
- Maker: +$0.005/сделку (immaterial на $61)
- TOTAL composition: ~$0.10/сделку = +$22/месяц = +$1/день на $61 — но ТОЛЬКО если каждый компонент validated.

**Без edge любое увеличение risk/cap только увеличивает скорость потерь.** R148 frozen.

## H. Decision tree (post-n=10–15)

| IF | THEN | NEXT |
|---|---|---|
| **P3 FAIL** (Δ≤0, anti-cherry провален) | P3 — не тот exit. Не «лучше exit», это неправильный фрейм. | Re-think: где реально bottleneck — entry, costs, freq? |
| **P3 PROMISING** (Δ>0, anti-cherry OK, n=10–15) | E1 candidate-тест paired на live-shadow n=30 | E1 forensic |
| **P3 STRONG** (Δ>0, anti-cherry OK, OOS OK, top-1<30%) | Pre-launch adaptive comparison | P3+adaptive (E1) parallel в shadow |
| **P3 PASS на n=30** | Decision to prepare production | P3→E1→Maker→Cap 30%→Risk ladder (по одному, в этом порядке) |

**Алгоритм post-P3-PASS:**
1. P3 + adaptive E1 paired shadow test (n=30 paired)
2. Если E1 лучше plain P3 — добавить как опцию exit (но не подменять default)
3. Maker fill-model (offline, microstructure)
4. Maker live test (микро-$5)
5. Cap 20%→30% controlled test
6. Risk ladder $0.50→$0.60→$0.75→$1.00 (каждый уровень NET-контроль)
7. Capital scaling $61→$200+ (только post edge-gate)

## I. Parallel preparation (read-only, P3 не загрязняет)

| Готовка | Почему не загрязняет P3 | Что делает |
|---|---|---|
| Maker fill-model instrumentation | Не меняет execution type (taker), только логирует spread/depth | собирает данные для offline-sim |
| Adaptive E1 parallel в shadow | Не меняет P3, добавляет альтернативный профиль | paired OOS-данные для adaptive |
| Structural-exit offline dataset | Не меняет production | дополнительный fallback-candidate |
| Liquidity/slippage segmentation | Не меняет execution, только классифицирует | DATA для symbol-tilt |
| Position competition simulator | Не меняет production | подтвердил, что не рычаг при текущей частоте |
| Economic simulator | Не меняет production | backtest "+$0.05-3/день" сценариев |

Все — `research/`, read-only, не production.

## J. Best moves + Mistakes + Roadmap

### BEST NEXT MOVE (одно)
**WAIT for P3 n=10–15. На n=10–15 провести interim forensic (механизм MFE→capture→giveback→NET, OOS, anti-cherry без TUT, BUY/SELL split). Если P3 держится → подготовить P3+adaptive E1 parallel profile в shadow (read-only).** Не запускать adaptive live до n=30 P3-gate.

### SECOND BEST MOVE
**Pass-readiness maker fill-model instrumentation: расширить shadow-engine записывать spread/depth (если доступно) в момент entry-сигнала. Не запускать maker live — только read-only сбор. Делать одновременно с P3-сбором.**

### BIGGEST MISTAKE TO AVOID
**Увеличить risk или cap ДО доказанного exit-edge.** При текущем (недоказанном) P3-выигрыше рискованно:
- Увеличение risk $0.50→$0.75 даст +50% каждого Δ, но **mean Δ сейчас $0.10/сделку**, и **малый n=4** — если среднее уменьшится на 30-50% при большем n, **больший risk = большие потери**.
- Увеличение cap 20%→30% масштабирует убыток (cap forensic, n=34).
- Увеличение universe/high-cap — DATA GAP, нельзя оценить.

**Одна потерянная good P3-fill стирает 20-50 сделок fee-экономии.**

### PREPARE NOW (read-only)
- P3+adaptive E1 parallel profile в shadow-engine (после n=10–15)
- Maker fill-model: добавить field `mfe_at_signal` + microstructure snapshot в shadow-state
- ADX/holding-4-12h passive OOS-counters (уже есть в crypto_funnel.py)
- Position-competition confirmation: уже сделан, вывод — не рычаг

### WAIT FOR
- P3-shadow n=10–15 (interim forensic)
- P3-shadow n=30 (full gate)
- После P3-gate, **отдельно**: maker fill-model, E1 paired, cap 30% controlled test
- НЕ ждать: больше research; это сейчас бесполезно

### EXACT NEXT EXPERIMENT
**P3 live-shadow продолжается без изменений.** R148 frozen. Каждая закрытая сделка пишется в ledger с P3-net, MFE, MAE, giveback, holding. На n=10–15 запускается interim forensic (read-only) и затем добавляется P3+adaptive E1 parallel profile (read-only).

### EXACT SUCCESS CRITERIA (P3 at n=10–15)
1. ΔNET > 0 (REAL суммарно)
2. median Δ > 0
3. bootstrap 95% CI median Δ не пересекает 0
4. anti-cherry без лучшей > 0
5. MFE capture > REAL (capture ratio, не только sum)
6. giveback < REAL (giveback ratio)
7. **Δ без TUTUSDT > 0** (независимость от 1 сделки)
8. top-1 symbol < 50%, top-1 side < 80%
9. MaxDD ≤ REAL
10. OOS direction согласован (последние 1/3)

### EXACT FAILURE CRITERIA (P3 FAIL)
- ΔNET ≤ 0
- median Δ ≤ 0
- anti-cherry без лучшей < 0
- OOS reverses sign
- top-1 symbol ≥ 80% (FRAGILE)
- Δ без TUTUSDT ≤ 0
- MaxDD P3 > REAL (worst)

### ROADMAP to material monthly NET (на $61)

| Stage | Component | Дополнительный /сделку | Дополнительный /день (5.5) | /месяц (22 дня) | Требуется |
|---|---|---|---|---|---|
| 0 (сейчас) | REAL | — | — | $0.15 | — |
| 1 | P3+adaptive E1 (если passes OOS) | +$0.07 | +$0.39 | +$8.50 | n=10–15 forensic, n=30 gate |
| 2 | cap 20%→30% (если edge в #1) | +$0.025 | +$0.14 | +$3.00 | n=10 после edge |
| 3 | maker (если fill-rate ≥60%) | +$0.005 | +$0.03 | +$0.60 | n=10 maker-data |
| 4 | risk $0.50→$0.75 (если #1-#3) | +50% каждого | +$0.28 | +$6.10 | n=10 после #3 |
| 5 | $0.75→$1.00 (если #1-#4) | +33% каждого | +$0.28 | +$6.20 | n=10 после #4 |
| 6 | capital $61→$200 (если #1-#5) | линейно | — | $1.10 → $4.00 | depends on edge |
| 7 | $200→$500 | линейно | — | $4 → $10 | depends on edge |

**Кумулятивно после stage 5: ~+$0.30/сделку (~$1.65/день, ~$36/месяц на $61).** Это **материально для $61** — НО только если все 5 stages passes OOS.

**Без edge:** все stages net-отрицательные. Увеличение risk/cap на недоказанном P3 **снижает** net.

### Главное: архитектура не упирается в потолок — она упирается в недоказанный edge

Текущая архитектура (P3 live + guard-стек + cap 20% + risk $0.50) **не мешает заработку**. Она **низко-эффективна** из-за giveback $9.89 (P3/E1 его адресует) и costs $2 (maker адресует). Если P3/E1 passes, costs maker — net 5-8$/месяц. Если + risk ladder — 10-30$/месяц. **Реально material NET возможен**, но требует **последовательности доказательств** (один stage за раз).

---

## ONE NEXT ACTION = **WAIT for P3 n=10–15, interim forensic**

Обоснование: P3+adaptive E1 — единственный новый рычаг, который прошёл IS anti-cherry (n=34, +$0.04 без лучшей). На live n=4 P3+adaptive IS-преимущество, но малая выборка. **P3+adaptive не внедрять до n=30 P3-gate.** Maker не запускать live до того же gate. Cap/risk не менять. **Самое ценное сейчас — следующие 6+ AUTO CRYPTO входов и их финализация в shadow. R148 frozen.**
