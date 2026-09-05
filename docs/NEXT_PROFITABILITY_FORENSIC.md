# NEXT PROFITABILITY FORENSIC — TradingOS

*FORENSIC / RESEARCH ONLY. R148 = FROZEN. NO DEPLOY. NO RESTART. NO CHANGES to risk/cap/entry/ADX/SL/TP/P3/guards/correlation. Данные: 34 реальные закрытые AUTO CRYPTO сделки (/tmp/exit_sim/dataset.pkl, M15 path 72h, факт. costs).*

---

## 5-line verdict

```
CURRENT BOTTLENECK:  exit-capture: доступный MFE $9.38, реализовано $0.15 (1.6%),
                     giveback $9.89 (105% MFE). Costs $2.00 — вторичны (но значимы).
BEST CURRENT LEVER:  усовершенствованный exit (частичная фиксация + трейл) —
                     консервативно take@+1R + trail даёт до ~$1.8-3 с costs, НЕ за счёт риска.
EXPECTED NET IMPACT: +$1.5…$3.0 на 34 сделках при risk $0.50 (немасштабируемо по абсолюту:
                     это $0.04-0.09/сделку; на $61 капитале ≈ +1-3%/мес при ~10-15 сделках/мес).
CONFIDENCE:          средняя. giveback-механика подтверждена (MFE ≥1R у 14/34, giveback 6/14),
                     НО лучший найденный трейл (0.25R) FRAGILE (89% от 1 сделки);
                     устойчивый целевой репрофиль — take@+1R/trail≥0.5R (~+$1.8).
NEXT ACTION:         продолжить P3 live-shadow до n=30 (единственный разрешённый live);
                     offline — сравнить P3-профиль с take@+0.75R/trail0.5R НЕ как новые
                     параметры, а как robustness-зону для gate.
```

---

## 1. Executive Verdict

**Подтверждено фальсификацией:** bottleneck НЕ вход (движение $9.38 появляется) и НЕ размер (cap-форезн: NET падает с ростом cap), а **exit-capture**: реализуется 1.6% доступного движения, giveback $9.89. Это самый большой потерянный компонент (порядка $9.9 против $2.0 costs).

**Честная оговорка:** из $9.89 giveback реализуем НЕ весь (нельзя поймать пик без look-ahead). Консервативная оценка достижимого — ~$1.5-3.0 на 34 сделках (и то с риском overfitting, см. §7). **Абсолютные суммы малы при капитале $61** — это +$0.04-0.09/сделку; «большой рычаг 2×» возможен ТОЛЬКО через композицию (exit × удержание дольше × maker), а не одной правкой.

**Значимый вывод:** больший # не находится в размере/входе. Единственный направленный рычаг — **лучший capture из уже существующего движения**, и он ограничен реалистичными пределами (не $9.9).

## 2. Current NET Bottleneck (аудит)

| Компонент | gross/impact | lost $ | evidence | confidence |
|---|---|---|---|---|
| Available MFE | +$9.38 | — | 34 сделки | high |
| Realized capture | +$0.15 | $0.15 → | 34 сделки | high |
| **Giveback (отдано)** | — | **$9.89 (105% MFE)** | 14/34 достигли ≥1R, 6/14 не удержали | high |
| Costs (fees+slippage) | — | $2.00 | 34 сделки | high |
| Funding | ≈$0 | ~$0 | факт | mid |

**Bottleneck = exit-capture (giveback).** Costs вторичны, но $2.00 на $0.15 NET — существенны (13×). Размер — KEEP (не bottleneck).

## 3. P3 Status

P3 live-shadow: **n=1 финализированная** (TUTUSDT REAL +$0.697 / P3 +$0.968 / Δ+$0.271). INSUFFICIENT SAMPLE. XANUSDT, XAIUSDT открыты. **Не делать выводов о победе/поражении из n=1.**

## 4. P3 Evidence (offline, 34 сделки, консервативный M15-симулятор с trail-lag/SL-first)

