# WHALE-MANUAL v1 — PHASE 2 SHADOW REPORT

_Дата: 2026-08-24 ~16:30 UTC. Production заморожен (mode=MANUAL, kill_switch=true, immutable). Никаких реальных ордеров. Shadow-only._

## 1. FILES CREATED

| File | Назначение |
|---|---|
| `/root/tradingos_lab/edge_factory/whale/whale_detector.py` | Shadow whale/money-flow detector (scan-history + live stub) |
| `/root/tradingos/logs/whale/events.jsonl` | 135,849 событий (все 30s-окна, 3 символа) |
| `/root/tradingos/logs/whale/absorption.jsonl` | 10,694 absorption-candidates |
| `/root/tradingos/logs/whale/outcomes.jsonl` | 135,849 outcome-записей (forward returns + control-метка) |
| `/root/tradingos/logs/whale/control.jsonl` | (планировался) |

## 2. FILES MODIFIED

| File | Изменение |
|---|---|
| `/root/tradingos_lab/edge_factory/data/liquidation_collector_omf002.py` | **FIX**: topic-фильтр `allLiquidation` → prefix `allLiquidation.*`; schema short/long имена (T/s/S/v/p); periodic flush 60s |

## 3. LIQUIDATION COLLECTOR ROOT CAUSE (E-017 source) — FIXED

- **Root cause 1 (топик)**: `_on_msg` сравнивал `msg["topic"] != "allLiquidation"`, но Bybit v5 шлёт `allLiquidation.{SYMBOL}` (например `allLiquidation.SOLUSDT`). Сравнение никогда не совпадает → ВСЕ события отбрасывались → 0 events ~4 дня.
- **Root cause 2 (schema)**: код ожидал full-имена (`time/symbol/side/size/price`), live WS шлёт short (`T/s/S/v/p`).
- **Fix**: prefix-match по топику + парсинг обоих вариантов имён + 60s-persistent flush.
- **Live-verification**: `flushed 1 liquidation events to 1 symbols` → `DOGEUSDT 2026-08-24 13:17:38 Buy 322.0 @ 0.09173` в parquet. **LIVE подтверждён.**

## 4. DATA SOURCES / GAPS

| Source | Статус |
|---|---|
| Microstructure 30s (15 sym) | LIVE — taker flow живой с 14 авг (~10 дней) |
| Trade tape tick | LIVE — **только BTCUSDT** (43 дня) |
| Derivatives OI/funding (34 sym) | LIVE |
| Liquidations | **FIXED — теперь LIVE** (E-017 источник восстановлен) |
| ETH/SOL tape | **DATA GAP** (нет tick-level aggressor для этих символов) |

## 5. SAMPLE SIZE (shadow scan)

- BTCUSDT: 45,304 строк (2026-08-08 → 2026-08-24)
- ETHUSDT: 45,307 строк
- SOLUSDT: 45,307 строк
- Всего окон: 135,897; outcome-записей: 135,849; control windows: 30,069; absorption-candidates: 10,694

## 6. МЕТОДИКА (falsification-first)

- flow нормализован z-score против собственной истории символа (rolling 2h).
- forward returns: 1m/3m/5m/15m от mid (right-censored, 30s bars).
- контрольная группа: 30,069 random windows (seed 42, 1:4) — НЕ подбирались.
- НЕ оптимизировано под результат: пороги анализировались grid'ом post-hoc, полная картина показана.

## 7. РЕЗУЛЬТАТЫ (предварительные, shadow)

### 7.1 Flow vs Control (mean future return, %)

| Группа | n | mean 1m | mean 5m | mean 15m |
|---|---|---|---|---|
| \|z\|≥1.5 | 16,163 | +0.0014 | +0.0081 | +0.0185 |
| \|z\|≥2.0 | 8,271 | +0.0022 | +0.0112 | +0.0211 |
| \|z\|≥2.5 | 4,255 | +0.0012 | +0.0120 | +0.0254 |
| \|z\|≥3.0 | 2,155 | +0.0024 | +0.0185 | +0.0311 |
| **CONTROL** | 30,069 | +0.0001 | +0.0025 | +0.0136 |

