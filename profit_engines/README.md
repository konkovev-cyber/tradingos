# TradingOS Profit Engines

Отдельные исследовательские/shadow движки. Frozen directional baseline НЕ трогается.
Каждый engine проходит PROFIT FUNNEL: HYPOTHESIS → DATA → GROSS → REAL COST → OOS
→ FALSIFICATION → EXECUTION REPLAY → $82 ECONOMICS → SHADOW → MICRO-LIVE → REAL NET PNL.

## КОНТЕКСТ (2026-08-10, измерено)
- 24 гипотезы отклонены, 0 подтверждённых engines при $82.
- Реальная cost model: taker 20bps RT / maker 4.2bps RT (BTC). me_lib.py COST=60 — артефакт, не использовать.
- Universal blocker: 5-50bps gross vs 20bps cost. Median MFE текущих сигналов 0.32R.

## Текущее состояние движков

### P1 — BTC→ALT LAG (LONG-ONLY)
**STATUS: PROMISING-EXPLORATORY (НЕ BUILD).** Engine реализован (`btc_alt_lag/engine.py`),
диагностика на 60д окне подтверждает валидацию: OOS negative во всех конфигурациях
(net_med −84..−181bps, WR 0.2-0.5). Механизм статистически реален (shuffle p=0.0000),
но не переносится OOS на single-regime 60д окне.
**Требование:** >=90д свежих M15 данных, LONG-ONLY preregistered, тот же фаннел.
**Структурно stopless-1h-hold** (vol-stops убивают edge) — риск = полный ход до time-exit.
**SPEC:**
- EVENT: BTC 15m |ret| > 0.5% (UP only для LONG), кластер-дедуп 2 бара
- LAG: beta(30d,M15)×BTC_ret − ALT_ret, топ-5, min 50bps, sign match
- ENTRY: market при close события +1 бар; EXIT: time 1h (stopless)
- COST: 20bps taker RT; данные: lag_cache 139 series

### P2 — LIQUIDATION REVERSION
**STATUS: REJECT.** Даже на 1m данных displacement median 4.5bps / p90 9.3bps
против 20bps cost. Real liquidation feed на Bybit отсутствует (всё proxy).
**Причина смерти:** событие размером 2× ниже cost floor; shuffle: liquidation-label
не добавляет ценности над random-large-move.

### P3 — FUNDING CONVERGENCE
**STATUS: PROMISING-CONDITIONAL (bottleneck = капитал).** 34 символа, 18118 значений,
452 экстремальных события (>20bps/8h). Экстремумы собирают 44.7bps median next-3
против 24bps delta-neutral cost (71.4% profitable). НО ~$0.10/day на $82.
**Min capital:** $250+ для $0.20/day, $500+ для $0.50/day.
**SPEC:**
- EVENT: |funding| > 20bps/8h (p99 ≈ 43.7bps), устойчивость >=2 сеттлментов
- POSITION: delta-neutral (long spot + short perp для positive funding, зеркально)
- EXIT: time 3-6 сеттлментов или funding reversion
- COST: 24bps RT delta-neutral (spot 10×2 + perp); min notional $63+ (BTC)
- Данные: /tmp/disc002_funding_history.json (100-133д, 34 sym)

## Как добавить движок
1. `profit_engines/<engine>/engine.py` с `run_diagnostic()` → verdict dict
2. Прогнать через фаннел (common: CostModel, split_3, shuffle_pvalue)
3. Verdict: BUILD (→ shadow spec) / PROMISING (bottleneck) / DATA_REQUIRED / REJECT
4. Никогда: не считать gross/WR/backtest доказательством; только OOS NET после реальных издержек

## Ключевое правило
Не добавлять индикаторы ради спасения мертвого движка. Каждый движок обязан
ответить: «Что должно быть неправдой, чтобы механизм оказался бесполезным?» —
и выдержать эту фальсификацию.