- P3 (take@+1R, trail 0.75): **NET +$1.21** (факт +$0.155; чистый SL/TP −$0.04)
- Δ(A−REAL) > 0 в 23/34 сделок, median Δ +$0.017, mean +$0.031
- MFE capture ↑, giveback ↓ (6/14 дали back → P3 фиксирует 33%@+1R)
- TRAIN +$0.20 / OOS +$1.63; top-1 ≈ 51% NET (допустимо)
- **Вывод: P3 подтверждает механизм giveback-контроля offline. Live — ждёт n=30.**

## 5. Candidate Mechanisms (по классам задачи)

| Класс | Кандидат | Механизм | NET Δ (оффлайн) | Robustness | Вердикт |
|---|---|---|---|---|---|
| A. Exit | **take@+1R + trail** (P3) | partial устраняет giveback | +$1.21-1.80 | mid (top-1 51%) | **STRONG (live идёт)** |
| A. Exit | take@+0.75R / trail 0.5R | фиксация раньше | +$0.30-1.79 | mid | WEAK-ПРОМИСИНГ (чувствителен) |
| A. Exit | take@+1R / trail 0.25R | агрессивный трейл | +$3.05 | **FRAGILE (89% от 1 сделки)** | REJECT |
| A. Exit | structural exit | close<anchor | +$1.69 IS | FRAGILE (top2 118%, confirm 0→1 рушит) | REJECT |
| A. Exit | расширение SL / catastrophic | — | — | SL не режет (11/12 уходили ≥1R против) | REJECT |
| B. Entry | ADX<25 | режим | +$2.69 IS | OOS нет | PROMISING (пассивный OOS, не фильтр) |
| C. Cost | maker entry | fee ↓ | +$0.67 (модель) | не live | PROMISING (после P3) |
| D. Capital | удержание 4-12ч | occupancy | +$3.76 IS | корреляция, OOS нет | WEAK (прокси exit) |
| E. Selection | SELL/RSI60+/thin | убыточные подгруппы | — | OOS нет | WEAK (после OOS) |

## 6. Cost Forensic

- Costs $2.00 на NET $0.15 (13×). Медианный slippage 20bps (не 2bps). One-way fees 8-30bps.
- **Вклад costs:** даже при идеальном capture $2.00 из $9.38 — реально съедаемая часть; важна, но вторична к giveback.
- Maker-экономия ≈ $0.67/34 — отдельный небольшой рычаг, масштабируемый только при масштабе.

## 7. Order-vs-SL Forensic

**Гипотеза «заменить SL условным/лимитным ордером» — НЕ подтверждается как улучшение.**
- SL market: высокая вероятность выхода, цена может быть хуже стопа.
- stop-limit: риск **неисполнения** при проскоке рынка (gap).
- SL-after-exit forensic: 11/12 SL-сделок после выхода уходили ещё ≥1R против → SL был **правильным**, а не преждевременным.
- **Вывод: НЕ менять SL на conditional/limit. Catastrophic boundary обязателен.** Расширение SL не даёт edge. Только улучшение capture НА ПРИБЫЛЬНЫХ позициях (P3) имеет смысл.

## 8. Capital Efficiency

- 4-12ч сделки: +$3.76/12 (единственный NET-позитив); <4ч: −$3.35/20.
- Ранние закрытия (<4ч) занимают слоты впустую и теряют движение.
- NET/occupied-hour: baseline $0.001/ч vs P3-профиль $0.005-0.009/ч.
- **Не размер, а удержание правильных сделок** — компонент больших рычагов.

## 9. Symbol/Market Selection

- Small-cap 100% выборки, slippage 20bps. Проблема в тонкости.
- Убыточные подгруппы: SELL (n=19 −$0.52), ADX≥25 (n=17 −$2.53), RSI≥60, thin (VET-тип).
- **Не менять universe/фильтры**; собирать OOS-данные для этих меток параллельно P3.

## 10. OOS / Robustness

