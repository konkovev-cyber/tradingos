# POST-P3 ECONOMIC ENGINE — TradingOS

*PREPARED, NOT IMPLEMENTED. R148 FROZEN. Production не менять, ничего не деплоить, P3 не трогать, risk/cap/entry/ADX/SL/TP/guards/correlation не трогать. Документ — готовый к применению economic model + exact post-P3 order of operations, чтобы после P3-gate сразу было понятно, куда двигаться. Все числа — CONDITIONAL, IS (live-shadow n=4, historical n=34).*

---

## 1. Current Economic Bottleneck

**Bottleneck не в P3 — bottleneck в самих числах масштаба на текущем капитале.**

- equity ≈ $61, cap 20%, risk $0.50, 5-7 сделок/день
- median holding 3.3h → 0.7 параллельных позиций (max=4 **не лимитирует**)
- max_open_risk 3% = **$1.83** → при risk $0.5 хватает на 3 позиции, при risk $1.0 — **только 1**
- daily_loss 1.5% = $0.92 → при risk $0.5 = 1 SL до блока, при risk $1.0 = **0 SL до блока**
- total_loss 4% = $2.44, MARGIN_BUFFER $5

**Главный capacity-limiter — guard `max_open_risk 3%`, не cap 20% и не max_positions 4.** Это означает:

1. **Risk-ladder $0.50→$1.00 — фактически невозможен** при current equity: при $1.00 max 1 параллельная позиция и 0 SL в день до дневного блока. Это **не** реалистичный composition stage.
2. **Реалистичный range: risk $0.50→$0.75** (max_pos 3→2, max daily SL 1→1). За пределами — guards душат.
3. **Дополнительный фактор** — 100% small-cap universe (med slippage 20bps) — для capital scaling нужно exit в более ликвидные монеты (DATA GAP для BTC/ETH).

## 2. Conditional Economic Model (IS, live-shadow n=4 + historical n=34)

Все числа **CONDITIONAL** на P3/E1 OOS-pass. Anti-cherry-pick пережил (n=34 IS), но OOS не проверен.

| Composition | Δ/trade | trades/day | **/day** | /month (22 дня) | guard-bottleneck |
|---|---:|:---:|---:|---:|---|
| baseline (P3 0.5R) | **$0.07** | 5.5 | $0.39 | $8.50 | max_open_risk 3% (3 позиции) |
| P3 adaptive E1 (0.5→0.3→0.15) | **$0.10** | 5.5 | $0.55 | $12.10 | max_open_risk 3% (3 позиции) |
| P3+E1 + cap 30% | **$0.125** | 5.5 | $0.69 | $15.10 | max_open_risk 3% (3 позиции) |
| P3+E1 + cap 30% + maker | **$0.130** | 5.5 | $0.72 | $15.75 | max_open_risk 3% (3 позиции) |
| P3+E1 + cap 30% + risk $0.75 | **$0.1875** | 5.5 | $1.03 | $22.70 | max_open_risk 3% (**2** позиции) |
| P3+E1 + cap 30% + risk $1.00 | **$0.25** | 5.5 | $1.38 | $30.25 | max_open_risk 3% (**1** позиция, 0 daily SL) |

**Что внутри:** Δ/trade считается как `P3+adaptive_E1 Δ` × `risk/RISK_BASE` (линейное масштабирование — допущение, нужна OOS-валидация).

## 3. Fastest Path to $0.50/day, $1/day, $3/day

Чтобы получить target /day при 5.5 trades/day:

| target | required Δ/trade | реалистично? |
|---:|---:|---|
| **$0.50/day** | **$0.091** | YES — это P3+E1 Δ = $0.10 IS, требует P3 PASS на n=10-15 |
| **$1.00/day** | **$0.182** | PARTIALLY — достижимо с risk $0.75 (после P3 PASS), но max 2 параллельных |
| **$3.00/day** | **$0.545** | NO — требует capital scaling $200+ (max_pos 4 + risk $1.0 на capital $200) |

**Главный insight:** $0.50/day реалистично **на текущем капитале $61** при P3 PASS. $1/day — на грани (risk $0.75, 2 параллельных позиции). $3/day — **только с capital scaling** до $200+ (3-4 параллельных позиции при risk $0.75-1.00).

## 4. Guard Bottleneck Map

