# MAKER SHADOW GATE — TradingOS

*PREPARE ONLY. R148 FROZEN. P3 live-shadow продолжается без изменений. Никакого maker в production. Документ определяет строгий gate для гипотезы maker-исполнения и честно фиксирует, что текущий ledger может и НЕ может измерить.*

---

```
CURRENT STATUS:            Production FROZEN. Cost drag = 13× NET (fees $0.99 + slip $1.00
                           на NET $0.15 / 34 сделки). Maker — гипотеза №2 после P3.
P3 STATUS:                 live-shadow n=3 (TUT +0.271, XAN +0.022, XAI +0.008;
                           все Δ>0, INSUFFICIENT SAMPLE). Gate на n=30.
MAKER STATUS:              НЕ запущен. В ledger есть только naive fee-оценка профилей B/C
                           (верхняя грань, см. §4). Реального измерения НЕТ — DATA GAP.
MAIN HYPOTHESIS:           PostOnly/maker-вход снижает fee+slippage drag настолько,
                           что NET после всех costs растёт — БЕЗ ухудшения fill rate,
                           missed entries и adverse selection.
MINIMUM ECONOMIC EFFECT:   ΔNET ≥ +$0.02/сделка на текущем капитале — иначе
                           ECONOMICALLY IMMATERIAL. Фактическая fee-экономия сегодня:
                           +$0.004–0.006/сделка → НИЖЕ ПОРОГА (см. §12).
NEXT ACTION:               WAIT FOR P3 (обоснование в §12–14).
```

---

## 1. Executive Verdict

Maker — правильная гипотеза №2 по направлению (cost drag 13× NET — второй по величине после giveback), но:

1. **На текущем капитале чистая fee-экономия = $0.004–0.006/сделка** (ΔB из 3 реальных записей ledger) — по критерию §12 это **ECONOMICALLY IMMATERIAL**.
2. **Текущие профили B/C в ledger — НЕ измерение maker**, а арифметическая верхняя грань: `b_net = real_net + (taker−maker)×notional`. Они предполагают 100% fill, тот же вход, ноль missed entries, ноль adverse selection. Реальные риски maker могут съесть экономию с избытком.
3. Честный maker-shadow требует измерения fill/missed/adverse-selection — этих данных в ledger НЕТ (§6). Такой эксперимент = изменение order type = production change = отдельный approval.

**Вывод: maker становится значимым только после масштабирования (P3 PASS → cap 30%/$0.75 → капитал). Готовить gate — сейчас; запускать — после P3-решения.**

## 2. Hypothesis

> PostOnly/maker execution снижает fee/slippage drag настолько, что увеличивает NET PnL после всех costs, не ухудшая fill quality, missed-entry rate, adverse selection и risk.

Механизм: taker 0.055% → maker 0.020% за сторону; потенциально лучший entry (limit vs market+slippage). Против: неполное/нулевое исполнение на движении (лучшие входы уходят без нас), adverse selection (fill приходит чаще, когда цена идёт против).

## 3. Control

**Control = REAL current execution** (taker market, фактические entries/exits из trade_results). Сравнение строго **paired**: один и тот же сигнал, один и тот же exit-путь, разница ТОЛЬКО в исполнении входа. Запрещено сравнивать «maker-сделки vs обычные сделки» (selection bias).

## 4. Maker Candidate — что реально есть сейчас

Профили B/C в `exit_shadow_ledger.jsonl`:
```
b_net = real_net + (FEE_TAKER − FEE_MAKER) × notional   # B: текущий exit + maker-вход
c_net = a_net   + (FEE_TAKER − FEE_MAKER) × notional   # C: P3-путь + maker-вход
```
Это **fee-delta, не исполнение**: 100% fill, вход по той же цене, ноль задержки. Реальные ΔB из ledger: **+$0.0042, +$0.0057, +$0.0057** (3 сделки). Отсутствующие поля: signal_ts, intended_entry, actual_entry, fill_probability, time_to_fill, missed, partial_fill, cancel, spread, depth — **ВСЁ ОТСУТСТВУЕТ → DATA GAP**.

## 5. Paired Comparison Method

Для каждой AUTO-сделки (только те же входы, что в production):
- REAL: фактический taker-вход (цена, время, fees, slippage) + фактический exit.
- MAKER: гипотетический PostOnly-вход, размещённый в момент сигнала по цене bid/ask (или signal-price − offset), с моделью исполнения (§6), затем ТОТ ЖЕ exit-контур.
- ΔNET = NET_maker − NET_real на paired-паре. Отдельно: Δfees, Δslippage, Δfill-rate, ΔMFE/MAE (adverse selection), Δholding, Δoccupancy.

## 6. Fill / Missed Entry Model (чего нет и что нужно)

Модель исполнения PostOnly (консервативная, no look-ahead):
- **Fill**: только если цена ПРОШЛА СКВОЗЬ limit-уровень (trade-through), не касание (queue-position консерватизм).
- **Missed**: ордер не исполнен за окно T (варианты: 1×/3×/5× сигнального интервала) → cancel; сигнал считается пропущенным.
- **Partial fill**: исполнена доля q → вход q, остальное missed.
- Для каждого missed: subsequent price path → hypothetical PnL if filled (taker-fallback) → **opportunity cost**.
- Итог: `NET_EFFECT = fee_savings + entry_improvement − opportunity_cost`.

Источники для модели: M15 klines (грубо, консервативно), microstructure-снапшоты ~30с (15 символов — частичное покрытие). Честная альтернатива — микро live-тест с minNotional ($5) на 10–20 сделок: это production change → отдельный approval.

