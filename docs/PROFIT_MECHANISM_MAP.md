# TradingOS — PROFIT MECHANISM MAP (2026-08-13)

**Честная карта проверенных экономических механизмов.** Каждая строка = механизм, который был
формально проверен (falsification-first, costs, OOS, controls) и получил вердикт. НЕ список
«надо ещё попробовать».

| Механизм | Исследование | Predictability | Gross | Costs | NET | OOS | Capital efficiency | Вердикт |
|---|---|---|---|---|---|---|---|---|
| Directional (RSI/ADX/score/momentum) | T1-T31 | нет | ~0 | > | − | − | − | ❌ REJECT |
| Entry timing (5 паттернов × 6 входов) | T30 | нет | +0.04% best | > | − | − | − | ❌ REJECT |
| Session timing (5×3×4) | T31 | волатильность есть, направление нет | ~0 | > | − | − | − | ❌ REJECT |
| Microstructure (order-book imbalance) | T32 | **есть ~1bp** | +1.1bp | 15bp | −13.9bp | − | − | ❌ REJECT_EXECUTION |
| SL / horizon | T45 | нет (recovery < random) | − | > | − | − | − | ❌ REJECT |
| Position lifetime | T48 | нет | −1.67$/ч | > | − | − | − | ❌ REJECT |
| Inversion (анти-сигнал) | T49 | нет (14/14 = SL-механика) | − | > | − | − | − | ❌ REJECT |
| Time-as-risk | T50 | нет (NET/час <0 везде) | − | > | − | − | − | ❌ REJECT |
| Controlled averaging | T51 | нет (marginal ≤0) | − | > | − | − | − | ❌ REJECT (tail×2) |
| Relative value / pair | T52 | нет (нестационарно) | +17.5bp best | > | −3.5..−17.5 | − | − | ❌ REJECT |
| OI / price divergence | T53 | нет (инкремент ≈ 0) | − | > | − | − | − | ❌ REJECT |
| Cross-venue dislocation | T54 | реальный D 0.5-1.4bp | 1.35bp | > | − | − | − | ❌ REJECT (D=5-10× basis) |
| PINOK (внешняя стратегия) | T55 | нет (gross≈random) | − | > | − | − | − | ❌ DUPLICATE |
| Crypto pipeline | T56 | дефект воронки (F7) | − | − | − | − | − | ✅ F7 FIXED (edge не появился) |
| MFE capture (Guardian exit) | T58 | giveback реальный, но = market+manual | − | − | − | − | − | ❌ REJECT (KEEP M0) |
| **H2 maker/rebate** | LIVE | **не измерен** | ? | ? | **?** | ? | ? | 🟡 0 FILLS — НЕ ПРОВЕРЕН |
| **Manual-close process** | T58/T84 | вмешательство ухудшает реализацию | − | − | − | − | − | ⚠️ ЗАБЛОКИРОВАНО (T84) |

## Главный вывод

> **В проверенном пространстве (крипто-перпетуалы Bybit, 2026-08-06→13, ~2.5M+ симулированных
> сделок) не обнаружено ни одного directional NET-edge механизма.** Отвергнуты: сигналы, вход,
> сессии, микроструктура, SL/lifetime, инверсия, время, усреднение, relative value, OI-divergence,
> cross-venue, внешние паттерны, pipeline (дефект исправлен), MFE-capture.

## Что осталось живым (не отвергнуто)

1. **H2 maker/rebate** — принципиально другая экономика (исполнение, не прогноз цены).
   0 реальных fills → **не доказано ни «да», ни «нет»**. Единственный механизм, ждущий факт.
2. **Manual-close process** — T58 показал: человек иногда уничтожал заармированный Guardian-выход.
   T84 заблокировал (кроме PANIC). Следующая выборка — чистая AUTO.

## Правило на будущее (binding)

Если H2 после реальных fills не даст положительный NET — **дальнейшее добавление индикаторов,
паттернов и фильтров не имеет доказательного основания**. Искать нужно: другой механизм
(отличный от directional), другой рынок/инструмент, или принять «нет воспроизводимого edge на
текущей конструкции».

## Артефакты (полные отчёты)

T1-31: /tmp/entry_discovery/, /tmp/session_edge/, /tmp/ls_sweep_forensic/
T32: /tmp/micro_edge/ · T45: /tmp/stop_horizon/ · T48: /tmp/t48_lifecycle/
T49: /tmp/signal_inversion/ · T50: /tmp/time_risk/ · T51: /tmp/controlled_avg/
T52: /tmp/rel_value/ · T53: /tmp/oi_divergence/ · T54: /tmp/cross_venue/
T55: /tmp/pinok/ · T56: /tmp/crypto_bias/ · T58: /tmp/guardian_capture/
