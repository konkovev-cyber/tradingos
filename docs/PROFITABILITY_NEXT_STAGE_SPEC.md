# PROFITABILITY NEXT STAGE SPEC

*Development-forensic spec. RESEARCH ONLY. R148 = FROZEN. NO DEPLOY / NO RESTART / NO CONFIG CHANGES. Все данные — 34 реальные закрытые AUTO CRYPTO сделки (/tmp/exit_sim/dataset.pkl) + live-shadow ledger.*

---

## EXECUTIVE VERDICT

**CURRENT BOTTLENECK:**
> Реализация уже возникшего движения через exit + cost drag: gross +$1.14 → fees $0.99 + slippage $1.00 → NET +$0.155 (costs ≈ 13× NET; expected_R +23.2R vs realized −1.3R, gap −24.5R).

**PRIMARY HYPOTHESIS:**
> Exit-механика, которая фиксирует часть прибыли при +1R и удерживает остаток трейлингом (P3), систематически монетизирует движение, которое текущая exit-механика отдаёт назад (giveback 6/14 сделок с MFE≥+1R).

**NEXT EXPERIMENT:**
> Продолжить P3 live-shadow до n=30 финализированных сделок; промежуточный сигнал на n=10–15. Параллельно — пассивный OOS-сбор ADX-меток (НЕ фильтр) и полный counterfactual-набор CONTROL/P3/STRUCTURAL на расширенном датасете.

**DO NOT CHANGE:**
- risk_per_trade ($0.50), NOTIONAL_CAP (20%), max_positions (4), max_same_side (1)
- entry pipeline, scoring, thresholds, ADX, gates, universe
- SL/TP генерация, correlation (внедрён), deposit guard, MARGIN_BUFFER ($5)
- P3-shadow (профиль 33%@1R, trail 0.75R), production exit
- R148 (весь production frozen)

**SUCCESS CRITERIA (конкретно):**
- Иерархический P3 gate (НЕ жёсткое 10/10 — иначе можем отклонить хороший экономический результат из-за второстепенного технического критерия).
- Промежуточный (n=10–15): median Δ(P3−REAL) > 0 и не объясняется 1–2 сделками.

**P3 GATE — ИЕРАРХИЧЕСКИЙ:**

**REJECT** (если любое):
- median ΔNET ≤ 0;
- P3 NET ≤ REAL NET;
- эффект исчезает после anti-cherry-pick (удаление лучшей/худшей сделки, одного symbol >30%, одного side >70%);
- fees/slippage съедают весь прирост (NET после costs не положительный);
- MaxDD существенно хуже без компенсации;
- OOS не подтверждает направление.

**PROMISING** (если):
- median ΔNET > 0;
- P3 NET > REAL NET;
- MFE capture выше;
- giveback ниже;
- MaxDD не ухудшается существенно;
- но статистической/экономической уверенности пока недостаточно.

**PRODUCTION CANDIDATE** (только если ВСЁ):
- n ≥ 30;
- ΔNET устойчиво > 0;
- median Δ > 0;
- P3 NET > REAL NET;
- эффект не концентрирован в одном symbol/side;
- OOS подтверждает направление;
- costs учтены полностью (NET после fees/slippage положительный);
- MaxDD приемлем;
- occupancy не ухудшается существенно;
- есть понятный экономический механизм (P3 монетизирует уже возникшее движение).
- Production НЕ меняется автоматически — только по отдельному approval.

**KILL CRITERIA (конкретно):**
- На n=10–15: median Δ ≤ 0 при ≥3 днях И эффект исчезает после anti-cherry-pick → снять «PROMISING», наблюдать без изменений.
- Один trade ≥ 100% NET (top-1) при n≥15 → выборка невалидна (концентрация), продолжить сбор до оценки без топ-1.
- Если Δ объясняется только большим holding time (а не монетизацией движения) → проверить, не артефакт ли это.

**PRODUCTION STATUS:**
FROZEN / NO DEPLOY

---

