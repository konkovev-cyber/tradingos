# PROFITABILITY IDEA CATALOG — TradingOS

*RESEARCH / IDEA DISCOVERY ONLY. R148 FROZEN. Production, risk, cap, entry, P3 не менять. Документ — генерация гипотез с falsification-фильтром; внедрение только после отдельного approval. Каждая гипотеза должна отвечать: механизм, источник данных, контроль, OOS, kill.*

---

```
CURRENT BOTTLENECK:        giveback $9.89 (105% MFE) + costs $2.00 (13× NET). P3 — capture
                           единственный живой кандидат (n=3, все ΔA>0, INSUFFICIENT SAMPLE).
BIGGEST UNEXPLOITED LEVER: микроструктурные/контекстные сигналы, которые РАНЬШЕ ловят
                           нож (MFE) БЕЗ удержания до 2R — захватывают первый сегмент
                           движения, где сейчас entry даёт giveback 6/14 сделок.
BEST NEW IDEA:            Entry-timing по expected MFE (impulse persistence + ADX+volume
                           persistence × small-cap thin book) — тест: σ амплитуды +0.3R
                           за 15м vs +0.5R за 4ч. Потенциально +$0.05–0.10/сделка.
EXPECTED ECONOMIC IMPACT:  LARGE (>+$0.10/сделка) только для механизмов, которые работают
                           на «первых барах» (импульс/persistence) — фокус на bootstrap TE.
                           Текущий forensics: чисто-time-or-purely-exit даёт SMALL.
                           LARGE — если разрыв между MFE_15m и MFE_4h статистически
                           значим (proxy: slope/impulse accuracy).
CONFIDENCE:               низкая (новые данные обязательны); falsification имеет
                           приоритет: 5 из 20 гипотез ниже предварительно REJECTED.
NEXT RESEARCH:            перед P3-gate не запускать ничего нового; ПАРАЛЛЕЛЬНО дать
                           субагенту отдельный forensic «Expected MFE первым касанием vs
                           exit-уровневой capture» — пересекается ли гипотеза P3.
```

---

## 1. Executive Summary

Текущий контур — AUTO CRYPTO, risk $0.50, cap 20%, R148 frozen. P3 live-shadow (n=3, все ΔA>0). Этих данных хватает на скучную правду: **главный рычаг — better capture из уже существующего движения**, и **большой абсолютный рычаг на текущем капитале — это combination, а не одиночная находка**. Документ собирает 22 гипотезы с falsification-фильтром, рейтингом по реальному денежному потенциалу, и отвечает на 4 спецвопроса.

## 2. Current Economic Bottleneck (по forensics этой сессии)

| Компонент | Потери $ | Доля | Доказательство |
|---|---|---|---|
| Giveback (take profit / executor early exit) | **$9.89** | 105% MFE | 14/34 достигли +1R, 6/14 не удержали |
| Costs (fees+slippage) | **$2.00** | 13× NET | медианный slippage 20bps |
| Funding | ~$0 | 0 | факт |
| Opportunity cost / occupancy | неизмеримо | — | 4-12ч самый прибыльный |

**Bottleneck composition:** giveback = $9.89 максимум (нельзя поймать пик без look-ahead); costs = $2.00 (конкретно, устранимо частично). На текущем капитале нет «большого» одного рычага — есть композиция.

## 3. Existing Experiments (ПРОЧЕК, чтобы новые не совпадали)

| ID | Гипотеза | Статус | Когда |
|---|---|---|---|
| P3 (33%@1R + trail 0.75R) | улучшить capture | live-shadow n=3, ΔA>0 IS | running |
| Maker (PostOnly) | снизить fees/slippage | prepare-only (DOC) | после P3 |
| Cap 30% | масштаб до полного $0.50 risk | forensic done (KEEP 20% никуда) | после P3 |
| Risk ladder | $0.50 → $1.00 | forensic done | после P3 |
| ADX<25 | режимная концентрация | пассивный OOS | после P3 |
| Holding 4-12ч | прокси exit | IS only | встроено в P3 |
| Structural exit | — | REJECT (FRAGILE) | — |
| Catastrophic SL expansion | — | REJECT (SL не режет прибыль) | — |

## 4. 20+ New Hypotheses (с falsification + ranking)

