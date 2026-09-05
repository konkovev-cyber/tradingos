# WHALE_DATA_AUDIT.md — Whale/Money-Flow Data Availability Audit (Phase 1)

_Дата: 2026-08-24. Цель: определить что реально доступно для whale-detector (BTC/ETH/SOL), granularity, latency, quality, gaps, историческая глубина. READ-ONLY аудит — код не написан, никакие данные не собраны/изменены._

## 1. РЕЗЮМЕ (для быстрого чтения)

| Источник | Статус | Скрипт (PID) | Гранулярность | Символы | Свежесть | Reuse |
|---|---|---|---|---|---|---|
| Microstructure (book + taker) | ✅ LIVE | `edge_factory/data/microstructure_collector.py` (489943) | 30 s | 15 (BTC,ETH,SOL,...) | 0 min | Да, ядро detector |
| Trade tape (tick) | ✅ LIVE | `edge_factory/data/trade_collector.py` (1491537) | каждая сделка | **ТОЛЬКО BTCUSDT** | 0 min (parquet hourly+sha256) | Да (BTC), для ETH/SOL — GAP |
| Derivatives (OI/funding/long-short) | ✅ LIVE | `edge_factory/data/derivatives_collector.py` (785) | ~5.5 min | 34 | 1 min | Да |
| Funding snapshots (полный вселен) | ✅ LIVE | poller (нет матч. процесса) | 5 min | ~весь Bybit universe | сегодня | Да |
| Liquidations | ❌ **DEAD** | `edge_factory/data/liquidation_collector_omf002.py` (1472375) | событие | 15 | **0 событий ~4 дня** | Треб. фикс |
| Orderbook raw (L2 историч.) | ❌ STALE | legacy `orderbook_collector.py` | ~10 s | BTC | **с 2026-08-03 мёртв** | Нет (заменён microstructure) |

## 2. МИКРОСТРУКТУРА (ядро whale detector) — LIVE 30s

Файл: `/root/tradingos_lab/edge_factory/data/microstructure/{SYM}.jsonl` (45k+ строк/символ с 2026-08-08)

Поля (пример BTCUSDT 2026-08-24T12:37):
```
ts, iso, symbol, mid, spread_bps,
imb_10, imb_20, imb_50           # order-book imbalance %
bid_depth_01pct, ask_depth_01pct # глубина в 1% от mid
taker_buy_vol, taker_sell_vol, taker_total, taker_delta, taker_delta_pct, taker_imb,
n_trades
```

**Ключевой факт для whale:**
- taker flow **живой с 2026-08-14T18:58** (первая строка с `taker_total>0` = строка 17615; последние 5000 строк — 100% с taker). Раньше (8-14 авг) taker был 0 — collector обновили.
- `taker_delta != 0` только в ~12% записей (ранее), но в свежих — чаще; окно = последние 50 trades из `/recent-trade`.
- Это даёт **~10 дней живых taker-данных** (14–24 авг) для валидации.

## 3. TRADE TAPE (tick-level) — LIVE, только BTC

Файл: `/root/data_buffer/raw/bybit/trades/BTCUSDT/{date}/{hour}.parquet` (+ .sha256)

Schema: `timestamp(int64, ms), received_at, price(float), qty(float), side(str Buy/Sell), trade_id(UUID), symbol`

**Факты:**
- **Беспрерывная лента с 2026-07-13 — 43 дня** (999 parquet файлов), свежий час пишется (15:35 сегодня).
- ~10 000 сделок/час (последний час: 10 011 rows).
- **ВАЖНО: ETHUSDT и SOLUSDT — 0 дней tape.** Trade collector подписана только на BTCUSDT. Для whale-сигналов по ETH/SOL tap-агрессивность **недоступна** без расширения collector.
- Источники venue-of-trade нет (все Bybit).

## 4. DERIVATIVES (OI/funding/long-short) — LIVE

Файл: `/root/tradingos_lab/edge_factory/data/derivatives/derivatives.jsonl` (155,550 rows с 2026-08-06, 1 min old)

Поля: `ts, iso, symbol, funding_rate, open_interest, buy_ratio, sell_ratio`

- 34 символа (BTC, ETH, SOL, XRP, BNB, DOGE...)
- каденция ~5.5 min (REST 300s loop)
- НЕ зафиксировано время котировки биржи (только локальное ts).

## 5. FUNDING SNAPSHOTS (полный universe) — LIVE

Файл: `/root/tradingos_lab/edge_factory/data/funding_snapshots/{date}.jsonl`
- Ежедневный файл ~76 MB (288 строк/день по ~5 min), сегодня 153 строки.
- Поля включают `predicted_funding_rate`, `next_funding_time`, `funding_interval_min`, min_notional.
- Для whale useful как контекст (funding extremes) — не как самостоятельный сигнал (по определению промта).

## 6. LIQUIDATIONS — ❌ DEAD (DATA GAP)

- Collector `liquidation_collector_omf002.py` (PID 1472375) — **0 событий за ~344,000 сек (~4 дня)**.
- Output dir `/root/data_buffer/raw/bybit/liquidations/` пуст.
- Подозрение (explore-8): баг фильтрации топика — код ожидает `allLiquidation`, тогда как реальный WS-топик по символам `allLiquidation.{SYMBOL}`.
- **Следствие:** liquidation-cascade (E-017) и liquidation-подтверждение для whale — **недоступны** до фикса. Фикс = отдельная задача (не входит в Phase 0; требование owner approval).

## 7. ORDERBOOK RAW (L2 история) — ❌ STALE

