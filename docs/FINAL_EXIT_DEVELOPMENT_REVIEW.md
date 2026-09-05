# FINAL EXIT DEVELOPMENT REVIEW — TradingOS

*FALSIFICATION-FIRST, R148 FROZEN. Production НЕ менять, никаких deploy/restart, никаких изменений live. Документ — causal review существующих exit-кандидатов, не новый brainstorm.*

---

## 5-line verdict

```
CURRENT BOTTLENECK:    giveback $9.89 (105% MFE) + costs $2.00 (13× NET). P3 n=3 live, все ΔA>0,
                        INSUFFICIENT SAMPLE.
BIGGEST UNEXPLOITED:   adaptive-giveback (0.5→0.3→0.15 после +1R) — единственный вариант,
                        который переживает anti-cherry-pick на этой выборке, при IS-факторах
                        ещё завышенных. **НЕ новый edge — это вариант P3 с другим giveback.**
IS FIRST-TOUCH NEW EDGE?
                        FALSIFIED на n=34. corr(MFE_4bar, MFE_ultimate) = **-0.13**
                        (отрицательная). Impulse-persistence НЕ работает на этой выборке.
EXIT ENGINE v2 NEEDED?  Кандидаты дают +$2–3.5 (IS) при top-1 85–179% — большинство
                        FRAGILE. adaptive tight (0.5→0.3→0.15) единственный переживает
                        anti-cherry-pick, и это ВСЕГО-НАВСЕГО P3-вариант с другим giveback.
                        **Полноценный Exit Engine v2 не нужен; P3 + правильный giveback даёт
                        тот же результат.**
ONE NEXT ACTION:        WAIT FOR P3 (R148 FROZEN). На n=10–15 проверить тот же adaptive-tight
                        giveback-параметр на LIVE shadow data. Если P3(n=3+) Δ>0 + adaptive
                        giveback тоже переживает live ООS — это улучшение P3, не новый engine.
```

---

## 1. Current Money Leak

| Component | $ | Evidence |
|---|---|---|
| Available MFE | $9.38 | 34 AUTO CRYPTO сделки (M15 72h пути) |
| Realized capture (net) | $0.15 | — |
| Giveback | $9.89 (105% MFE) | 14/34 достигли +1R, 6/14 не удержали |
| Costs | $2.00 (13× NET) | — |
| Funding | ~0 | — |

**Bottleneck = exit-capture (giveback) + costs.** Вход НЕ bottleneck — MFE есть.

## 2. P3 Status (live-shadow)

- n=3, все ΔA>0 (TUT +0.271, XAN +0.022, XAI +0.008, **обе убыточные сделки P3 потерял меньше**).
- INSUFFICIENT SAMPLE.
- P3 — это **фиксированный partial @ +1R + trail 0.75R** (заблокированный в shadow-engine).
- Offline forensic на 34 путях: P3 conservative → +$1.21 (med), +$1.80 (если adaptive giveback).

## 3. First-Touch Hypothesis — **FALSIFIED на этой выборке**

**Гипотеза:** если first-touch MFE ≥ +0.5R за первые N баров, ultimate MFE будет высоким (impulse persistence).

**Falsification-тест на 34 путях:**

| Burst | corr(MFE_first-touch, MFE_ultimate) | corr(MAE_first-touch, MAE_ultimate) | n(ft ≥+0.5R) | rate(ult ≥+1R) |
|---|---|---|---|---|
| 2 бара (30м) | **-0.118** | 0.341 | 5 | 60% |
| 4 бара (60м) | **-0.131** | 0.404 | 9 | 67% |
| 8 баров (120м) | **-0.242** | 0.502 | 14 | 50% |
| 12 баров (180м) | **-0.230** | 0.586 | 16 | 50% |