## 1. EXECUTIVE VERDICT
См. блок выше. Ключевой факт: **проблема не во входе и не в размере — движение возникает, но не монетизируется** (gap −24.5R; costs 13× NET). P3 — единственный кандидат с live-точкой; ADX<25 — сильный forensic-сигнал, но OOS не доказан; structural exit — REJECT.

## 2. CURRENT BOTTLENECK
- Gross +$1.14 / Fees $0.99 / Slippage $1.00 / NET +$0.155 на 34 сделках.
- expected_R +23.2R vs realized −1.3R → сигнал обещает ~24R, реализовано ≈ 0.
- Медианный MFE 2.17R доступен, capture ~0%.
- costs 13× NET — на 20bps-медианном slippage малоликвидных small-cap каждый $ notional покупает edge в ~2× fee+slip drag.

## 3. WHAT WE ARE ACTUALLY OPTIMIZING
**REALIZED NET PnL после всех costs** на малом реальном капитале. НЕ WR, НЕ gross, НЕ количество сделок, НЕ paper expectancy. Единственная ось оптимизации: **больше NET из уже правильных сделок** (уже существующее движение), без увеличения risk и без снижения качества входов.

## 4. ENTRY vs EXIT vs COST decomposition

| Компонент | Влияние на NET | Доказательство | Выборка | IS/OOS | Что измеримо | Что НЕЛЬЗЯ утверждать |
|---|---|---|---|---|---|---|
| 1. ENTRY EDGE | движение ЕСТЬ (MFE 2.17R med) | gap −24.5R | 34 | IS | expected vs realized | edge > 0 после costs не доказан |
| 2. POSITION SIZING | НЕ bottleneck | cap forensic: NET падает с ростом cap | 34 | IS | риск/notional | увеличение размера даёт больше NET |
| 3. EXIT / PROFIT CAPTURE | **ГЛАВНЫЙ** (6/14 giveback; capture ~0%) | P3 forensic +$1.21/34 | 34 | IS+OOS(11) | MFE capture, giveback | P3 статистически доказан (n=1 live) |
| 4. SL / LOSS CONTAINMENT | SL корректен (11/12 уходили ≥1R против) | SL-after-exit | 12 SL | IS | fwdR | «SL режет прибыль» |
| 5. HOLDING TIME | 4–12ч +$3.76 vs <4ч −$3.35 | IS-корреляция | 34 | IS | NET по бакетам | holding сам по себе edge (прокси exit) |
| 6. FEES | $0.99/34 ≈ 6.4× NET | факт | 34 | — | fee/сделку | maker-экономия реализуема без fill-риска |
| 7. SLIPPAGE | $1.00/34 ≈ 6.5× NET | факт (20bps med) | 34 | — | slip/сделку | 2bps-модель отражает реальность (реал. 20bps) |
| 8. OPPORTUNITY COST / SLOT | ранние закрытия занимают слоты впустую | NET/occ-hour | 34 | IS | NET/час | — |
| 9. MARKET REGIME | ADX<25 +$2.69 vs ≥25 −$2.53 | сильный IS | 17/17 | **OOS НЕТ** | ADX-метка | ADX — самостоятельный edge (возможен прокси) |
| 10. INSTRUMENT SELECTION | CC −$1.08, SAGA/GWEI −0.5; FHE/MMT/BTR + | повторяемость | 34 | IS | NET/символ | символьные паттерны робастны |

## 5. P3 hypothesis (каузальная проверка)

**Механизм:** при достижении +1R фиксируется 33% (реализованный R гарантирован), остаток 67% управляется трейлингом 0.75R за экстремумом → giveback сокращается, MFE capture растёт.

**Что должно наблюдаться, если P3 действительно улучшает NET (контрольный список на n=30):**
- [ ] REALIZED NET(P3) > REALIZED NET(CURRENT)
- [ ] MFE capture ↑ (прокси: realized_R P3 / MFE > realized_R fact / MFE)
- [ ] giveback ↓ (доля сделок MFE≥1R, реализовавших <1R)
- [ ] NET после fees ↑ (уже в модели: incremental fees)
- [ ] PF не ухудшается существенно
- [ ] MaxDD не хуже
- [ ] average loss не становится опасно большим (|avgLoss| ≤ ~$0.5 = 1R)
- [ ] holding/occupancy приемлемо (NET/occupied-hour не падает)
- [ ] результат сохраняется OOS (последние 1/3 сделок)