- Legacy `orderbook_collector.py`/`/root/data_buffer/raw/bybit/orderbook/` — мёртв с 2026-08-03 (348k файлов .gz, логи dead).
- Заменён живым microstructure (30s book snapshots). Только BTC.

## 8. ПРОШЛЫЕ RESEARCH VERDICTS (ОБЯЗАТЕЛЬНО к соблюдению)

| Исследование | Файл | Вердикт |
|---|---|---|
| OMF-001 (микроструктура 10 гипотез M1–M10) | `/root/tradingos/docs/OMF001_ORDERFLOW_RESEARCH.md` | **NO MICROSTRUCTURE EDGE** — 7 REJECT, 2 DATA GAP, 1 INCONCLUSIVE; сигналы ≤4.5 bps против 15 bps cost floor |
| OMF-002 (liquidation flow) | `docs/OMF002_DATA_AUDIT.md` | **DATA COLLECTOR LIVE — 0 событий** |
| Cross-sectional (A1–A3, BTC regime E2) | `docs/FINAL_NON_INDICATOR_EDGE_RESEARCH.md` | **ALL REJECT** |
| RR hunt (6 families) | `docs/RR_EDGE_HUNT_VERDICT.md` | **NO ECONOMIC RR EDGE** |
| Итог | `docs/ECONOMIC_EDGE_RECOVERY_VERDICT.md` | **D) NO ECONOMIC SOLUTION** — "Information EXHAUSTED" |
| **EDGE_ATLAS E-011 Absorption Reversal** | `docs/EDGE_ATLAS.md` | **DATA_COLLECTION — НЕ валидировано** (нет L2) |
| **EDGE_ATLAS E-017 Liquidation Cascade Exhaustion** | `docs/EDGE_ATLAS.md` | **DATA_COLLECTION — НЕ валидировано** |
| THESIS-H-MIC-005 Dark Pool Sweep | `edge_factory/runs/` | не исполнен |

**Следствие:** whale-подход пересекается с E-011/E-017 (absorption/liquidation) — эти прямо **открыты для валидации**. Но переход через canonical gate обязателен (см. §10).

## 9. REUSE-МАППИНГ для WHALE-MANUAL v1

| Компонент | Файл | Reuse как есть? | Что именно |
|---|---|---|---|
| 2-step confirm Telegram | `telegram_control/manual_signal.py` (`ms_open_`→gate→`ms_exec_`→`ms_amt_`) | ✅ Полностью | Карточка+кнопки+подтверждение уже существуют |
| Manual executor (SL/TP inline) | `telegram_control/manual_signal.py` `_place_market_order` | ✅ Полностью | Bybit POST + TP/SL + set_leverage 5x + journal |
| Signal card/journal persistence | `_send_signal_card`, `SIGNAL_SENT` journal | ✅ | Кнопки переживают рестарт |
| Order dedup | `core/order_dedup.py` | ✅ | `decision_id` JSONL 48h — сейчас **пуст** (manual не ставил decision_id); whale обязан ставить |
| Singleton guard | `core/singleton.py` | ✅ | flock-защита от дублей |
| Risk limits (global) | `strategies/deposit_guard.py` | ⚠️ Нужен ОТДЕЛЬНЫЙ профиль | Лимиты глобальны из `trading_mode.json` (kill_switch ON) |
| SL/TP manage / guardian | `guardian/reality_guardian.py` | ✅ | `_set_trading_stop`, отслеживание MANUAL-позиций |
| Telegram proxy | `socks5://127.0.0.1:1080` | ✅ | Обязателен (no direct access — BUG #11) |
| Control plane | `core/control_plane.py` (план) | Пока НЕ создан | WORKSTREAM B после root cause |

## 10. CANONICAL GATE (ОБЯЗАТЕЛЬНЫЙ порог для валидации)

`/root/tradingos/research/canonical_trade_sim.py` (same-bar SL-first, направленные MFE/MAE, no lookahead, max_horizon=96)

Порог проекта:
- OOS NET > costs (taker 5.5+5.5 bps + 1 bps slippage/side **~13 bps RT**, стресс 1.0/1.2/1.5/2.0×)
- anti-cherry > 0 (per-symbol не-концентрировано)
- day-by-day стабильность + отсутствие period-concentration (MICRO_SANITY правило)
- min event n, time-stable
- для whale с universe=3 символы: требование ≥5 symbols недостижимо → **STATUS: INSUFFICIENT UNIVERSE** до расширения (owner approval)

## 11. ИТОГ: что whale engine может переиспользовать КАК ЕСТЬ

1. **BTC trade tape (43 дня)** → taker/CVD, крупные сделки, burst detection — только BTC.
2. **Microstructure 30s (14+ дней taker)** → OB imbalance/depth, taker delta для 15 символов (вкл. BTC/ETH/SOL).
3. **Derivatives (OI/funding/long-short, 34 символа)** → подтверждение направления.
4. **Funding snapshots (полный allлен)** → funding extremes context.
5. **Telegram 2-step confirm + manual executor + dedup + singleton + guardian SL/TP** → полностью reuse.

**Мust-fix gaps (до детектора):**
- ETH/SOL trade tape отсутствует (расширить `trade_collector.py` или использовать microstructure taker как суррогат).
- Liquidations dead (баг topic — фикс отдельной задачей).
- Отдельный risk profile для WHALE_MANUAL (не глобальный deposit_guard).
- decision_id должен ставиться на каждый whale signal (сейчас пусто).

## 12. NEXT (Phase 2 gate)

По промту WHALE-MANUAL v1 Phase 0: аудит завершён → вернуть отчёт владельцу (архитектура + список файлов) → **ЖДАТЬ owner разрешения** на Phase 2 (detector). Реальный код detector — только после подтверждения этого аудита.