| Risk | max open @ 3% | max daily SL @ 1.5% | max total loss | min capital для X параллельных |
|---:|---:|---:|---:|---:|
| $0.50 | **3** | **1** | 4 | $50 (1) / $100 (2) / $150 (3) |
| $0.60 | 3 | 1 | 4 | $60 / $120 / $180 |
| $0.75 | **2** | 1 | 3 | $75 / $150 / $225 |
| $1.00 | **1** | **0** | 2 | $100 / $200 / $300 |

**Вывод:** при equity $61 реалистичные composition stages:
1. **risk $0.50 + P3+E1** (без risk-up) → $0.55/day
2. **risk $0.75 + P3+E1 + cap 30%** → $1.03/day (2 параллельных)
3. **capital scaling до $100-150** → $0.50-1.50/day

**Не реалистично на $61:** risk $1.0 (1 параллельная позиция, 0 daily SL = блок сразу на первом убытке).

## 5. Current Economic State (4 cases for OOS validation)

| Composition | Δ/trade | /day | /month | verification needed |
|---|---:|---:|---:|---|
| **A. baseline** (P3 0.5R) | $0.07 | $0.39 | $8.50 | уже тестируется в live-shadow n=4 |
| **B. P3 adaptive E1** | $0.10 | $0.55 | $12.10 | parallel profile, n≥10 paired live |
| **C. P3+E1 + cap 30%** | $0.125 | $0.69 | $15.10 | controlled test, n=10 после P3 |
| **D. P3+E1 + cap 30% + risk $0.75** | $0.1875 | $1.03 | $22.70 | controlled test, n=10 после C |

**Все числа — UPPER BOUND**, потому что:
1. P3/E1 IS-данные — anti-cherry пережил, но OOS не проверен
2. Линейное масштабирование Δ ∝ risk — допущение (на больших рисках может быть меньше из-за adverse market conditions)
3. small-cap universe (медианный slippage 20bps) — на high-cap (нет данных) эффект может быть другим

## 6. What can be PREPARED today (не трогая P3 experiment)

**Готовые к реализации артефакты** (все read-only, не загрязняют P3):

| Артефакт | Назначение | Что делает |
|---|---|---|
| `research/post_p3_economic_engine.py` | Conditional economic model | Числа 5.1-5.4 (composition × Δ/trade × /day × /month × guard-bottleneck) |
| `research/capacity_simulator.py` | Capital + risk + guard interaction | Таблица 4 (max_pos, max_daily_SL при разных risk) |
| `research/risk_ladder_simulator.py` | Risk $0.50→$0.60→$0.75 controlled test design | Параметры: сколько n required, какие контрольные метрики |
| `research/maker_fill_model.py` | Pass-readiness, не live | Architecture maker fill-rate simulation на microstructure-data |
| `research/combined_net_model.py` | Composition sensitivity | Δ/trade × risk × cap × costs → NET/день |
| `research/post_p3_approval_checklist.md` | Pre-deploy validation | 10 чеков для approval каждой stage |
| `research/post_p3_dashboard.py` | Report generator | Текущий Δ vs baseline, P3 vs P3+E1, OOS-блок |

Все артефакты — read-only, никаких live-изменений, R148 frozen, P3 не трогаем.

## 7. Post-P3 Decision Tree

### Если P3 FAIL (n=30, median Δ ≤ 0 или anti-cherry проваливает)
→ P3 REJECT. Не начинать новый search. Записать как falsified, уйти в HOLD до накопления нового evidence.
→ НЕ автоматически уходить в adaptive/maker/cap-30% — каждое требует P3 как baseline.
→ Возможные next steps (только при evidence-led):
  1. Пере-анализ entry edge (FALSIFIED first-touch impulse persistence, ADX<25 IS-only)
  2. Совершенно другая гипотеза (non-P3)

### Если P3 PROMISING (n=10-15, IS pass, OOS weak)
→ Продолжить до n=30. P3+E1 paired shadow test (read-only).
→ НЕ подменять production exit. P3+E1 — candidate, не default.

### Если P3 STRONG (n=10-15, IS + OOS pass, low concentration)
→ Завершить gate на n=30.
→ Перейти к controlled test P3+E1 (separate profile, read-only, paired).
→ Готовить approval-checklist для stage 2 (cap 30%).