### Шкала денежного потенциала:
- MICRO: <$0.01/trade (immaterial на $61)
- SMALL: $0.01–0.03
- MATERIAL: $0.03–0.10
- LARGE: >$0.10/trade

### Priority 0–5 (0=useless, 5=exceptional)

| # | Idea | Mechanism | NET impact | Cost | Risk | Data | OOS | Priority |
|---|---|---|---|---|---|---|---|---|
| 1 | **Expected MFE первым касанием (impulse persistence)** | Сигнал, где +R уже за 15м, чаще имеет +0.3R за 4ч — entry рано ловит первый сегмент, цель импульса | LARGE (>$0.1) | LOW | LOW | partial: MFE_15m / MFE_4h ratio на 34 | лёгкий | 4 |
| 2 | **Liquidity-aware skip (spread/volume filter безопасно)** | Не торговать когда spread > 0.05% И volume_24h < 10×ATR-current — экономия slippage ~30% | MATERIAL (~$0.05) | 0 | LOW | есть в microstructure | трудный (data только top 15) | 3 |
| 3 | **Time-of-day filter (UTC)** | Часы с наименьшим avg-MAE/MFE-ratio сигнала | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 2 |
| 4 | **Expected MAE pre-trade gate** | Если predictor оценивает MAE > 0.8R, skip — уменьшает тяжёлый хвост | MATERIAL (~$0.04) | 0 | LOW | отсутствует (нужен pre-MAE) | трудный | 2 |
| 5 | **Position competition (allocator)** | max_pos=4, но низколиквидные сделки занимают слот; приоритизация по expected MFE/$ | MATERIAL (~$0.05) | 0 | LOW | partial | лёгкий | 3 |
| 6 | **Dynamic time exit (4-12ч opt из forensic)** | Закрыть по max-holding, если trade не достиг +0.5R | MATER(I)AL ~$0.05 | 0 | LOW | partial | лёгкий | 2 |
| 7 | **Re-entry after winner exit (P3-extended)** | После P3 partial, если price продолжает +R, открыть новую позицию меньшего размера | MATERIAL (~$0.05) | LOW (commission) | LOW | DATA GAP | трудный | 2 |
| 8 | **Loser avoidance signature** | Признаки, что сделка пойдёт в -1R (без look-ahead) | MATERIAL (~$0.04) | 0 | LOW | DATA GAP | трудный | 2 |
| 9 | **BTC.dom для alts-mode** | Alt сигнал сильнее, когда BTC в режиме X | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 2 |
| 10 | **Funding-rate tilt** | Если funding > +0.05%/8h, SELL contestants тяжелее; альтсигналы, идущие ПРОТИВ funding, лучше | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 2 |
| 11 | **OI divergence (top-15 microstructure)** | Если OI растёт + price stagnates → reversal risk | SMALL (~$0.02) | 0 | LOW | partial (15 syms) | лёгкий | 2 |
| 12 | **Cross-sectional relative strength** | Alt, которая сильнее рынка за последний час, имеет лучший MFE | MATERIAL (~$0.04) | 0 | LOW | partial | лёгкий | 2 |
| 13 | **Failed-breakout fade** | На пробое 30D high с immediate reversal — SELL re-entry | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 1 |
| 14 | **Pullback entry (НЕ сейчас, а в ИДЕЕ)** | Вместо immediate-impulse ждать 50% pullback — лучший mean entry | LARGE потенц. | 0 | LOW | poor (impulse сейчас конвертит в реальное время) | лёгкий | 3 |
| 15 | **Conditional cancel/replace по adverse selection** | PostOnly, который за 30с не заполнен при adverse move → cancel, taker fallback | SMALL (~$0.02) | LOW | LOW | partial | лёгкий | 2 |
| 16 | **No-trade-зоны (earnings, FOMC, roll)** | Не торговать в высоковолатильные часы | SMALL (~$0.02) | 0 | LOW (потенциально ↓ исполнение) | partial | лёгкий | 2 |
| 17 | **Fear & greed / long-dominance regime** | Активировать только в risk-on regimes | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 1 |
| 18 | **Symbol-tier ranking (sub-billion liquidity)** | Top-2B по ликвидности vs bottom-cap — разделить выборку | MATERIAL (~$0.04) | 0 | LOW | есть (Microstructure, 15 syms) | лёгкий | 2 |
| 19 | **Pre-impulse cooldown** | Не входить в первые 5 мин импульса (queue disadvantage) | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 2 |
| 20 | **Lead/lag BTC → alts** | Alt-сигнал в первые 60с после BTC-strong имеет более высокий MFE | MATERIAL (~$0.04) | 0 | LOW | partial | лёгкий | 2 |
| 21 | **Cross-margin health (insurance budget)** | Не удерживать открытые позиции когда счёт обнуляет max_open_risk ≤ 1.5% — позволить аллокатору заходить | SMALL (~$0.02) | 0 | LOW | partial | лёгкий | 2 |
| 22 | **Dynamic risk scaling по realized vol** | risk_per_trade · (1 / realized_vol_1h) — низкая vol → больше позиций, высокая vol → меньше | MATERIAL (~$0.04) | 0 | LOW | DATA GAP | лёгкий | 2 |