**Минимальные sample sizes:**

| Уровень | n (закрытых сделок) | Критерий |
|---|---|---|
| A. preliminary signal | 10–15 | median Δ > 0, не 1–2 сделки |
| B. promising | 30 | gate 10/10 (exit_shadow_report.py) |
| C. statistically credible | 50–60 | Δ>0 в ≥60% сделок, p<0.1 (sign test), OOS-половина |
| D. production gate | 30 + независимый OOS-блок | C-уровень подтверждён на новых данных после B |

## 6. ADX<25 hypothesis (прокси-декомпозиция)

**НЕ ставить фильтр.** Вопрос: ADX<25 — самостоятельный edge или прокси другого фактора? Проверить по каждой из 34 сделок (разбить ADX<25 vs ≥25):

- instrument composition (какие символы в каждой группе — не концентрируется ли edge в 2–3 символах)
- BUY/SELL mix
- RSI, score, prob
- volatility (ATR/price)
- spread/slippage (из costs)
- holding time
- MFE / MAE
- exit type (TP/SL/GUARDIAN/MANUAL)
- time of day (UTC-час)
- symbol concentration (HHI)

**Гипотеза-прокси №1:** ADX<25 ≈ нет тренда → меньше импульсных SELL-ловушек, входы ближе к развороту. **№2:** ADX<25 ≈ больше сделок держатся 4–12ч (совпадение с holding-edge). **№3:** ADX<25 ≈ конкретные символы (FHE/MMT/BTR). **Решение:** если ADX-эффект исчезает после контроля за holding/symbol/side — это прокси, НЕ отдельный edge. Пока это не доказано — ADX остаётся forensic-меткой, не фильтром.

## 7. Structural Exit hypothesis
**REJECT (из /tmp/structural_exit/report.md):** top2 = 118% NET, confirm 0→1 рушит (+$1.69→−$6.83), MFE capture отрицательный, swing-вариант MaxDD −$9.7. Не развивать на n=34. Вернуться только на ≥30 новых сделок с робастностью к параметрам и top2<50%. **В наборе CONTROL/P3/STRUCTURAL числится только для чистоты методологии — не как кандидат на внедрение.**

## 8. Holding-time hypothesis
4–12ч: +$3.76 (n=12, avg +$0.31) — единственный прибыльный диапазон; <4ч: −$3.35 (n=20). **Прокси-статус:** скорее следствие exit-механики (ранний BE/TIGHT на <4ч режет прибыль), чем самостоятельное правило. Встроено в P3-гипотезу («не выходить рано»). Отдельным фильтром НЕ становится.

## 9. Size/capacity analysis
**KEEP 20% / $0.50 (из /tmp/cap_forensic/report.md).** Повышение cap 20→50% монотонно ухудшает NET (−$0.67→−$1.36) и MaxDD (−$2.31→−$3.12); cap binding 85% сделок на 20%; медианной монете нужен cap 30% чтобы достичь $0.50 risk — но это масштабирует убыток убыточного контура. risk_per_trade, notional cap, lot-step, SL-дистанция — различать; bottleneck — не размер, а edge/exit/costs.

## 10. Required sample sizes
См. §5 таблицу (A: 10–15, B: 30, C: 50–60, D: 30+OOS). Плюс: для ADX-решения — минимум 30 сделок с ADX-меткой в каждом плече (недостижимо за ~неделю — принять как долгий пассивный сбор); для cost/maker — 30+ сделок с фактическим slippage.

