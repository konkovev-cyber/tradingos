# Funding Rate BTC Perp — Due Diligence

**Status:** PIPELINE (R105: НЕ становится ACTIVE до завершения grid_v3_pro)
**Source:** He, Manela, Ross, von Wachter (2024) arXiv:2212.06888
**Author:** Chief Quant Reviewer
**Date:** 2026-07-21

---

## 1. Возможно ли реализовать стратегию на BingX API?

**ДА.** BingX — топ-5 CEX по объёму BTC perp, имеет:
- Spot + Perp (USDT-margined) на одном аккаунте
- Funding каждые 8h
- API endpoints для funding history, open interest, mark/index price
- Минимальный контракт: $50-100 notional для altcoin perps

**Edge mechanism (per He 2024):**
- Long spot + Short perp = delta-neutral, получает funding каждые 8h
- Funding > теоретический bound → входить
- Funding < bound → выходить

---

## 2. Какие данные нужны

| Данные | Где взять | BingX endpoint | Период |
|--------|-----------|----------------|--------|
| Funding history | BingX API | `/openApi/swap/v1/market/fundingRateHistory` | 2018+ (доступно) |
| Mark price | BingX API | `/openApi/swap/v1/market/markPrice` | real-time |
| Premium index | BingX API | `/openApi/swap/v1/market/premiumIndex` | real-time |
| Open interest | BingX API | `/openApi/swap/v1/market/openInterest` | daily |
| Spot price | BingX API | `/openApi/spot/v1/ticker/24h` | 2018+ |
| Volume | BingX API | `/openApi/swap/v1/market/kline` | 2018+ |
| Liquidations | BingX WebSocket | WS trade stream | real-time |

---

## 3. Что уже есть в TradingOS

**Уже работает (по memory):**
- `BingX Read Adapter` — читает positions, balance, openOrders
- ubot_bingx — `/api/status`, `/api/capital` на localhost:9091
- Отдельные скрипты funding collector (через search)

**Не реализовано:**
- `funding_rate_history` collector в TradingOS kernel
- `open_interest` persistent storage
- `premium_index` collector
- Replay engine с funding data

---

## 4. Каких данных нет

**Critical gap:**
- Нет persistent DB с funding history за 2018-2024
- Нет OHLCV spot+perp dataset с funding overlay
- Нет backtester с funding cashflow

**Решение:** Скачать вручную через BingX API или использовать готовые датасеты:
- Coinalyze.net (has BTC perp funding 2019+)
- Glassnode (paid)
- CryptoQuant (paid)

---

## 5. Можно ли проверить без нового торгового движка?

**ДА, но минимальная обвязка нужна:**
1. Data collector: funding_rate_history + premium_index (1 файл, ~100 строк)
2. Replay script: simulate long-spot-short-perp с funding cashflow (~50 строк)
3. PnL calc + Sharpe (можно через pandas)

НЕ нужно: position manager, risk engine, ордера. Это чистый backtest.

---

## 6. Минимальный Replay

**Inputs:**
- Funding rate (8h bars) за 2020-2024
- Spot price (1d bars)
- Entry signal: funding > 0.03% per 8h (например)

**Logic:**
```
for each 8h bar:
    if funding > 0.0003:
        enter long-spot-short-perp
        mark_to_market on spot and perp
        collect funding payment
    elif funding < 0:
        close
    else:
        hold
```

**Outputs:**
- Equity curve
- Sharpe ratio
- Max DD
- Annual return

---

## 7. Минимальный объём истории

- **3 года** минимум (2021-2024) — покрывает bear+bull+flat
- **5 лет** идеально (2019-2024)
- **1 год** недостаточно — один regime

---

## 8. Критерии PASS

- Sharpe > 0.8 (retail-cost adjusted)
- Max DD < 30%
- Положительный return в 4/5 годов
- Edge сохраняется после 2022 (после публикации He 2024)
- Минимум 100 входов

---

## 9. Критерии STOP

- Sharpe < 0.3 → STOP (noise)
- Max DD > 50% → STOP
- Отрицательный return в 3+ годах → STOP
- < 50 входов → INSUFFICIENT DATA

---

## 10. Что может сделать гипотезу ложной

1. **Decay** — He 2024 сами пишут что deviations падают ~11% в год. Edge может уменьшиться.
2. **Crowded trade** — после публикации arXiv:2212.06888 (Aug 2024) много ритейла начало делать то же самое
3. **Funding crush** — на бычьем рынке funding растёт, но spot идёт вверх быстрее → long spot проигрывает perp
4. **Liquidation risk** — если spot+perp на одной бирже, perp можно ликвидировать даже при delta-neutral (margin call)
5. **Borrowing** — short perp маржа съедает $$, при низком funding — убыток
6. **Bybit/Binance outages** — historical data gaps

---

## STATUS: PIPELINE

**Текущая фаза:** Due Diligence ✓
**Следующая фаза:** Replay (3 дня)
**Когда ACTIVE:** только после STOP/PASS по grid_v3_pro

Не начинать Replay пока:
- grid_v3_pro не показал 30+ сделок live
- ИЛИ явно STOP/PASS
