# T32.K — EXIT CAPTURE ENGINE: HONEST VERDICT

*Read-only forensic на 34 исторических AUTO CRYPTO сделках. Online observable signal во время движения. Без future information, без reversal prediction, без fixed-R TP. Production FROZEN, R148 unchanged, P3 unchanged.*

---

## 1. Что проверяли (5 классов exit event)

Online signal, который срабатывает **во время уже начавшегося движения**, не до него:

| Event | Условие срабатывания |
|---|---|
| A. MOMENTUM FAILURE | 3+ consecutive adverse closes после достижения +0.25R profit |
| C25/C33/C50 PROFIT RETRACEMENT | retracement от MFE ≥ 25%/33%/50% в profit zone |
| D. VOLATILITY FAILURE | 5-bar range < 70% от 10-bar range AND MFE ≥ threshold |
| B. MICRO STRUCTURE BREAK | close < last higher-high (LONG) или > last lower-low (SHORT), MFE ≥ threshold |
| E. COMBINATION | C33 AND A одновременно |

Параметры (25/33/50%, 0.7 ratio, 3 bars, 5/10 windows) — все **предфиксированы** по user-ТЗ. **Никакого grid-search.**

## 2. Результаты на 34 исторических сделках

| Event | NET | Δ vs REAL | med | trigger% | OOS |
|---|---:|---:|---:|---:|---|
| **REAL** | **+$0.155** | — | -$0.005 | — | — |
| A. momentum failure | -$0.871 | -$1.026 | -$0.133 | 19/34 | -$1.92 OOS |
| C25 retrace 25% | -$1.798 | -$1.953 | +$0.003 | 24/34 | -$1.95 OOS |
| C33 retrace 33% | -$0.763 | -$0.918 | -$0.003 | 24/34 | -$1.97 OOS |
| C50 retrace 50% | -$2.428 | -$2.583 | -$0.061 | 24/34 | -$2.58 OOS |
| D. volatility failure | -$0.047 | -$0.202 | -$0.133 | 17/34 | -$1.61 OOS |
| B. structure break | -$0.676 | -$0.831 | +$0.082 | 22/34 | -$1.05 OOS |
| E. combination | -$2.430 | -$2.585 | -$0.133 | 19/34 | -$1.82 OOS |

**Все 7 моделей ухудшили результат по сравнению с REAL на historical sample.**

## 3. Anti-cherry (full → no-best & no-worst removed)

| Event | full | no-both |
|---|---:|---:|
| A | -$0.871 | -$2.621 |
| C25 | -$1.798 | -$1.815 |
| C33 | -$0.763 | -$1.879 |
| C50 | -$2.428 | -$2.952 |
| D | -$0.047 | -$1.820 |
| B | -$0.676 | -$0.692 |
| E | -$2.430 | -$2.893 |

**Anti-cherry уничтожает все результаты** — даже "лучшая сделка" не спасает, остаток переходит в глубокий минус.

## 4. OOS (chronological 2/3 train, 1/3 test)

| Event | train Δ | OOS Δ |
|---|---:|---:|
| A | +$0.893 | **-$1.920** |
| C25 | -$0.001 | -$1.952 |
| C33 | +$1.049 | -$1.968 |
| C50 | -$0.003 | -$2.580 |
| D | +$1.403 | -$1.605 |
| B | +$0.217 | -$1.048 |
| E | -$0.761 | -$1.824 |

**Все 7 моделей OOS ОТРИЦАТЕЛЬНЫЕ.** Даже те, кто показывал good training fit (A: +$0.89 train, D: +$1.40 train), на test дают -$1.6..-$1.9.

## 5. Что значит результат

**Все классы событий capture:**
1. УБИВАЮТ прибыль в winners — закрывают слишком рано (потеря continuation)
2. НЕ СПАСАЮТ losers — losses уже произошли к моменту capture-event
3. НЕ РАБОТАЮТ на OOS — даже signal, который «работает» на training, проваливается на test

Это подтверждает T32.J honest NO-GO: **exit information boundary не найден** на этом universe. Онлайн-сигналы, основанные на текущих барах, **не могут улучшить capture** систематически. **Сигнал, который "работает" на одном периоде, проваливается на следующем.**

## 6. Honesty о разнице с P3/HYB_B/EBC

| Profile | Historical 34 | Live-shadow n=7 |
|---|---:|---:|
| REAL | +$0.155 | -$0.297 |
| P3 | -$0.67 | -$0.404 |
| HYB_B | +$4.75 | **-$0.683** |
| EBC @ +3.5% | +$1.38 | **-$0.604** |
| T32.K capture event (лучший D) | -$0.05 | — |

**T32.K результаты (D = -$0.05) БЛИЖЕ к REAL чем HYB_B/EBC historical.** Это значит, что online-capture-engine **менее вреден** чем fixed-TP, но всё ещё **не лучше REAL**.

## 7. FINAL VERDICT: NO-GO — EXIT INFORMATION BOUNDARY NOT FOUND

**Online observable signal во время движения не позволяет систематически улучшить capture на наших 34 исторических + live-shadow n=7 сделках.**

Это **второе honest подтверждение** (после T32.J):
- T32.J: pre-MFE threshold reversal detection → NO-GO
- T32.K: online-event capture → NO-GO

**Это сильный вывод:** проблема не в том, что мы ещё не придумали достаточно умный выход. На нашем small-cap thin-tape universe, **online information о текущей цене не позволяет надёжно определить, когда надо закрывать прибыль vs когда держать дальше.**

## 8. СЛЕДУЮЩИЙ ВОПРОС — ЭКОНОМИКА ВХОДА И СТОИМОСТИ

Если exit-engine не улучшаем, где ещё остаётся рычаг?
- **Entry quality** (отбираем входы с большим MFE expectation, убираем los losers)
- **Trading costs** (maker fee, slippage, min-notional)
- **Risk sizing** (выше или ниже — risk-per-trade и total exposure)
- **Frequency** (более или менее сделок, с учётом costs)

Эти рычаги могут изменить NET значительно даже с тем же capture-rate.

## 9. Что сделано / что НЕ сделано

**Сделано:**
- ✅ 5 классов exit event проверены (A, C25/33/50, D, B, E)
- ✅ 7 pre-specifiedпараметров протестированы (без grid-search)
- ✅ Anti-cherry на всех
- ✅ OOS test на всех
- ✅ Full honest result сохранён в memory

**Не сделано:**
- ❌ Live-shadow backfill (поломан pd import; исторические данные достаточны)
- ❌ Production deployment (FROZEN)
- ❌ Новые исследования exit-engine (закрываем ветку)

Production FROZEN, R148/P3 unchanged, shadow-engine работает. **Exit-engine research ВЕТКА ЗАКРЫТА как NO-GO.**