Flow-события показывают слегка больший mean return, чем control, но **величина 0.02-0.03% на 15m далека от cost floor 0.13% RT**.

### 7.2 E-011 ABSORPTION REVERSAL — направленный отклик

| Дециль | \|z\| порог | n | signed 5m | signed 15m |
|---|---|---|---|---|
| q=0.90 | ≥1.8 | 13,596 | +0.002% | −0.002% |
| q=0.97 | ≥2.7 | 4,077 | +0.004% | **−0.006%** |
| q=0.99 | ≥3.5 | 1,359 | −0.003% | **−0.024%** |
| q=0.995 | ≥4.0 | 681 | −0.005% | **−0.031%** |
| q=0.999 | ≥5.1 | 136 | +0.009% | +0.014% |

**Наблюдение:** экстремальный поток (q≥0.99) показывает РЕВЕРС на 15m (−0.024…−0.031%) — качественно соответствует E-011 absorption-reversal. НО величина < 0.13% cost floor ×3-6. q=0.999 (n=136) — шум.

### 7.3 Absorption-кандидаты (z≥3 + слабое движение 1m)

- n=720; signed 15m = +0.001% — фактически НОЛЬ, не подтверждает ни продолжение, ни реверс.

## 8. ГИПОТЕЗЫ — ВЕРДИКТЫ (предварительно, требуется больше данных)

| Гипотеза | Результат | Статус |
|---|---|---|
| Large flow → continuation | mean 15m слегка > control, но << costs | **NO ECONOMIC EDGE (пока)** |
| Absorption (high z + weak resp) → reversal | q≥0.99 показывает −0.024% signed15m | **INSUFFICIENT SAMPLE / предварительный реверс-сигнал, экономически нежизнеспособен** |
| E-017 liquidation cascade | данные только начали собираться (fixed сегодня) | **DATA GAP — сбор только начался** |
| ETH/SOL tape aggressor | отсутствует | **DATA GAP** |

## 9. ВАЖНЫЕ ОГРАНИЧЕНИЯ (честно)

1. **Горизонт 15m мал**: microstructure 30s окна не дают tick-level агрессивности (только 50-trade окно). BTC-лента позволяет более точный aggressor — но детектор использует microstructure для всех 3 символов единообразно.
2. **cost floor 0.13% RT** — даже если эффект реален на 30m-1h (не проверено), нужен масштаб ~0.2%+.
3. z-score нормализация использует 2h rolling — волатильность-режимы могут искажать.
4. `q=0.999` противоречит тренду (reversal→continuation) — признак шума на малой выборке; нужен больший горизонт данных.
5. **Не проведена canonical-симуляция** — фаза 4 промта (нужны ≥50 событий после costs; у нас события есть, но time-horizon/cost тест не выполнен — указано INSUFFICIENT).

## 10. NEXT (что нужно для честного PASS/REJECT)

1. Дождаться ≥30-60 дней microstructure (больше экстремальных событий, n>5000 на q≥0.99).
2. Включить BTC tick-tape aggressor (точный CVD) для BTC — проверить улучшает ли точность z.
3. Проверить горизонт 30m-1h (данные позволяют).
4. Провести canonical_sim по событиям с SL/TP-структурой после накопления выборки.
5. E-017: накопить ≥50 liquidation-событий (collector fixed сегодня) → тогда тест cascade.

## 11. РЕЗЮМЕ

- Liquidation collector **ИСПРАВЛЕН и LIVE** (E-017 источник восстановлен).
- Shadow whale detector **работает**: 135k окон, 10.7k absorption-candidates, 30k control.
- Предварительно: **flow → price response существует, но на текущих микроструктурных данных (30s, 16 дней) экономически НЕ жизнеспособен** (0.02-0.03% vs 0.13% costs).
- E-011 (absorption reversal) — **предварительный реверс-сигнал на экстремальных событиях (q≥0.99), INSUFFICIENT SAMPLE для подтверждения**.
- E-017 (liquidation cascade) — **DATA GAP, сбор возобновлён**.
- **NO TRADE. NO AUTO change. Production frozen.**