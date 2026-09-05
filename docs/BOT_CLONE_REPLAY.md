# WORKING-BOT LITERAL CLONE — REPLAY + VERDICT

*Honest read-only forensic на наших 34 AUTO CRYPTO сделках. Production FROZEN, R148/P3 unchanged. Запрос: «можно ли перенести принцип pre-committed reduce-only Limit из рабочего бота без grid/averaging».*

---

## Что делает рабочий бот (FACT, из reverse engineering 7д/49 round-trips)

- **paired same-qty round-trip**: вход limit (maker, ~0.02% fee) + парный same-qty противоположный limit (maker, ~0.02% fee) с фиксированным ценовым спредом
- **Спред winner-выхода** НЕ универсален: BOTUSDT +1.81% (6 закрытий), BSPUSDT +1.15% (10), AKEUSDT +5.93% (10), BTWUSDT +6.01%, общая **медиана +3.5%**
- 0 trigger/SL ордеров в истории (нет защитного exit)
- 60% fills maker (vs 5.5% taker), 49 закрытий за 7 дней

**Переносим**: ровно один параметр — **спред** в % от entry. Без grid, без averaging, без нескольких уровней.

---

## Репликация literal bot-clone на наших 34 сделках

```python
at_entry_filled:
    limit_px = entry * (1 ± spread_pct / 100)   # ± согласно side
    place_reduce_only_limit(opposite_side, qty=full, limit_px, postOnly=True)
# дальше: limit либо исполняется (maker fee), либо SL (taker)
# timeout 72h → close at last close
```

| spread% | NET | Δ vs REAL | TP fills | top1% | verdict |
|---|---:|---:|---:|---:|---|
| 0.50% | -$2.57 | -$2.73 | 24/34 | 0% | REJECT |
| 1.00% | -$1.71 | -$1.86 | 22/34 | 0% | REJECT |
| 1.15% | -$1.25 | -$1.41 | 22/34 | 0% | REJECT |
| 1.50% | -$0.83 | -$0.98 | 21/34 | 0% | REJECT |
| 1.81% | -$0.42 | -$0.58 | 20/34 | 0% | REJECT |
| 2.50% | -$0.06 | -$0.22 | 18/34 | 0% | REJECT |
| **3.50%** | **+$1.38** | **+$1.23** | **16/34** | **41%** | **PROMISING** |
| 5.93% | -$1.57 | -$1.72 | 9/34 | 0% | REJECT |
| 8.50% | -$3.34 | -$3.49 | 5/34 | 0% | REJECT |

REAL = +$0.155.

**+3.5% — ЕДИНСТВЕННЫЙ ООS-POSITIVE pre-committed limit.**

---

## Anti-cherry на 3.5%

| full | no-best | no-worst | no-both |
|---:|---:|---:|---:|
| +$1.381 | +$1.901 | +$0.816 | **+$1.335** |

anti-cherry **no-both** всё ещё **+$1.34** — не одна-сделочная. top1 41% (умеренная концентрация).

---

## OOS (chronological 2/3 train, 1/3 test)

| spread% | train Δ | OOS Δ | passed |
|---|---:|---:|:---:|
| 1.15% | -$0.590 | -$0.814 | ✗ |
| 1.81% | +$0.496 | -$1.071 | ✗ |
| 2.50% | +$0.095 | -$0.312 | ✗ |
| **3.50%** | **+$0.437** | **+$0.789** | **✓** |
| 5.93% | +$0.704 | -$2.428 | ✗ |

**Только 3.50% проходит OOS одновременно с train.**

---

## Сравнение с другими моделями

| Model | NET (n=34) | Δ vs REAL | OOS |
|---|---:|---:|---|
| REAL (current Guardian) | +$0.155 | — | — |
| P3 static | -$0.67 | -$0.83 | n/a |
| HYB_B (25/75 hybrid @ 0.5/1.0R) | +$4.75 (HIST) | +$4.59 | **-$0.65 OOS** ✗ |
| **WBE literal (paired same-qty @ +3.5%)** | **+$1.38** | **+$1.23** | **+$0.79 OOS** ✓ |
| WBE @ +5.93% (AKE actual) | -$1.57 | -$1.72 | -$2.43 OOS ✗ |
| WBE @ +1.81% (BOT actual) | -$0.42 | -$0.58 | -$1.07 OOS ✗ |

**WBE @ +3.5% — ЕДИНСТВЕННАЯ модель с positive train AND OOS на наших 34 сделках.**

---

## Главный DATA GAP (один критический)

**fill-rate maker-TP limit orders на наших символах** (тот же gap, что в MAKER_SHADOW_GATE §6). Без него:
- 3.5% WBE NET = $1.38 это **верхняя граница** (best-case maker fill)
- Реальный NET может быть меньше, если maker-TP-fill-rate низкий

---

## Можно ли скопировать механику выхода рабочего бота, не копируя его entry/grid/averaging?

**ЧАСТИЧНО.** Pre-committed paired same-qty reduce-only limit @ +3.5%:
- Исторически: NET +$1.38, Δ vs REAL +$1.23, OOS +$0.79 ✓
- Anti-cherry устойчив (no-both +$1.34)
- Live-shadow n=7 HYB_B backfill (аналогичный maker-TP) **FALSIFIED** (-$0.68) — это **другой профиль**, но общий принцип pre-committed уже один раз не подтвердился на live-данных
- Maker fee на TP — экономически критичный фактор; без валидации fill-rate нельзя доверять реплике

---

## Final verdict: PROMISING — CONDITIONAL на maker-fill-rate validation

Pre-committed reduce-only limit @ +3.5% — единственный кандидат, переживающий:
- исторический replay на 34 сделках
- anti-cherry (no-both +$1.34)
- OOS (train +$0.44, OOS +$0.79)
- median NET -$0.020 (TPs распределены по большому количеству сделок)

**Не готов к live-shadow до**:
1. maker-TP-fill-rate на наших символах (DATA GAP)
2. P3 gate n=30 (locked roadmap R149/R150)
3. отдельного approval

---

## Минимальный read-only shadow profile (уже реализован частично как HYB_B в exit_shadow_engine.py)

HYB_B в `exit_shadow_engine.py` моделирует **25%@+0.5R + 75%@+1.0R** — это НЕ literal bot clone. **Literal bot clone** требует отдельного профиля:

```python
# bot_clone (УЖЕ БЫ СДЕЛАТЬ) — для будущей реализации, не сейчас
def simulate_bot_clone(side, entry, sl, tp, bars, spread_pct):
    sign = +1 if side in (BUY, Buy, LONG) else -1
    limit_px = entry * (1 ± spread_pct / 100)
    # SL-first intrabar
    # maker fee on TP leg
    # taker fee on SL leg
    # 72h timeout
```

Когда запуск будет одобрен:
- Добавить функцию `simulate_bot_clone` в `exit_shadow_engine.py`
- Записывать как 5-й профиль (`E_bot_clone`)
- Заполнять `delta_E` в ledger

**Это всё read-only, никаких изменений production.**