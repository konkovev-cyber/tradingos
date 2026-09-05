# EXIT ENGINE v2 — RESEARCH SPEC

*RESEARCH / FORENSIC / PREPARE-ONLY. R148 = FROZEN. NO DEPLOY / NO RESTART / NO CONFIG CHANGES. Никакого кода в production. Никаких maker-on, никого изменения risk/cap/entry/guards/exit-logic. Это спецификация offline-falsification-исследования, а не implementation plan.*

---

## 5-line verdict

```
CURRENT BOTTLENECK:    giveback $9.89 (105% MFE) + costs $2.00 (13× NET). P3 live-shadow n=3, все ΔA>0, INSUFFICIENT SAMPLE.
ECONOMIC HYPOTHESIS:  hard SL остается catastrophic fallback; основная машина выхода переходит в state engine (LOSS/NEUTRAL/PROFIT/TRENDING), с adaptive giveback и time-decay. Цель: capture 10–20% MFE → NET.
DATA STATE:           34 закрытые AUTO CRYPTO сделки с M15 72h pутями (полные, в /tmp/exit_sim/dataset.pkl). Live-shadow n=3 с raw/REAL/P3/B/C.
FALSIFICATION:        спецификация deliberately сконструирована, чтобы попытаться сломать свою гипотезу. Высока вероятность, что COMBINED проиграет P3 (over-fit) и ADAPTIVE GIVEBACK проиграет P3 (MFE-cap <2R на 34 сделках вряд ли имеет сигнал). Запланированы REJECT-триггеры.
NEXT RESEARCH:        построить Exit Research Engine (read-only forensic), протестировать 5 кандидатов на 34 путях, отдать ОДИН: NEXT ACTION = RUN / WAIT / DROP.
```

---

## 1. Зачем этот spec

У тебя (пользователя) есть искомая идея: **не заменить SL, а превратить SL из основного exit-механизма в catastrophic fallback, заменив его на state-based exit engine**. До P3-PASS это **нельзя принимать как директиву** — потому что:

- Текущий P3 (33%@1R + trail 0.75R) сам является конкретной реализацией exit-engine-идеи. Если user-идея «COMBINED = P3 + structural + adaptive + time-decay» покажет лучший результат, что тогда с P3? shadow-evidence, единая гипотеза.
- Если «COMBINED» проиграет P3 на 34 путях — это информативно; если выиграет — смотрим устойчивость.
- Делать заранее COMBINED без falsification — подмена существующего shadow-эксперимента.

**Spec начинается с десакрализации гипотезы**: что COMBINED ≠ winner. Может случиться, что ни ADAPTIVE, ни STRUCTURAL не работает лучше P3 — это совершенно допустимый исход.

## 2. Lessons from prior work (важно)

Прошлые сессии (`ses_0786ab6f9`, `ses_08b5fa434`, `ses_023d80300`) уже проектировали Profit State Machine / Trade Engine v2 / PIE v2. Lessons:

- **R148 (08-08/2026):** всё, что давало delta ≤ 0 или ухудшало Net, REJECTED. Никаких переключений triple-state-machine.
- **Structural exit (08-17, my forensic):** REJECT — top-2 = 118% NET, параметрическая чувствительность (confirm 0→1 рушит +$1.69 → −$6.83). Не повторять в новой форме без сильного-доказательства.
- **Adaptive giveback 0.25R (08-17, my forensic):** FRAGILE — 89% от 1 сделки. Любая «adaptive giveback» гипотеза проверяется на robustness к параметрам.
- **Time-decay 4-12h (08-14, my forensic):** IS +$3.76, but integration with P3 partial не тестирована.
- **P3 (live-shadow, n=3):** active, не трогать.

**Сознательное последствие:** этот spec тестирует идеи поверх существующих данных, но **он также может показать, что P3 лучше, чем вся его продукция** — тогда user-идея REJECTED. Это допустимый и валидный результат.

## 3. Current Bottleneck (факты)

| Копмонент | Потери $ | Доля | Evidence |
|---|---|---|---|
| Available MFE | $9.38 | 100% | 34 сделки |
| Realized capture | $0.15 | 1.6% | 34 сделки |
| Giveback | $9.89 | 105% MFE | 14/34 достигли +1R, 6/14 не удержали |
| Costs | $2.00 | 13× NET | 34 сделки |
| Funding | ~$0 | negligible | факт |

**Bottleneck = exit-capture** (giveback) + costs. **НЕ вход** (MFE 2.17R median есть).

## 4. Economic Hypothesis

> State-based Exit Engine (LOSS/NEUTRAL/PROFIT/TRENDING state machine, output = structural / time-decay / partial-with-adaptive-giveback) захватывает больше доступного MFE, чем текущий guardian-stack + P3, при НЕ увеличивающемся DD и при неизменном риске ($0.50/trade).

