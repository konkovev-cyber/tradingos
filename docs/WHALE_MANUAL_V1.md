# WHALE_MANUAL_V1.md — Architecture (Phase 0/1)

_Дата: 2026-08-24. Статус: ARCHITECTURE ONLY — код detector не написан, реальные ордера НЕ отправляются. Phase 2 (детектор) после owner подтверждения аудита (`docs/WHALE_DATA_AUDIT.md`)._

## 1. ЦЕЛЬ

Отдельный ручной контур: отслеживать observable whale/money-flow footprints, оценивать вероятность продолжения/разворота, отправлять карточки в Telegram для РУЧНОЙ двухступенчатой конфирмации владельцем. НЕ обещать 100%, НЕ копировать слепо крупные сделки, НЕ торговать автоматически, НЕ трогать AUTO/control-state напрямую.

## 2. АРХИТЕКТУРА (как в промте — отдельный контур, AUTO не участвует)

```
                    ┌─────────────────┐
                    │ MARKET DATA     │  microstructure 30s / trade tape BTC / derivatives 5m / funding 5m
                    └────────┬────────┘
                             ↓
                    WHALE DETECTOR      (Phase 2 — не реализован)
                             │
                             ↓
                 WHALE CLASSIFIER       (ACCUMULATION / DISTRIBUTION / MOMENTUM / ABSORPTION / EXHAUSTION / UNKNOWN)
                             │
                             ↓
                    WHALE SCORE 0–100
                             │
                             ↓
                   RISK CALCULATOR      (leverage-safe, max-loss $0.50, lq-buffer)
                             │
                             ↓
                       TELEGRAM CARD     (WHALE SIGNAL + кнопки)
                             │
                             ↓
                 OWNER 2-STEP CONFIRM    (ms_* reuse: card → CONFIRM → amt → execute)
                             │
                             ↓
                   MANUAL EXECUTOR       (_place_market_order reuse: SL/TP inline, set_leverage 5x)
                             │
                             ↓
                        EXCHANGE         (Bybit)
                             │
                             ↓
                SL/TP VERIFY + LEDGER    (reality_guardian MANUAL-track + whale ledger)
```

AUTO ENGINE ──✗ не участвует.

## 3. ПЕРЕИСПОЛЬЗОВАНИЕ (100% подтверждено аудитом)

