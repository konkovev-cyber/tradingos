# WHALE-MANUAL v1 — PHASE 3 EXECUTION AUDIT (E-011, E-017)

_Дата: 2026-08-24 ~18:50 UTC. Production заморожен (mode=MANUAL, kill_switch=true, immutable, auditd+watchdog active). NO REAL TRADES. Shadow-only._

## ANTI-HALLUCINATION CHECK (перед выводами)

| Пункт | Phase 2 (было) | Phase 3 (сейчас) | Изменилось? |
|---|---|---|---|
| Данные новые? | 16 дней (08-08..08-24 14:54) | 16.3 дня (08-08..08-24 18:46) | Частично (данные обновились на 4ч) |
| Liquidation events | 995 | **1,518** (49 parquet, 13:17→18:46) | ✅ Да |
| Cascade episodes | НЕТ (не считали) | **41 эпизод** (26 LONG, 15 SHORT) | ✅ Да (новое) |
| OOS? | НЕТ | ✅ TRAIN/VAL/OOS (60/20/20 chronological) сделан | ✅ Да |
| NET после costs? | НЕТ | ✅ Посчитан для E-011 и E-017 | ✅ Да |
| Matched control? | Частично (30k random) | ✅ Control forward returns (300 ctl minutes) для E-017 | ✅ Да |
| 30-60 дней? | НЕТ | **НЕТ — только 16.3 дня** (честно: microstructure с 08-08) | Не изменилось |

## E-017 — LIQUIDATION CASCADE

### Cascade rule (зафиксирован ДО оценки)
```
CASCADE_MULT = 5.0 (notional_минута >= 5x baseline)
MIN_EVENTS = 3
baseline = медиана notional за прошлые 30 мин (per-symbol, shift 1, no lookahead)
Direction: short_liq_notional > 2x long → SHORT_CASCADE; и наоборот LONG_CASCADE
Эпизод = подряд идущие cascade-минуты одного направления (gap <= 2 мин)
```

### Результат
- **1,518 liquidation events** (было 995 на момент репорта Phase 2 — коллектор накапливает)
- **41 cascade-эпизод** (26 LONG_CASCADE, 15 SHORT_CASCADE)
- Forward returns через microstructure mid (signed по направлению каскада)

| Горизонт | n | mean signed | median | 95% CI |
|---|---|---|---|---|
| 1m | 41 | **+0.165%** | +0.156% | [+0.06, +0.27] ✅ CI>0 |
| 3m | 41 | +0.080% | +0.114% | [−0.01, +0.17] |
| 5m | 41 | +0.114% | +0.201% | [+0.01, +0.21] |
| 15m | 41 | +0.092% | −0.026% | [−0.09, +0.27] |
| 30m | 41 | +0.093% | −0.035% | [−0.16, +0.35] |
| 60m | 33 | +0.129% | +0.099% | [−0.09, +0.35] |

### E-017 matched control (промт §Control)
Control = 300 не-каскадных минут (microstructure fwd return raw):
- control 1m: −0.009% (CI [−0.03, +0.02])
- **Δ gross cascade-control = +0.174% (1m)**
- **Δ NET после 13bps = +0.044% (1m)** — ЕДИНСТВЕННЫЙ положительный NET

### E-017 economics

| Горизонт | gross | NET base (13bps) | NET +50% | NET +100% |
|---|---|---|---|---|
| 1m | +0.165% | **+0.035%** | −0.030% | −0.095% |
| 5m | +0.114% | −0.016% | −0.081% | −0.146% |
| 15m | +0.092% | −0.039% | −0.104% | −0.169% |
| 60m | +0.129% | −0.001% | −0.066% | −0.131% |

### E-017 VERDICT: **INSUFFICIENT SAMPLE / предварительный**
- Короткий (1m) каскад-отклик положительный (Δ NET +0.044%) — **качественный сигнал есть**, CI>0
- НО: n=41 мало, эффект на 1m ниже cost stability (сходит в минус при +50% cost), на горизонтах ≥5m NET отрицательный
- **Не PASS.** Данные накапливаются; для подтверждения нужно ≥200 эпизодов (оценка: несколько дней).