**Механизм:** дать runner'у дышать (избегать early-BE), выходить по структуре движения, адаптировать giveback шириной к достигнутому MFE, выходить по time-decay, если цена не идёт.

**Это экономически другая идея, чем P3:** P3 — фиксированный трейл после +1R частичной фиксации. COMBINED — adaptation *до* +1R тоже.

## 5. Exit-State Architecture (spec, no code yet)

```
ENTRY
  ↓
INITIAL RISK (catastrophic boundary: -X R, configured, always on)
  ↓
POSITION STATE ENGINE
  │
  ├─ LOSS      (MFE < 0.3R) → HOLD, watch structure (LONG: last swing low)
  ├─ NEUTRAL   (0.3R ≤ MFE < 1R) → no protection, watch structure
  ├─ PROFIT    (1R ≤ MFE < 2R) → activate partial + structure-exit
  ├─ TRENDING  (MFE ≥ 2R) → tighten structure-giveback, protect
  ├─ DECAY     (age > 12h AND MFE-flat AND structure-deterior) → forced EXEC
  ↓
CATASTROPHIC FALLBACK (always-on, НЕ зависит от state)
```

## 6. Точные определения кандидатов (parameter bands)

Все «R» ниже — relative to |entry − initial SL| на 1R. **Все candidate-simulations применяют identical no-look-ahead правила** в post-hoc-симуляции на 34 данных.

### A) REAL (control)
Текущая production exit: guardian-ladder (BE 0.8R / PARTIAL 1R / TIGHT 1.5R / TRAIL 0.5R) + факт. ruin-related manual closes. **Не симулируется моделью: берём as-is из lifecycle.**

### B) P3 (current live-shadow)
- Фиксированный partial @ +1R: 33% take.
- Остаток: trailing 0.75R за экстремумом.
- TP остаётся.
- Catastrophic 3R.
- (Live-используется в shadow-engine; наш forensic — strict M15-симуляция.)

### C) STRUCTURAL EXIT
- LONG: выход при close < min low первых 2 баров (anchor повторно опустошён).
- SHорт: зеркально.
- TP остаётся.
- Catastrophic 3R.
- НЕТ частичной фиксации — либо full, либо ничего (на выходе runner).
- **Band:** anchor window — {1, 2, 3} баров (parallel sub-candidates).

### D) TIME-DECAY EXIT
- Условия выхода (как полный, не частично):
  - `age > 12h` AND
  - `MFE-flat` (последние 3 бара не расширяют MFE ≥ 0.05R) AND
  - `structure-deterior` (close пробивает anchor downward для LONG, upward для SHORT)
- TP остаётся.
- Catastrophic 3R.
- **Band:** age threshold {8h, 12h, 24h}, MFE-flat window {2, 3, 5}.

### E) ADAPTIVE GIVEBACK
- Структура/капитал FULL-mode (или near-FULL).
- Разрешённый giveback = функция MFE:
  - MFE < 1R: no protection (giveback = 0.5R allowed)
  - 1R ≤ MFE < 1.5R: giveback 0.5R
  - 1.5R ≤ MFE < 2.5R: giveback 0.3R
  - MFE ≥ 2.5R: giveback 0.15R
- TP остаётся.
- Catastrophic 3R.
- **Band:** giveback levels {0.5/0.3/0.15} (control), {0.7/0.4/0.2}, {0.4/0.25/0.10} — проверка robustness.

### F) COMBINED EXIT ENGINE
- D + E + partial @ +1R (P3-style) + structural:
  - Partial @ +1R (33%) → FIXED
  - Runner: `max(structural, adaptive_giveback, time_decay)` — выход по первому сработавшему
  - TP остаётся
  - Catastrophic 3R
- **Band:** combine-flag пробует: (structural, adaptive, time_decay) — три варианта.

## 7. Применимый набор правил (no-look-ahead)

Все кандидаты используют **identical** следующие инварианты:

- **Catastrophic boundary:** всегда 3R от initial SL (БОЛЬШЕ жёсткого, чем P3/TIGHT для теста). Test: catastrophic не срабатывает чаще, чем на одном тестовом наборе.
- **TP unchanged:** фиксация выход при касании исходного TP.
- **Conservative intrabar:** на одном баре с двойным касанием — SL-сторона побеждает (worst-case).
- **No future data:** только бары, существовавшие до решения.
- **Financing:** effective cost = 0.055% taker × 2 + 2bps slippage на выходе (conservative: actual slip на 20bps, но model-strategy пока использует 2bps для over-fit-suppression).
- **Spread:** не используется (DATA GAP).

## 8. Metric-карта