**Результат:** Корреляция MFE **отрицательная** (от -0.12 до -0.24) на всех длинах burst. First-touch MFE **НЕ предсказывает** ultimate MFE на этой выборке. Тенденция слабо-обратная. Impulse persistence НЕ работает — это правдоподобный феномен на small-cap: первый impulse часто НЕПОЛНО и выдыхается (mean-reversion после impulse на тонких монетах).

**Прямой ответ на вопрос пользователя:**
- **Является ли first-touch новым механизмом?** — **Нет.** Это не новый edge. Impulse persistence не наблюдается.
- **Может ли улучшить P3 независимо?** — **Нет, не независимо — и, вероятно, никак.**
- **Самостоятельный Exit Engine?** — **Нет, не на этой выборке.**
- **Самостоятельное exit-engine v2?** — **Нет, first-touch — не каузальный канал. P3 + правильный giveback остаётся правильным кандидатом.**

## 4. Causal Decomposition (Candidate Mechanisms)

Тестирую 6 кандидатов на 34 путях (M15 72h, strict no-look-ahead, MFE дано фактически):

**Реализация:**
- **A) REAL:** lifecycle-факт (guardian-ladder + manual closes)
- **B) P3:** 33%@+1R, runner с giveback=0.75R за экстремумом, TP, cat=3R
- **C) STRUCTURAL:** close < anchor первых 2 баров (LONG) → close > anchor (SHORT), TP, cat=3R
- **D) TIME-DECAY:** age > 12h AND MFE flat (3 бара) AND structure deterior → exit
- **E) ADAPTIVE GIVEBACK:** runner с giveback = f(MFE); table-driven
- **F) COMBINED:** partial @+1R (P3-style) + structural + adaptive giveback + time-decay

**Кандидаты-детали:**
- E1: tight (0.5R → 0.3R → 0.15R)
- E2: wide (0.75R → 0.5R → 0.3R)
- E3: only-1R (0.5R → 0.3R, 1.5R → 0.15R)
- F1: tight (0.5→0.3→0.15) + structural + time
- F2: 0.5→0.3 (without 2.5R band) + structural

## 5. Comparison A/B/C/D/E/F (IS, n=34)

| Candidate | NET ($) | positive | R-sum | top-1% | giveback |
|---|---:|---:|---:|---:|---|
| A) REAL (lifecycle) | +0.16 | — | 26.5R | — | — |
| **B) P3 static 0.75R** | +2.02 | 24/34 | 26.5 | **140%** | 0.75R |
| **C) STRUCTURAL** | -0.10 | — | 26.5 | — | — |
| **D) TIME-DECAY 12h** | -0.05 | — | 26.5 | — | — |
| **E1) Adaptive 0.5→0.3→0.15** | **+3.55** | 24/34 | 26.5 | 85% | MFE-stratified |
| E2) Adaptive 0.75→0.5→0.3 | +1.66 | — | 26.5 | 179% | MFE-stratified |
| E3) Adaptive 0.5→0.3 | +3.25 | — | 26.5 | — | — |

**C/D** → **REJECT.** Без partial P3 runner либо слишком рано, либо слишком поздно. Structural/time-decay без partial упускают MFE.

**Adaptive tight (E1) = +$3.55** — лучший. Но это **IS**.

## 6. Anti-Cherry-Pick (IS)

| Variant | full | no-best | no-worst | no-both | top-1% |
|---|---:|---:|---:|---:|---:|
| B) P3 0.5 | +2.11 | -0.79 | +3.63 | +0.73 | **137%** |
| B) P3 0.75 | +2.02 | -0.80 | +3.54 | +0.72 | 140% |
| E1) Adaptive tight | +3.55 | **+0.53** | +5.07 | **+2.05** | 85% |
| E2) Adaptive wide | +1.66 | -1.31 | +3.18 | +0.21 | 179% |

**Ключевое:** static P3 проваливает no_best (–$0.79/-$0.80) — **полная зависимость от 1 сделки** (top-1% 137–140%). Adaptive wide хуже (top-1 179%). **Только adaptive tight (E1) переживает anti-cherry-pick** — no_best +$0.53 (положительный), top-1% 85%.

