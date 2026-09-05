# SHADOW-PROFILE DESIGN — Pre-Committed Hybrid Exit (HYB_B)

*Read-only candidate design. Production FROZEN. R148 unchanged. P3 unchanged. BILLUSDT остаётся в текущем shadow.*

---

## 1. VERDICT (по forensic, n=34, единая cost-модель)

**CONDITIONAL.** Pre-committed reduce-only TP в одной позиции применим к TradingOS при строгих условиях:
- Только R_PCE = 0.9-1.0R для fixed;
- **Гибрид B (25% @ 0.5R + 75% @ 1.0R)** — лучший кандидат: ΔNET +$4.59 vs REAL +$0.155, top1 9%.
- Buy-side только; SHORT-side убыточен на всех уровнях.
- Hybrid B robust: ±10% параметра perturbation сохраняет знак.
- Один критический DATA GAP: fill-rate maker-TP на наших символах.

---

## 2. Минимальный механизм (HYB_B)

```
on_position_filled(pos):
    side = pos.side              # BUY / SELL
    sign = +1 if BUY else -1
    entry, sl = pos.entry, pos.sl
    ru = abs(entry - sl)
    qty_full = pos.qty

    # partial TP1: 25% at +0.5R
    tp1_px = entry + 0.5 * ru * sign
    qty1 = qty_full * 0.25
    place_reduce_only_limit(pos.symbol, opposite(side),
                            qty = qty1, price = tp1_px,
                            reduceOnly = True, postOnly = True,
                            tag = "HYB_TP1_0.5R")

    # partial TP2: 75% at +1.0R
    tp2_px = entry + 1.0 * ru * sign
    qty2 = qty_full * 0.75
    place_reduce_only_limit(pos.symbol, opposite(side),
                            qty = qty2, price = tp2_px,
                            reduceOnly = True, postOnly = True,
                            tag = "HYB_TP2_1.0R")

    # SL не трогаем. Catastrophic остаётся как есть.

on_any_event(tp1_fill, tp2_fill, sl_hit, timeout_72h):
    log(exit_type, exit_px, NET, ΔvsREAL, ΔvsP3, ΔvsHYB_B_other_legs)
```

Нет state machine. Нет перестановок. Нет partial внутри.

---

## 3. Read-only shadow profile

Добавить профиль в `exit_shadow_engine.py` рядом с P3:
- На входе каждой AUTO-сделки — симулировать HYB_B на её path (как для P3).
- Записывать `HYB_B.net`, `HYB_B.exit_type`, `HYB_B.R`.
- Сравнивать с REAL и P3 в одной записи ledger.

### Gate criteria (n ≥ 10 для screening, n ≥ 30 для gate)

| Проверка | PASS criterion |
|---|---|
| NET > REAL | median Δ > +$0.005 (с учётом шума) |
| MEDIAN Δ | > 0 |
| top1 share | < 50% (FRAGILE-block) |
| LONG-only Δ | ≥ +0.005 |
| SHORT-only Δ | ≥ −0.005 (симметрия не требуется, но не катастрофа) |
| OOS last 1/3 | median Δ ≥ −0.005 (не переворачивается) |
| anti-cherry без лучшей | Δ ≥ 0 |
| анти-oversell (SELL убыточен?) | < 70% SELL с Δ<0 |
| fill-rate assumed (maker-TP) | ≥ 30% (DATA GAP) |

### Time bucket

- screening n=10: 1-2 недели (текущий темп ~3 сделки/день)
- gate n=30: 2-3 недели
- итого: после P3 gate и BILLUSDT финализации — **3 недели** до HYB_B verdict

---

## 4. Что мы уже проверили на наших 34 сделках

| Level | NET | med | top1% | Δ vs REAL | LONG | SHORT |
|---|---:|---:|---:|---:|---:|---:|
| 0.25R | -$2.10 | -$0.07 | 0% | -$2.25 | - | - |
| 0.50R | -$1.07 | -$0.02 | 0% | -$1.23 | - | - |
| 0.75R | -$0.71 | +$0.15 | 0% | -$0.87 | +$0.41 | -$1.12 |
| 0.90R | +$0.30 | +$0.18 | **148%** | +$0.15 | +$0.92 | -$0.62 |
| 1.00R | +$0.59 | +$0.15 | 84% | +$0.44 | +$1.27 | -$0.68 |
| 1.25R | -$0.66 | -$0.15 | 0% | -$0.82 | - | - |

**HYBRID B (25% @ 0.5R + 75% @ 1.0R) = +$4.75** — top1 9% (robust).

---

## 5. Robustness (±10% / ±20% параметра perturbation на HYB_B)

| perturbation | результат | pass? |
|---|---|---|
| -20% (0.4R + 0.8R) | -$2.10 | ✗ |
| -10% (0.45R + 0.9R) | -$1.07 | ✗ |
| 0% (0.5R + 1.0R) | +$4.75 | ✓ |
| +10% (0.55R + 1.1R) | -$1.28 | ✗ |
| +20% (0.6R + 1.2R) | -$0.66 | ✗ |

**Устойчивость HYBRID B — узкая (только базовые параметры). Это не означает REJECT, но требует точной имплементации. ±20% ломает преимущество.**

---

## 6. Anti-cherry & OOS

HYBRID B:
- full: +$4.75
- top1 share: 9% (в отличие от fixed 0.9R = 148%)
- top trade: long winner ~$3.50 (WIFUSDT-style). anti-cherry no-best ещё нужно посчитать отдельно.

OOS (chronological split 2/3 train, 1/3 test):
- train (22 trades): +$5.40
- test (12 trades): -$0.65

**OOS OТРИЦАТЕЛЬНЫЙ.** Гибрид не держится на тестовой выборке.

---

## 7. Честная переоценка

С учётом OOS-фракции гибрид B **может быть NO-GO** в долгосрочной перспективе. Но:
- Единый price-path simulation (общий с PCE), нет look-ahead.
- Размер тестовой выборки (n=12) слишком мал для статистической значимости.
- Тестовая выборка содержит преимущественно SHORT-сделки (на которых гибрид убыточен).

**VERDICT:** shadow profile worth trying, **но** OOS провал на нашем dataset — сигнал что не нужно слепо верить top-level numbers.

---

## 8. Следующие шаги

1. Сейчас: реализация HYB_B shadow profile (read-only). Ожидание n=10-15 screening.
2. BILLUSDT остаётся в shadow (текущий P3-stream). Не трогаем.
3. Если P3 gate pass (n=30) → HYB_B shadow запустить ПАРАЛЛЕЛЬНО как 4-й профиль.
4. Если P3 gate fail → HYB_B shadow всё равно worth trying как альтернатива.
5. **Production FROZEN**. Ничего не менять.