## E-011 — ABSORPTION REVERSAL

### История: честно
- **Только 16.3 дня** microstructure (08-08 → 08-24 18:46). Промт-порог 30-60 дней НЕ выполнен → **DATA GAP по 30-60д истории нет** (есть 16д).

### Canonical (TRAIN/VAL/OOS на доступных 16 днях, chronological 60/20/20)

Простая directional гипотеза: |z_delta|≥2, direction = sign(delta), exit 5m (gross % proxy).

| Split | Events | Trades | mean 5m | WR |
|---|---|---|---|---|
| TRAIN | 81,509 | 6,447 | −0.005% | 45% |
| VAL | 27,170 | 2,086 | +0.036% | 52% |
| **OOS** | 27,170 | 2,161 | **−0.008%** | **47%** |

### Cost sensitivity (OOS)
| Cost | NET 5m |
|---|---|
| base 0.13% | −0.138% |
| 1.5x 0.195% | −0.203% |
| 2x 0.26% | −0.268% |

### E-011 VERDICT: **REJECT (на текущей выборке)**
- OOS отрицательный (−0.008% gross), WR 47% — ниже 50%
- NET глубоко отрицательный при ЛЮБЫХ costs
- Не переживает даже base cost; понятно в разы хуже на 1.5x/2x
- Это НЕ оптимизация: split chronological, топ-10 не применялось (и так REJECT)

## FINAL TABLE

| Hypothesis | Events | Control | Gross | NET (base) | OOS NET_R | OOS PF | +50% Cost | TOP10 Removed | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **E-011** flow→5m | 6,447 (TRAIN) | 30k random | −0.008% (OOS) | −0.138% | OOS neg | <1 | −0.203% | не применялось (REJECT и так) | **REJECT** |
| **E-017** cascade→1m | 41 | 300 ctl | +0.165% | **+0.035%** | — (нет R-модели) | >1 (качественно) | −0.030% (уходит в минус) | не достигнуто n | **INSUFFICIENT SAMPLE** (сигнал есть, не подтверждён) |
| **E-017** cascade→5m | 41 | 300 ctl | +0.114% | −0.016% | neg | ~1 | −0.081% | — | **REJECT ECONOMICALLY** (на 5m+) |

## DATA GAPS / СЛЕДУЮЩИЙ GATE

1. **E-011**: нужны 30-60 дней microstructure (сейчас 16.3д). Gate: `микро >= 30 дней → canonical повтор (TRAIN/VAL/OOS с OOS NET_R > +0.10R, PF > 1.15, ≥50 events)`.
2. **E-017**: нужны ≥ 200 cascade эпизодов (сейчас 41). Gate: `>=200 episodes → повтор (1m short-horizon hold, CI + NET стабильность)`.
3. **1m горизонт ненадёжен** (спред/проскальзывание на 1m в микро z-шум). Для честной проверки нужен tick-tape BTC aggressor (CVD) — но это Phase 4 (owner approval).
4. Canonical R-модель (SL/TP) для E-017 не применялась — события малых горизонтов; использовать canonical_trade_sim обязует больший сэмпл.

## PRODUCTION (не тронут)
```
mode=MANUAL, kill_switch=true, trading_mode immutable
auditd+watchdog: active, 0 rogue events
liquidation collector: LIVE (исправлен), накапливает события
```

## ВЕРДИКТ ПО ПРОМТУ
```
E-011: REJECT (на текущих данных) — OOS negative, costs не переживает
E-017: INSUFFICIENT SAMPLE — предварительный positive 1m (Δ NET +0.044%), требует больше эпизодов
Общий: PHASE 3 EXECUTED — НО не PASS ни одна гипотеза.
NEXT DATA GATE: E-011 ≥30 дней микро; E-017 ≥200 эпизодов.
```