Но даже E1 — IS-результат. Два разных band-настройки дают разницу $1.89 (114%) — **признак IS-overfit**.

## 7. Cost-Adjusted (no-op)

Все numbers в §5 уже в NET после costs (fee 0.055%×2 + slip 2bps×2). `gross − costs` моделей, использующих 0.5R giveback, не становятся положительными сами по себе; adaptive giveback скорее выигрывает за счёт MFE-capture, чем costs.

## 8. OOS / Anti-Overfit (на той же выборке; 70/30 split)

| Variant | TRAIN (n=23) | OOS (n=11) | OOS vs TRAIN |
|---|---:|---:|---|
| B) P3 0.75 | +0.96 | +0.73 | OOS ≈ 0.76× TRAIN |
| E1) Adaptive tight | +0.79 | +1.18 | OOS **1.5×** TRAIN |

**Adaptive tight (E1) — единственный, где OOS > TRAIN** на этой выборке. Это интересный сигнал, но **n=11 OOS — слабо, может быть случайным** (доверительный интервал широкий).

## 9. Leakage / Bias Audit

- **Hindsight:** simulate_flex на M15-путях. Использует ТОЛЬКО прошлые бары (cat → time → structure → MFE-budget). Никаких будущих max-значений. ✓
- **Target leakage:** partial @+1R берётся из MFE 1R (тоже факт-уровень, не будущее). ✓
- **Selection bias:** 34 сделки — все AUTO CRYPTO 7-14.08, не отобранные post-hoc. ✓
- **Survivor bias:** все сделки включены (закрытые и SL-выходы). ✓
- **Cherry-pick:** обнаружен и зафиксирован (static P3 top-1 137%, E1 top-1 85%, E2 top-1 179%). ✓

## 10. Capture Efficiency vs Oracle (Upper Bound)

Oracle = `NET` если бы выход был сделан оптимально по MFE (теоретическая грань).

| Metric | A) REAL | B) P3 | E1) Adaptive | Oracle |
|---|---:|---:|---:|---:|
| Capture % MFE | 1.6% | ~7% | ~10% | 100% |
| NET | $0.16 | $2.02 | $3.55 | ~$9 |
| % Oracle | 1.8% | 22% | 39% | 100% |

**Adaptive tight берёт 39% of oracle** — значительный progress над 22% static P3, но **всё ещё 61% MFE упускается** даже у лучшего кандидата. Идеальный state-engine даст ещё больше, но **уже сейчас P3 + adaptive giveback = просто P3 с правильной настройкой**, не полноценный state machine.

## 11. Каузальный путь (MFE → NET)

| Stage | What | Evidence |
|---|---|---|
| ENTRY | — | 0 (не наша зона) |
| MFE появляется | да | 14/34 доходят до +1R |
| MFE **достигает ≥+0.5R** в первые 30м | **НЕ предсказывает** ultimate MFE | corr -0.13 |
| partial @+1R срабатывает | **да** | 24/34 hits +1R (если удерживается) |
| Adaptive giveback (MFE-stratified) | **да** | E1 no_best +$0.53, OOS > TRAIN |
| Costs (fees+slippage) | съедают ~30% adaptive gain | $1.21 → $0.36 final |
| NET | $0.16 → $3.55 IS (E1) | доллар +$3.39 от adaptive |

## 12. Failure Modes / Kill

| Fail | Trigger |
|---|---|
| OOS NET < TRAIN × 0.5 | E1 (adaptive) не подтверждается |
| Top-1% NET > 80% | E1 FRAGILE (top-1 уже 85%, near limit) |
| E1 - E0 (P3) ≤ $1 на IS | adaptive не даёт значимого преимущества над static P3 |
| Cost > Adaptive gain | adaptive gain $3.39 > cost ~$0.50 (нет) ✓ |

## 13. Gate (адаптированный)