**PRE-вёрдж figерий:**
- Тезисы 1 и 14 — единственные с потенциальным LARGE, оба про ранний capture
- Тезисы 2, 5, 6, 7, 12, 18, 22 — MATERIAL, реалистичные
- Остальные — SMALL / investigational

## 5. TOP-10 (после приоритезации)

| Rank | Idea | Why it could work | Why it could fail | Disproof | Min sample | EV | Complexity |
|---|---|---|---|---|---|---|---|
| 1 | **#1 Expected MFE первым касанием** | impulse persistence наблюдается на финансовых рынках; small-cap с тонкой книгой дают резкие +R быстро | persistence может быть selection bias; thin book → spurious correlations | OOS: импульс-presistence factor не предсказывает MFE за 4h | 30+ сделок | LARGE | medium (нужен offline-forensic) |
| 2 | #5 Position competition | данные HOLD на 4-12ч показывают, что занятость слота вредит; приоритизация по expected MFE | pre-MFE predictor может быть шумным | OOS: prioritization не улучшает портфельный NET | 30+ | MEDIUM | low |
| 3 | #6 Dynamic time exit | 4-12h самый прибыльный диапазон; текущие BE/TIGHT/TIGHT выходят рано | max-holding exit может выходить на V-образных разворотах | OOS: holding cap не даёт лучший NET | 30+ | MEDIUM | low |
| 4 | #14 Pullback entry | pullback entry исторически лучше immediate в momentum setups | на текущей выборке 100% small-cap — pullback vs impulse не различимы | OOS: pullback entries не имеют лучший NET на аналогичных сигналах | 30+ | LARGE | medium |
| 5 | #2 Liquidity-aware skip | spread/volume proxy — есть в microstructure | данных только 15 символов; partial coverage | OOS: skipped trades не имели лучшего NET если бы исполнялись | 30+ на 15 симв. | MEDIUM | low |
| 6 | **#22 Dynamic risk scaling** | vol-relative sizing — стандартная практика | thin-cap realized_vol может быть unstable | OOS: scaled sizing не даёт лучший NET чем flat | 50+ | MEDIUM | low |
| 7 | **#18 Symbol-tier ranking** | top-2B vs bottom-cap NET сильно различается; объединение ухудшает среднее | данных недостаточно для high-cap tier | OOS: топ против боттом имеет одинаковый NET-per-trade | 30+ | MEDIUM | low |
| 8 | **#7 Re-entry after winner exit** | exit может начать новый сегмент движения; P3 partial exit + re-entry = stacked | Несколько позиций увеличивают exposure | OOS: re-entry не улучшает per-trade NET | 20+ | MEDIUM | medium |
| 9 | **#12 Cross-sectional relative strength** | сильнейшие alts имеют лучший MFE | rank signal может lagging | OOS: альт с лучшим RS должен иметь MFE > среднего | 30+ | MEDIUM | low |
| 10 | **#20 Lead/lag BTC → alts** | BTC-strong first 60s → alt имеет больший MFE | может быть sampling bias | OOS: lead/lag factor не предсказывает MFE | 30+ | MEDIUM | low |

## 6. TOP-3