### Если P3 PASS (n=30, gate 10/10)
→ T1: P3 production decision (controlled test в production, 1 stage, отдельный approval).
→ T2: P3+E1 controlled test (parallel profile, n=10).
→ T3: cap 20%→30% controlled test (n=10).
→ T4: maker pass-readiness (если fill-model прошёл n=10 data).
→ T5: risk $0.50→$0.75 (отдельно, n=10).
→ T6: capital scaling (только после T1-T5).

## 8. EXACT POST-P3 ORDER OF OPERATIONS

| Stage | Что | Условие перехода | Approval |
|---|---|---|---|
| **0** (current) | P3 live-shadow n=4 → n=30 | n≥30, gate 10/10 | R148 frozen |
| **1** | P3 production controlled test | P3 PASS (gate 10/10) | **отдельный approval** |
| **2** | P3+E1 paired shadow | P3 PASS, 1 уже выполнен | read-only, no approval |
| **3** | P3+E1 production controlled test | P3+E1 IS+OOS pass (n≥10 paired) | **отдельный approval** |
| **4** | Cap 20%→30% controlled test | P3/E1 PASS (1-3) | **отдельный approval** |
| **5** | Maker pass-readiness | Fill-model прошёл n=10 data | **отдельный approval** для live maker |
| **6** | Risk ladder $0.50→$0.75 | 1-5 PASS, n=10 каждая | **отдельный approval** |
| **7** | Capital scaling $61→$100-200 | 1-6 PASS, 30+ сделок validated | **отдельный approval** для каждого шага scaling |

**Каждое condition — отдельный approval, никаких batched changes.** R148 frozen до stage 1 approval.

## 9. BIGGEST ECONOMIC RISK

**Главные риски (в порядке убывания):**

1. **P3 не выдержит OOS** (live-shadow n=4, IS только) → весь pipeline stages 2-7 не запускается.
2. **Linear scaling assumption wrong** (Δ ∝ risk) — на больших risk adverse market conditions могут усилить убытки больше, чем дать линейный рост Δ. Mitigation: test on n≥10 controlled before full deploy.
3. **Смешивание stages** — anti-cherry-pick переживает single stage, но не combined. **Каждый stage отдельно** (R102, R105, R150).
4. **guard-bottleneck при risk>0.75** — max_pos 1-2, max_daily_SL 0-1. Risk-ladder выше $0.75 требует capital scaling $100-200.
5. **universe small-cap** (100% small-cap, 0 high-cap data) — capital scaling требует выхода на high-cap (DATA GAP). Без этого масштабирование упирается в те же small-cap.
6. **pre-existing entry quality** (n=34, MFE 26.5R но realized -1.3R) — даже лучший exit не компенсирует убыточный entry, если cap stays small.

**Pre-stage 1 mitigation:** на live-shadow сейчас видеть MFE-capture / giveback по P3 и сопоставлять с 100%-smallcap realities. Если MFE 26.5R — это really "только small-cap", и high-cap даёт другие ratio — все composition stages могут быть отвергнуты до gate.

## 10. ONE NEXT DEVELOPMENT TASK

**Подготовить `research/post_p3_economic_engine.py`** — conditional model, который я уже набросал в п.2-5. Код:

- Таблица 4 (guard bottleneck при разных risk)
- Таблица 5 (composition × Δ/trade × /day × /month)
- Reverse: target /day → required Δ/trade
- Forward: Δ/trade при n=10-15, n=30 → /day → /month

**Inputs:** `realized_R` (historical n=34), `mfe_peak_r` (для capture), `fees` + `slippage` (для cost-adjusted), все IS. Outputs: JSON с числами для каждой composition.

**Use case:** когда P3-shadow достигнет n=10-15, скрипт выдаёт exact ΔNET/день для каждой composition при условии P3 OOS-pass. **Без запуска live experiment.** Read-only.

**Время разработки:** один субагент, ~30 минут. Артефакт готов сразу после.

**Не делаю:** deploy, restart, config change, live experiment, изменение P3/risk/cap/entry/ADX/SL/TP/guards/correlation.

## ONE NEXT ACTION = **PREPARE economic engine скрипт (read-only) + WAIT for P3 n=10-15**

Production FROZEN. P3-shadow продолжает набор. Не делаю ничего, кроме подготовки готового engine и ожидания выборки.