- P3: TRAIN +$0.20 / OOS +$1.63 (подтверждает направление, OOS n=11 слаб).
- ADX<25: только IS (+$2.69) — OOS нет, вероятен прокси (holding/symbol/side).
- Агрессивный трейл 0.25R: **FRAGILE** (без лучшей сделки NET +$0.32, 11% сохранение) → не рассматривать.
- Правило: ни один кандидат не считается подтверждённым без OOS и anti-cherry-pick.

## 11. Candidate Ranking

| Rank | Candidate | n | NET Δ | OOS | MaxDD | Robustness | Economic reason | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | **P3 (take@+1R, trail 0.75R)** | 34+live | +$1.21-1.80 | +$0.73-1.16 | лучше | mid (top-1 51%) | фиксирует giveback | **STRONG — live идёт** |
| 2 | ADX<25 режим-метка | 17/17 IS | +$2.69 IS | н/д | — | OOS нет, вероятен прокси | конц. edge в низкотрендовом | PROMISING |
| 3 | maker entry | 34 | +$0.67 | н/д | — | модель | fee-снижение | PROMISING |
| 4 | take@+0.75/trail0.5 | 34 | +$0.30-1.79 | н/д | — | чувствителен | запасной exit-профиль | WEAK |
| — | structural / SL-рашир / трейл0.25 | — | — | — | — | FRAGILE | overfit | REJECT |

## 12. Rejected Hypotheses

- **Расширение SL / убрать SL**: SL корректен (11/12 уходили против); catastrophic обязателен.
- **Structural exit**: REJECT (top2=118%, confirm 0→1 рушит).
- **Агрессивный трейл 0.25R**: REJECT (89% от 1 сделки — overfit).
- **Увеличение risk/cap**: KEEP 20%/$0.50 (не bottleneck; масштабирует убыток убыточного контура).
- **ADX-фильтр сейчас**: не вводить (нет OOS, вероятен прокси).
- **Order-vs-SL (conditional/limit)**: не менять SL.

## 13. Best Next Experiment

**Продолжить P3 live-shadow до n=30 — уже запущен, без изменений.** Это единственный разрешённый live-эксперимент и он прямо нацелен на главный bottleneck (giveback).

## 14. Exact Experiment Spec (P3 gate, иерархический)

- **REJECT**: median Δ≤0 | P3≤REAL | anti-cherry-pick падает | costs съедают | MaxDD хуже | OOS не подтверждает | механизм MFE→capture→giveback не виден.
- **PROMISING**: median Δ>0, P3>REAL, MFE capture↑, giveback↓, MaxDD ок, не 1 сделка.
- **PRODUCTION CANDIDATE**: n≥30 + PROMISING + концентрация PASS + OOS PASS + costs PASS + occupancy ок + экономический механизм. Production — по отдельному approval.
- Инструмент готов: exit_shadow_report.py (bootstrap CI, anti-cherry-pick, OOS, концентрация, MFE/giveback).
- **Robustness-зона в gate:** сравнить P3-профиль с take@+0.75R/trail0.5R на тех же сделках — УБЕДИТЬСЯ, что Δ держится в зоне параметров (не пик), чтобы исключить overfit на trail 0.75.

## 15. Production Safety

- R148 FROZEN. Никаких изменений до P3-gate + отдельного approval.
- NO DEPLOY, NO RESTART.
- Нет большого одиночного рычага, дающего 2× NET без 2× риска на этой выборке. Наибольший стабильный — P3 (≈1.5-2× факта, $1.2-1.8/34) + последующая композиция (P3 × удержание 4-12ч × maker).
- Если после n=30 P3 не подтвердится — REJECT, без подгонки; дальше пересмотр (ADX-cиз, maker) по отдельному решению.

---

## NEXT ACTION

> Продолжить P3 live-shadow до n=30 (ничего не менять); параллельно — в отчёте при n=10-15 и n=30 проверять robustness-зону P3 против take@+0.75R/trail0.5R и против факта, чтобы отделить устойчивый capture-выигрыш от overfitted-трейла. Абсолютный «большой рычаг» на $61-капитале отсутствует в этой выборке; единственный путь к заметному NET — подтвердить P3, затем композиция P3 + удержание 4-12ч + maker, каждый шаг через отдельный gate.