## 11. OOS validation plan
- P3: порядок по времени исполнения; последние 1/3 (или n≥10) = OOS-блок; sign test (H0: median Δ=0, p<0.1).
- ADX: день-by-day стабильность (≥50% дней с NET>0 в ADX<25 плече), period-concentration test (эффект не от одного дня).
- Walk-forward по 10 сделкам для P3, если n≥40.
- Каждый кандидат: TRAIN/VALIDATION/OOS фиксируются ДО запуска (без подгонки).

## 12. Counterfactual methodology
- **Один механизм = один counterfactual.** Запрещены комбинации: P3+ADX, P3+structural, P3+изменённый entry, P3+risk.
- CONTROL = фактическая сделка (guardian-ladder как есть). P3 = 33%@1R + trail 0.75R (live-shadow, консервативный M15-интрабар, SL-first, trail-lag 1). STRUCTURAL = close < anchor первых 2 баров (только для сравнения, REJECT).
- Все: qty = min(floor(0.50/|E−SL|), cap 20%×61); NET = gross − incremental fees − 2bps slip; единая экономика, единые данные, единые costs. No-look-ahead.
- Сравнение — по NET/PF/ExpR/MaxDD/worst/avgLoss/occupancy/MFE-capture, отдельно IS и OOS.

## 13. Production gate criteria
(10 проверок exit_shadow_report.py, все обязательны): n≥30; A NET>0; A NET>REAL; Exp>0; PF>1; не 1–2 сделки (top2<100%); нет доминирующего символа (top1<60%); ≥3 дня; реальные costs; median Δ>0. Плюс из §5: avgLoss ≤ ~$0.5, occupancy не хуже, OOS-блок положителен.

## 14. Kill criteria
- n=30: A NET≤0 ИЛИ A≤REAL ИЛИ median Δ≤0 → REJECT (exit-направление закрывается до нового решения)
- n=10–15: median Δ≤0 при ≥3 днях → снять PROMISING, наблюдать без действий
- top-1 ≥100% NET при n≥15 → выборка невалидна (концентрация), продолжить сбор
- ADX: если эффект исчезает при контроле holding/symbol/side → прокси, не фильтр
- Любой рост MaxDD/worst/avgLoss сверх контроля при сохранении Δ → взвесить риск-компенсацию

## 15. Exact next experiment
**ОДИН эксперимент: P3 live-shadow до n=30 (уже запущен, без изменений).**
- Ничего не менять в production/конфигах/сервисах.
- На каждой закрытой сделке shadow уже фиксирует: REAL NET, P3 NET, Δ NET, R, MFE, MAE, exit reason, holding, fees (ledger).
- Пассивно параллельно: crypto_funnel.py продолжает метить ADX (funnel-счётчик уже работает), чтобы к моменту P3-gate был готов ADX-слой на тех же сделках для прокси-декомпозиции.
- Контрольные точки: 10–15 (промежуточный) → 30 (gate) → по решению пользователя.

## 16. What must NOT be changed yet
- risk_per_trade $0.50, NOTIONAL_CAP 20%, leverage 5x, max_positions 4, max_same_side 1
- entry pipeline / scoring / thresholds / ADX-фильтр (не вводить) / gates / universe
- SL/TP генерация, correlation (внедрена), deposit guard, MARGIN_BUFFER $5
- production exit (guardian), P3-shadow профиль
- структурный exit, расширение SL, maker в production (только shadow-профили B/C в ledger)
- НИЧЕГО не деплоить, не рестартовать, R148 frozen.

---

## Итоговая декомпозиция для решения (коротко)

| Вопрос | Ответ |
|---|---|
| Что оптимизируем? | REALIZED NET после costs |
| Где теряются деньги? | exit-монетизация (−24.5R gap) + costs (13× NET) |
| Вход? | НЕ трогаем (движение есть, вход не доказанно-убыточен) |
| Размер? | KEEP (не bottleneck) |
| P3? | Единственный кандидат, live-shadow n=1, продолжается |
| ADX<25? | Сильный IS-сигнал, вероятен прокси — только пассивный OOS-сбор |
| Structural? | REJECT |
| Один следующий шаг? | Набрать P3-shadow до 30, ничего не меняя |