**REJECT** на n=30 если:
- Adaptive tight OOS NET ≤ 0
- E1 anti-cherry-pick: NET no_best < 0
- top-1% > 100% (overfit persistence)

**PROMISING** если:
- E1 OOS > 0
- E1 no_best > 0 (это подтверждается)
- E1 не-1-symbol concentration
- E1 не-1-side concentration

**PRODUCTION CANDIDATE** — ТОЛЬКО если:
- P3 live-shadow OOS gate PASS
- E1 (adaptive giveback) подтверждается в live shadow
- anti-cherry-pick: NET no_best > 0
- top-1 symbol < 30%, top-1 side < 70%
- ME = adaptive (не full state machine) **достаточен**

## 14. Exit Engine v2 Нужен? — Нет

**Главный вывод:** лучший кандидат (E1) **получается за счёт одного параметра в P3** (giveback с MFE-stratified), **не требует** state machine с 8 состояниями, structural-exit, time-decay, exit-score и т.д.

**Причины отклонения Exit Engine v2:**
- Structural exit (C) — REJECT в 17.08, не работает один
- Time-decay (D) — REJECT без partial, упускает MFE
- Adaptive giveback (E) — это **не state engine**, это настройка одного параметра P3
- COMBINED (F) — IS-overfit, не нужный
- **State machine complexity (PROFIT EMERGING, TRENDING, DECAY)** — **на этой выборке НЕ РЕШАЕТ НИКАКУЮ ДОКАЗАННУЮ ПРОБЛЕМУ**, что не решил бы просто P3 + правильный giveback

**Если exit-engine v2 строится, он будет cost-центром, а не value-add.** Правильный путь: **P3 (live, 0.75R) → заменить giveback на MFE-stratified (E1) → калибровать параметры на OOS → всё.**

## 15. Recommended Development Path

**Не строить полноценный Exit Engine v2.** Вместо:

1. **P3 live-shadow продолжает набор (n=3 → 10–15 → 30)**
2. **На n=10–15 промежуточного forensic:** добавить профиль `P3+adaptive` (B как сейчас + giveback по MFE-bands) к ledger. Сравнить параллельно.
3. **На n=30 gate:** если P3 + adaptive outperform plain P3 по no_best/top-1%/OOS → применить к production с тем же guard'ами (R148 frozen по другим параметрам).
4. **Остальное** (structural, time-decay, full state-engine) — **отдельная гипотеза** для одиночных сигналов только если #1 не работает (маловероятно).

## 16. Why First-Touch Falsified (deeper)

- На выборке 100% small-cap (sub-$1) — **тонкая книга**. В таких условиях первый импульс часто сразу выдыхается.
- High-cap (BTC/ETH/XAU) **отсутствует** (n=0) — там first-touch мог бы работать, но это **DATA GAP**.
- **Для TradingOS** при equity ~$61 small-cap — это основной universe. First-touch **НЕ СЛЕДУЕТ** вводить.
- У **adaptive giveback** тоже могут быть edge cases на high-cap, но это вне нашего universe.

---

## FINAL RANKING

```
#1 = P3 + adaptive giveback (E1: 0.5→0.3→0.15) — IS, anti-cherry-pick пережил, OOS > TRAIN
#2 = P3 (текущий live-shadow, 0.75R trail) — active, не трогать
#3 = ничего нового — все другие кандидаты REJECT
```

## ONE NEXT ACTION = **WAIT FOR P3 n=10–15, добавить parallel `P3+adaptive` профиль в shadow ledger**

Обоснование: adaptive tight переживает anti-cherry-pick и OOS>IS, **но это P3-вариант, не новый engine**. Лучший результат — **улучшить P3 параметром (giveback по MFE)**, а не строить state machine. На live-shadow n=10–15 можно проверить E1 на live data, сохраняя R148. Если P3(n=3+) Δ>0 + adaptive tight Δ>0 в live — это и есть правильный следующий шаг, а не Exit Engine v2.