| Метрика | Что |
|---|---|
| **NET** | gross − fees − slippage (per trade, в $) |
| `gross` | NOT `gross` после costs; «gross improvement» только как компонент |
| fees | 0.055% per side × 2 на notional |
| slippage | 2bps × notional на выход |
| MFE | max favorable Р from entry to exit |
| MAE | max adverse R |
| realized R | NET / |entry − initial SL| / qty |
| **MFE capture** | realized R / MFE (если MFE > 0) |
| **giveback** | (MFE − realized R, если MFE > realized R) |
| holding time | (idx_close − idx_entry) × 15 min |
| **occupancy** | avg holding × max_pos ≈ avg capital-time |
| **anti-cherry** | Δ без best / worst / top-1-symbol |
| **Top-1 contribution** | share of NET from one entry |
| **OOS** | last 1/3 by entry_ts |
| bootstrap CI | 5000-iteration median Δ NET |

## 9. IS/OOS plan

- 34 → 23 train + 11 OOS (по entry_ts).
- Optionally:
  - Rolling 10-window walk-forward if n≥30.
- Anti-cherry-pick обязателен.
- Adverse-claim: «после удаления лучшей сделки NET становится <5% → FRAGILE».

## 10. Failure modes / kill criteria

| Kill | Trigger |
|---|---|
| `<`17 сделок | INSUFFICIENT SAMPLE |
| median ΔNET ≤ 0 при n≥30 | REJECT |
| OOS not confirming direction | REJECT |
| Top-1 symbol share ≥ 40% | FRAGILE |
| Median ΔNET без лучшей сделки <5% of baseline | FRAGILE |
| Sensitivity > 50% flip от median across bands | REJECT |
| PF < 1.0 на OOS | REJECT |
| Confirmed edge appears только на одном symbol/regime | REJECT |
| Capture max in 1 symbol, 1 side | REJECT |

## 11. Comparison table (target output)

| Exit | NET | PF | ExpR | WR | MFE cap | giveback | holding | top-1 | OOS | band |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A) REAL | | | | | | | | | | |
| B) P3 | | | | | | | | | | |
| C) STRUCTURAL | | | | | | | | | | |
| D) TIME-DECAY | | | | | | | | | | |
| E) ADAPTIVE | | | | | | | | | | |
| F) COMBINED | | | | | | | | | | |

Каждая строка — aggregate по 34 сделкам. C / D / E / F показывают ОБА band-median и ±1 band-sensitivity.

## 12. Cost-aware (non-negotiable)

ΔNET = gross-impact − additional-cost. Если Δ до costs выглядит хорошо, а после costs — уходит в ноль или ниже, REJECT. (Output: отдельные строки для NET before costs, NET after fees, NET after fees+slippage.)

## 13. P3-shadow vs COMBINED: или независимое shadow

**Не подменяем P3.** COMBINED — свежий кандидат, может либо превзойти P3, либо проиграть. Если паритет или проигрыш — документируем P3 как live winner, COMBINED = REJECT.

Live-shadow generator остается P3. COMBINED будет проверяться **offline** (read-only forensic на 34 сделках). Если COMBINED PROMISING → может быть запущен НЕ как live-shadow, а как параллельный read-only counter-factual (по подобию EXIT_MECHANICS_FORENSIC.md).

## 14. Entry etc. не трогаем

Прямо зафиксировано: entry-score, threshold, ADX, gates, correlation, universe, risk, cap — не менять. **Эта работа только об exit-механике.**

## 15. Что явно НЕ делать

- Не внедрять ничего, даже P3.
- Не запускать maker.
- Не расширять SL.
- Не менять ADX.
- Не убирать catastrophic fallback.
- Не менять risk/cap.
- Не подгонять параметры на R148 corpus.
- Не использовать hindsight в симуляции.

## 16. Exact next experiment (если этот spec acknowledged)

`research/exit_engine_v2/` — read-only forensic simulator:
- Скрипт `sim_exit_grid.py` — перебирает кандидаты A/B/C/D/E/F на 34 путях.
- Строит таблицу по §11.
- Ищет REJECT-триггеры до того, как выйти с выводом.
- **Не использует live data; только dataset.pkl.**

Evaluation ОДИН проход. Если МОЯ собственная гипотеза что «COMBINED ≈ P3 ± sensor noise» — будет зафиксировано.

---

## NEXT ACTION = **WAIT FOR USER (spec reviewed)**

Обоснование: spec consciously сделан, чтобы **сломать свою гипотезу** (COMBINED ≠ production winner). У тебя (пользователя) должен быть шанс:
- (a) утвердить spec → начать `research/exit_engine_v2/sim_exit_grid.py` read-only forensic → отчёт → decision.
- (b) изменить spec (different bands, different combinations, different exit score, addition of facets).
- (c) отклонить (если уже считаешь, что P3 live-shadow n=3 — достаточная aggregation и не нужно новых кандидатов).

Production: FROZEN. P3-shadow: продолжается. Ничего не деплою.