| # | Idea | Criteria met |
|---|---|---|
| **#1 Expected MFE первым касанием** | LANRGE потенц. ($0.10+) — единственное, что может дать 2× NET. Mechanism: persistence impulse на small-cap. Verify: импульс-presistence factor vs MFE_4h, OOS. |
| **#5 Position competition** | MATERIAL — высвобождает слоты. Mechanism: аллокация по predicted MFE. Verify: ranking по expected MFE vs текущий FIFO. |
| **#6 Dynamic time exit (4-12h cap)** | MATERIAL — empirical finding из текущей выборки. Mechanism: выход из не-движения. Verify: max-holding gate on losers. |

## 7. Biggest Potential Money Levers

**На $61 капитале отдельно взятая идея НЕ даёт 2× NET без удвоения risk.** Существует **composition-рычаг**: P3 (capture) → cap 30% (scale) → multiple-improvements (cost down). Это удваивает или больше, но через addressable-ярусы, а не одним механизмом.

## 8. Ideas Independent of P3

#1, #2, #5, #6, #7, #14, #20, #22 — не используют P3-механику. #4 (pullback) конкурирует с текущим «immediate entry». #4 не реализует «держать позицию дольше», он меняет ТОЧКУ входа.

## 9. Ideas Independent of Entry

Только #6 (time exit) и #7 (re-entry) — действуют на уровне exit. #18 (symbol-tier) — частично рыночный выбор, но не меняет entry-сигнал.

## 10. Ideas Independent of Exit

Только #1 (entry timing), #2 (liquidity skip), #14 (pullback entry), #12 (relative strength), #20 (lead/lag).

## 11. Cost Reduction Opportunities

#2 (liquidity skip), #15 (cancel/replace), maker (отдельный документ). Cost-уменьшение работает <$0.01/trade на $61 — IMMATERIAL, но становится MATERIAL на $200+.

## 12. Capital Efficiency Opportunities

#5 (position competition), #6 (time exit), #22 (vol-scaling). Эти воздействуют на доход на единицу capital-time, без удвоения risk.

## 13. Rejected / Low-Value Ideas

- Все, что сводится к «simple maker = прибыль» (DOC MAKER_SHADOW_GATE — IMMATERIAL на $61).
- Все, что сводится к увеличению risk или leverage (R148).
- Убрать guards/catastrophic protections (R148).
- Подгонять пороги ADX/score/prob на текущей выборке (R148).
- Использовать hindsight для оптимизации TP/SL.

## 14. Спецвопросы

**«2× NET, не меняя risk?»** — composition: P3 (capture) + cap 30% (scale) + cost ↓. Не одна идея.
**«2× NET, не увеличивая #sделок?»** — composition, тот же ответ.
**«2× NET, не меняя entry?»** — exit-механика (P3, time-exit, partial) + cost-reduction.
**«2× NET, не меняя exit?»** — entry-quality (impulse persistence, pullback, lead/lag) + selection.

Четыре отдельные техники, одна цель — увеличить NET per unit of capital-time.

## 15. Recommended Research Queue

**НЕ запускать параллельно P3.** Порядок:
1. (после P3 n=30) T1 #1 — Expected MFE первым касанием (offline-first, forensic на 34 сделках).
2. T2 #5 — Position competition (offline, allocator на тех же 34).
3. T3 #6 — Dynamic time exit (offline, time-cap variant из существующего forensic).
4. (если P3 PASS) T4 — maker forensic.
5. (если P3 PASS) T5 — cap 30%, risk ladder.
6. Parallel: passive OOS для ADX<25, holding 4-12ч, BTC regime.

**Reject первого прохода:** тезисы 3, 4 (no-trade time-of-day), 9, 10, 11, 13, 17, 19, 21 — вероятно DATA GAP или IMMATERIAL, ниже forensic.

---

## NEXT RESEARCH = WAIT FOR P3 n=30, затем #1 (Expected MFE первым касанием)

Обоснование: (а) P3 первично — без capture мотивация для остальных рычагов ниже экономического порога; (б) #1 имеет наибольший потенциальный NET-эффект и пересекает P3-гипотезу (ожидаемый MFE ранним касанием = лучший capture), что усилит или ослабит P3 после forensic; (в) остальные 21 тезис — запас для очереди после P3-решения.