## 7. Fee / Slippage Model

- Fee: maker 0.020% / taker 0.055% за сторону (текущие значения в exit_shadow_engine).
- Slippage: фактический taker-slippage из trade_results (медиана 20bps!) против maker 0 (вход в книгу) + возможный хуже-уровень при partial.
- Считать НЕ «fee saving», а ΔNET целиком (§5).

## 8. Adverse Selection (главный риск ложного вывода)

Гипотеза-ловушка: «fee меньше → maker лучше». Проверка обязательна:
- Сравнить последующий MFE/MAE распределение: maker-fills vs taker-fills на paired-сигналах.
- Если maker исполняется преимущественно на сигналах, где цена сразу идёт против (наш лимит «ловит» падающий нож), MFE_maker < MFE_taker систематически → adverse selection подтверждён.
- Метрика: median ΔMFE(paired) и доля maker-fills, где последующее движение против > 0.5R.

## 9. Liquidity Segmentation

Разбивка эффекта: symbol / spread(bps) / volume24h / volatility / side / time-of-day / regime. Ожидание из данных: thin small-cap (вся текущая выборка, медианный спред ~6-20bps) — худшее место для PostOnly. Определить, где maker в принципе имеет смысл; фильтр НЕ создавать автоматически.

## 10. Sample Requirements

- **INTERIM: n≥10–15** качественных paired-наблюдений (fill-модель откалибрована).
- **FULL: n≥30.** **CONFIRMATION: n≥50–60.**
- 30 сделок одного символа ≠ доказательство: минимум 5 символов, ни один >40% выборки.
- Текущий эквивалент в ledger (B/C) годится ТОЛЬКО для upper-bound оценки, НЕ для gate.

## 11. Anti-Cherry-Pick

Полный набор: remove best / remove worst / remove both / top-symbol <30% Δ / side <70% Δ / time-concentration / chronological OOS (последние 1/3) / bootstrap 95% CI median ΔNET. Исчез эффект после удаления одной сделки → **FRAGILE**, не PRODUCTION CANDIDATE.

## 12. Денежный порог — ЧЕСТНЫЙ РАСЧЁТ

Фактические ΔB: **$0.004–0.006/сделку** (notional $12–16, вход-only). Даже maker на вход+выход: ~$0.011/сделку. При 5–7 сделках/день: **$0.02–0.08/день**.

**По критерию задачи ($0.003–0.01/trade = immaterial): текущий maker-эффект ECONOMICALLY IMMATERIAL на капитале $61 / cap 20%.**

Масштабирование порога: экономия линейна notional →
| Капитал | notional/сделка | Δ maker/сделка (вход+выход) | Вердикт |
|---|---|---|---|
| $61 (сейчас) | ~$15 | ~$0.011 | immaterial |
| $61 + P3 + cap30%/$0.75 | ~$20–25 | ~$0.015–0.018 | на границе |
| $200 | ~$60 | ~$0.042 | material |
| $500 | ~$150 | ~$0.105 | material |

Плюс риск: ОДИН пропущенный хороший вход (P3-capture ~$0.15–0.30 NET) стирает 15–60 сделок fee-экономии. Знак истинного эффекта при тонких монетах **неизвестен** до fill-модели.

## 13. PASS/FAIL Gate (иерархический)

**REJECT**, если (на paired-выборке): median или mean ΔNET ≤ 0 | opportunity cost > fee-экономии | adverse selection существенен (median ΔMFE < 0 значимо) | fill-rate неприемлемо низок (<60–70%, порог уточнить по данным) | OOS не подтверждает | эффект на 1 символе/режиме | MaxDD хуже.

**PROMISING**: ΔNET>0, fee/slip-снижение подтверждено, missed-cost приемлем, adverse selection не ухудшился, OOS направленно подтверждает, НО n<30 или концентрация на грани.

**PRODUCTION CANDIDATE**: n≥30 + OOS PASS + anti-cherry-pick PASS + ΔNET ≥ порога §12 + не сконцентрирован + понятный механизм. Деплой — только по отдельному approval.

## 14. Exact Shadow Protocol (когда будет запускаться — после P3-решения)

Вариант A (пассивный, без production-изменений): для каждого нового AUTO-сигнала логировать spread/depth в момент сигнала + моделировать PostOnly-fill по последующему пути (trade-through, окна 15/45/90 мин) + taker-fallback opportunity cost. Требует: расширение движка exit_shadow (второй параллельный счётчик, как P3) — кодовая правка research-инструмента, НЕ production.
Вариант B (активный микро-live): PostOnly на minNotional $5 на 10–20 сделок для калибровки fill-модели — production change, отдельный approval.
Рекомендация: A после P3-gate; B только если A неоднозначен.

## 15. Production Safety

R148 FROZEN. P3 не трогать. Maker не запускать. Никаких изменений order type. P3 и maker — независимые гипотезы: итоговая таблица REAL / P3 / MAKER отдельно; «P3+maker = победитель» запрещено до раздельного PASS каждого. Композиция — только после двух независимых gate + approval.

---

## NEXT ACTION = WAIT FOR P3

Обоснование: (а) maker-эффект на текущем капитале ниже денежного порога (§12) — независимо от исхода P3 он не станет значимым без масштабирования; (б) честное измерение maker требует fill-модели, которой нет (DATA GAP §6) — её разработка до P3-решения преждевременна; (в) P3 (n=3, все Δ>0) — активный эксперимент, которому нельзя мешать. Gate-документ готов; когда P3 дойдёт до n=30 и будет принято решение — вернуться к §14 (вариант A) без потери времени.