| Компонент | Файл | Как используется | Изменения нужны |
|---|---|---|---|
| 2-step confirm + кнопки | `telegram_control/manual_signal.py` (`ms_open_`→gate→`ms_exec_`→`ms_amt_`) | Полный reuse для whale | Шаблон карточки `_format_signal` — whale-специфичный |
| Signal card persistence | `_send_signal_card` + `SIGNAL_SENT` journal | Кнопки переживают рестарт | Нет |
| Manual executor | `_place_market_order` | SL/TP inline + set_leverage 5x + journal | Whale risk sizing (лимиты) |
| Order dedup | `core/order_dedup.py` | decision_id на каждый whale signal | **ОБЯЗАТЕЛЬНО ставить decision_id** (сейчас manual его не ставит — лог пуст) |
| Singleton | `core/singleton.py` | flock от дублей сканера | Нет |
| Guardian SL/TP | `guardian/reality_guardian.py` `_set_trading_stop` + MANUAL track | Защита позиций | Нет |
| Telegram proxy | `socks5://127.0.0.1:1080` | Обязателен | Нет (BUG #11 fix) |
| Risk limits | `strategies/deposit_guard.py` | **ГЛОБАЛЬНЫЕ** — нужен отдельный профиль | Новый риск-файл WHALE_MANUAL |
| Control plane | `core/control_plane.py` (в плане) | Только для auth state transitions | N/A (WORKSTREAM B) |

## 4. ДАННЫЕ (вода из WHALE_DATA_AUDIT.md)

- **BTC**: trade tape 43 дня (tick: price/qty/side) + microstructure 30s + derivatives + funding → полноценный whale detector.
- **ETH/SOL**: microstructure 30s (book imbalance + taker delta) + derivatives + funding; **tape НЕТ** → агрессивность считать из microstructure taker (суррогат) или расширить collector (owner approval).
- **Liquidations**: DEAD (DATA GAP) — сигналы с liquidation-подтверждением недоступны до фикса collector.

## 5. WHALE DETECTOR (Phase 2 — после подтверждения аудита)

Поля события (event): символ, время, направление, flow_size, relative_flow_size, aggressor (from tape/micro), book_state (imb, depth), OI, funding, liquidations (если есть), price_before/after, MFE/MAE, future_return/R.

Кандидат-параметры (для последующего бэктеста через canonical sim — НЕ thresholds заранее):
- крупная aggressive-сделка / burst (относительно нормального распределения qty символа)
- повтор направления в коротком окне
- book-реакция (imb/depth изменение + price response)
- OI подтверждение (price↑ + OI↑ + aggressive BUY↑ = интереснее; price↓ + OI↑ + aggressive SELL↑ аналогично)
- absorption (крупный BUY flow + цена почти не растёт + sell-liquidity пополняется → возможен разворот)

## 6. WHALE SCORE (0–100) — reproducible

Не произвольная сумма. Каждый компонент: source / weight / evidence / statistical justification / sample size. Score показывается вместе с CONFIDENCE + EVIDENCE COUNT + DATA QUALITY (включая DATA GAP где применимо).

## 7. LEVERAGE — безопасный, не max exchange

Показ: exchange_max / system_max / risk_safe / recommended. Главный параметр — **MAX LOSS ($0.50)**, не плечо. Плечо подбирается под стоп-дистанцию и ликвидационный буфер. Callbacks не могут превысить hard limit.

## 8. ТЕЛЕГРАМ-КАРТОЧКА (формат определен в промте)

```
🐋 WHALE SIGNAL / HH:MM UTC
BTCUSDT PERPETUAL / LONG-SHORT-WAIT-ABSORPTION
WHALE SCORE xx/100 · CONFIDENCE · HORIZON
MONEY FLOW: aggressive flow / relative / OI / book / liq (или DATA GAP) / funding / price response
WHY THIS SIGNAL: 1..3 пункта
ENTRY / INVALIDATION / STOP / TARGET1 / TARGET2
RISK: recommended lev / system max / exchange max / margin / notional / MAX LOSS / lq price / lq buffer
⚠️ WHALE ≠ GUARANTEE · MANUAL · OWNER CONFIRMATION
[🟢 OPEN LONG] [🔴 IGNORE] [📊 DETAILS]
```

## 9. SAFETY INVARIANTS (обязательные)

- UNKNOWN/MISSING/ERROR/NaN НЕ превращаются в сигнал; данные отсутствуют → DATA GAP.
- WHALE-MANUAL НЕ пишет `trading_mode.json` напрямую; любые state transitions — через control plane (план).
- Никаких реальных ордеров без owner 2-step confirm; SL/TP обязательны (иначе fail-closed).
- Повторный callback/рестарт не создаёт второй ордер (dedup decision_id).
- Не трогает external unmanaged positions.
- Отдельный риск-профиль WHALE_MANUAL (max_lev=10, default=3, max_risk=$0.50, max_pos=2) — HARD, не обходится кнопкой.

## 10. ВАЛИДАЦИЯ (canonical gate — только после накопления выборки в shadow)

- BATLS: shadow (NO REAL ORDERS) → canonical sim → OOS NET>costs (13 bps RT, стресс до 2×), anti-cherry, day-stability, min events.
- universe=3 → «INSUFFICIENT UNIVERSE» до расширения (owner approval).
- СРАВНЕНИЕ с control group (random/normal events) — whale event > control после costs.
- Если нет преимущества после costs → честный вердикт NO WHALE EDGE.

## 11. ФАЙЛЫ (Phase 2+ — создаются после owner confirmation)

```
docs/WHALE_DATA_AUDIT.md            (Готово — этот аудит)
docs/WHALE_MANUAL_V1.md             (Готово — этот архитектурный документ)
tradingos_lab/edge_factory/whale/
  whale_detector.py                 (детектор event'ов)
  whale_classifier.py               (accumulation/distribution/momentum/absorption/exhaustion/unknown)
  whale_score.py                    (0–100 reproducible)
  whale_risk.py                     (риск/плечо/ликвидация — отдельный профиль)
  whale_telegram.py                 (формат карточки + 2-step confirm)
  whale_ledger.py                   (signals/executions/outcomes jsonl)
  whale_shadow.py                   (shadow-раннер, NO ORDERS)
tests/test_whale_*.py               (классификация/score/риск/safety/idempotency)
config/whale_manual.json            (риск-профиль WHALE_MANUAL)
```

## 12. НЕ ДЕЛАЕМ

❌ Копирование wallets ❌ «Binance bought → LONG» ❌ любая большая сделка = кит ❌ обещание 100% ❌ автоторговля ❌ 50–100x ❌ смешение с AUTO ❌ старт со сотен альткоинов ❌ синтетические liquidation данные ❌ НЕ повторять OMF-001/002/RR (все REJECT) без нового info source.

## 13. ФАЗЫ (порядок по промту, каждая gate на owner approval)

```
PHASE 0: AUDIT existing system         ✅ (этот документ + WHALE_DATA_AUDIT.md)
PHASE 1: DATA AVAILABILITY             ✅ (WHALE_DATA_AUDIT.md §2-7)
PHASE 2: EVENT DETECTOR                ⏳ ждёт owner подтверждение аудита
PHASE 3: CLASSIFICATION
PHASE 4: CANONICAL VALIDATION (OOS, costs, control, по§10)
PHASE 5: SHADOW TELEGRAM (кнопка OPEN = только подтверждение, НЕ ордер)
PHASE 6: OWNER REVIEW (events/OOS/NET/PF/WR/MFE/MAE/cost-sens/top10) → PASS/REJECT/DATA GAP/INSUFFICIENT SAMPLE
PHASE 7: MANUAL EXECUTION             только после отдельного owner approval
```