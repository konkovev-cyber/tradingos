# План: Улучшение ручного сигнального контура (SIGNAL_ONLY) TradingOS

## Назначение: документ для реализации другим ИИ (DeepSeek)

Этот план содержит 5 задач improvements ручного контура TradingOS. 
Каждая задача — самостоятельная, может быть реализована отдельно.
Порядок реализации: 1 → 2 → 3 → 4 → 5 (от критичного к UX).

---

## КОНТЕКСТ ДЛЯ ИСПОЛНИТЕЛЯ

TradingOS — система автоторговли крипто-perpetual на Bybit. Проект: `/root/tradingos`.

**Два контура:**
1. **AUTO** — `tradingos-reality.service`, `data/run_observation.py` → `strategies/trade_executor.py` → `guardian/reality_guardian.py`. Автоматически открывает/закрывает позиции. 120 символов.
2. **MANUAL (SIGNAL_ONLY)** — `trading-control.service`, `telegram_control/bot.py` + `telegram_control/manual_signal.py` + `signals/manual_scanner.py`. Сканирует 18 символов, шлёт карточки в Telegram, пользователь нажимает кнопку «ОТКРЫТЬ», вводит сумму → реальный ордер на Bybit. `dry_run: false`, `allow_real: true`.

**Ключевые файлы:**
- `telegram_control/bot.py` — главный бот (обработчики команд, callback'ов, polling)
- `telegram_control/manual_signal.py` — ручной контур: карточки, кнопки, `_place_market_order`, `handle_text`, `callback_handler_manual`
- `signals/manual_scanner.py` — сканер: `score_symbol()` → оценка 0-100, SL/TP, gate
- `guardian/reality_guardian.py` — LIVE profit protection (BE@0.8R, Partial@1.0R, Tight@1.5R, Trail@0.5R, GIVEBACK-TAKE)
- `operations/manual_session.json` — конфиг ручного контура
- `operations/trading_mode.json` — конфиг AUTO контура

**Текущие параметры ручного контура:**
```json
{
  "dry_run": false,
  "allow_real": true,
  "min_signal_score": 70,
  "scan_interval_min": 20,
  "symbols": [18 символов: BTC, ETH, SOL, XRP, DOGE, BNB, ADA, NEAR, SUI, LINK, ONDO, WLD, APT, AAVE, AVAX, UNI, LTC, OP],
  "risk_per_trade_usd": 0.5,
  "max_positions": 2,
  "min_rr": 1.2
}
```

**Credentials (API ключи Bybit):**
- Рабочий файл: `/root/trading_brain_v4/research/execution/.env` (переменные `BYBIT_API_KEY`, `BYBIT_API_SECRET`)
- НЕ использовать `load_dotenv` — он не перезаписывает env vars (баг 10003). Чтение файла напрямую.

**Как MANUAL-позиции открываются сейчас:**
1. `manual_scanner.scan_all()` → `score_symbol()` → сигнал ≥70
2. `_send_signal_card()` в Telegram → кнопка «🟢 ОТКРЫТЬ» (callback `ms_open_{symbol}`)
3. `callback_handler_manual` → `_gate_checks()` → confirmation card → «✅ ДА, ОТКРЫТЬ» (`ms_exec_{symbol}`)
4. В LIVE-режиме: `_awaiting_amount[user_id] = {symbol, side, sl, tp}` → бот: «Введите сумму в USD»
5. `handle_text()` → парсит сумму → `_place_market_order(symbol, side, usd, sl, tp)` → raw signed POST `/v5/order/create`
6. Позиция открывается на Bybit с SL/TP

**Что НЕ работает сейчас (проблемы для исправления):**
- Guardian НЕ управляет MANUAL-позициями (нет BE/Partial/Tight/Trail)
- MANUAL-позиции нельзя закрыть из бота (только `/panic` — закрывает ВСЁ)
- Сигналы не имеют expiry (висят бесконечно в `_last_signals`)
- Dedup подавляет повторные сигналы пожизненно (до рестарта)
- Нет уведомлений о SL/TP срабатывании на MANUAL-позициях

---

## ЗАДАЧА 1: Guardian для MANUAL-позиций (КРИТИЧНО)

### Проблема
`guardian/reality_guardian.py:_process_position()` управляет только позициями из `reality_state.json` (AUTO-контур). MANUAL-позиции открываются через `_place_market_order` (raw POST), но не регистрируются в guardian state. Guardian видит их при опросе `get_positions()` биржи, но не применяет правила BE/Partial/Tight/Trail (нет state-записи).

### Решение
При открытии MANUAL-позиции через `_place_market_order` — регистрировать её в guardian state.

**Файл:** `telegram_control/manual_signal.py`, функция `_place_market_order` (~строка 700)

После успешного ордера (строка `return {"ok": True, ...}`), добавить:

```python
# Регистрация в guardian state (чтобы BE/Partial/Tight/Trail применялись)
try:
    import json as _json
    _state_path = "/root/tradingos/guardian/reality_state.json"
    _state = _json.loads(open(_state_path).read()) if Path(_state_path).exists() else {}
    _state[symbol] = {
        "be_fired": False,
        "partial_fired": False,
        "tight_fired": False,
        "mfe_peak": 0.0,
        "mae_trough": 0.0,
        "entry_to_sl_risk": abs(sl - res["price"]) if res.get("price") else 0,
        "side": "Buy" if side.upper().startswith("LONG") or side.upper() == "BUY" else "Sell",
        "entry": res.get("price", 0),
        "size": qty,
        "sl_initial": sl,
        "tp_initial": tp,
        "source": "MANUAL",  # пометка — это ручная позиция
        "entry_time": time.time(),
    }
    open(_state_path, "w").write(_json.dumps(_state, ensure_ascii=False))
except Exception as e:
    logger.warning(f"Guardian state registration failed: {e}")
```

**Дополнительно:** в `guardian/reality_guardian.py:_process_position()` добавить пометку источника:
- Перед `is_new_position` блоком (строка ~508): если `sym_state` новый, проверить, не MANUAL ли это (по `pos["size"]` или символу в `manual_signals.jsonl`). Помечать `sym_state["source"] = "MANUAL"` или `"AUTO"`.
- При классификации закрытия: MANUAL-позиции писать в `manual_signals.jsonl` (не `trade_results.jsonl`).

**Проверка:**
1. Открыть MANUAL-позицию через кнопку
2. Проверить `reality_state.json` — символ должен появиться с `source: "MANUAL"`
3. Ждать BE/Partial/Tight (когда цена дойдёт до +0.8R/+1.0R/+1.5R)
4. Guardian должен двигать SL на MANUAL-позиции

---

## ЗАДАЧА 2: TP reachability % в карточке сигнала

### Проблема
Карточка показывает TP, но не показывает, насколько он достижим исторически.

### Решение
В `signals/manual_scanner.py:score_symbol()` уже есть `tp_unreachable` (bool) и `range_30d_high`/`range_30d_low`. Нужно добавить процент баров, касавшихся TP.

**Файл:** `signals/manual_scanner.py`, функция `score_symbol()`, после расчёта `range_30d_high` (~строка 197)

Добавить расчёт:
```python
# TP reachability: сколько % баров за 30D касались уровня TP
tp_reachability_pct = 0.0
try:
    if side == "LONG" and final_tp > 0:
        above = sum(1 for x in rows30 if float(x[2]) >= final_tp)
    elif side == "SHORT" and final_tp > 0:
        above = sum(1 for x in rows30 if float(x[3]) <= final_tp)
    tp_reachability_pct = round(above / max(len(rows30), 1) * 100, 1)
except Exception:
    pass
```

Добавить в return dict: `"tp_reachability_pct": tp_reachability_pct`

**Файл:** `telegram_control/manual_signal.py`, в `_build_card()` (~строка 108-127)

После строки `f"🎯 TP: <code>{_fmt_price(tp1)}</code>"` добавить:
```python
tp_reach = sig.get("tp_reachability_pct", 0)
reach_emoji = "🟢" if tp_reach >= 50 else ("🟡" if tp_reach >= 20 else "🔴")
lines.append(f"{reach_emoji} TP достижимость: {tp_reach:.0f}% (30D)")
```

**Проверка:** открыть `/signals` → карточка должна показывать «🟢 TP достижимость: 63% (30D)» или «🔴 TP достижимость: 8% (30D)»

---

## ЗАДАЧА 3: Кнопка закрытия MANUAL-позиции

### Проблема
`/panic` закрывает ВСЕ позиции. Закрыть отдельную MANUAL-позицию из бота нельзя.

### Решение
В `/positions` добавить кнопку `[🔴 CLOSE]` рядом с каждой MANUAL-позицией.

**Файл:** `telegram_control/bot.py`, функция `cmd_positions()` (~строка 189-217)

Для каждой позиции добавить кнопку:
```python
InlineKeyboardButton("🔴 CLOSE", callback_data=f"close_yes_{symbol}")
```

**Важно:** существующий callback `close_yes_{symbol}` уже обрабатывается в `callback_handler` (строки ~240-264 — подтверждение закрытия). Нужно убедиться, что он вызывает `_close_position` из guardian (reduceOnly market order).

**Файл:** `telegram_control/bot.py`, `callback_handler` (~строка 240)

При `close_yes_{symbol}`:
```python
if data.startswith("close_yes_"):
    symbol = data.split("_", 2)[2]
    # Получить размер позиции
    from tradingos.strategies.bybit_position_check import get_open_positions_with_side
    positions = get_open_positions_with_side()
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if pos:
        side = pos["side"]
        size = float(pos.get("size", 0))
        # Закрыть через guardian _close_position (reduceOnly)
        sys.path.insert(0, "/root/tradingos")
        from guardian.reality_guardian import _close_position
        ok = _close_position(symbol, side, size)
        if ok:
            await query.message.reply_text(f"✅ {symbol} закрыта")
        else:
            await query.message.reply_text(f"❌ Ошибка закрытия {symbol}")
```

**Проверка:** `/positions` → кнопка `[🔴 CLOSE]` → подтверждение → позиция закрыта на Bybit

---

## ЗАДАЧА 4: Expiry сигналов + уведомления о SL/TP

### Проблема A: Сигналы не истекают
`_last_signals[symbol]` хранится бесконечно. Цена могла уйти на 5%, но кнопка всё ещё предлагает старый вход.

### Решение A: TTL сигналов 60 минут

**Файл:** `telegram_control/manual_signal.py`

В `_last_signals` хранить timestamp добавления:
```python
_last_signals[symbol] = {**sig, "_stored_at": time.time()}
```

В `callback_handler_manual`, при `ms_open_` / `ms_exec_`:
```python
sig = _last_signals.get(sym)
if sig and time.time() - sig.get("_stored_at", 0) > 3600:  # 60 минут
    sig = None  # истёк
    _last_signals.pop(sym, None)
```

### Проблема B: Нет уведомлений о SL/TP на MANUAL-позициях

### Решение B: Фоновый мониторинг MANUAL-позиций

**Файл:** `telegram_control/manual_signal.py`, добавить новую функцию `background_position_monitor()`

```python
async def background_position_monitor():
    """Мониторинг MANUAL-позиций: SL/TP срабатывание → уведомление."""
    _tracked = {}  # symbol → entry/size/side
    while True:
        try:
            import httpx
            # Получить открытые позиции
            with httpx.Client(timeout=15) as c:
                r = c.get("https://api.bybit.com/v5/position/list",
                          params={"category":"linear","settleCoin":"USDT"})
                positions = (r.json().get("result") or {}).get("list") or []
            current_syms = {p["symbol"] for p in positions}
            # Проверить: отслеживаемые закрылись?
            for sym, info in list(_tracked.items()):
                if sym not in current_syms:
                    # Позиция закрылась (SL/TP/manual)
                    await _send_manual_close_notification(sym, info)
                    _tracked.pop(sym, None)
            # Добавить новые MANUAL-позиции в трекинг
            for sym in current_syms:
                if sym not in _tracked:
                    # Проверить, MANUAL ли (есть в manual_signals.jsonl)
                    # ...
                    pass
        except Exception:
            pass
        await asyncio.sleep(60)
```

Регистрация в `bot.py`:
```python
# В main(), рядом с background_scan_loop
from telegram_control.manual_signal import background_position_monitor
asyncio.create_task(background_position_monitor())
```

**Проверка:** открыть MANUAL-позицию → дождаться SL/TP → бот присылает карточку закрытия

---

## ЗАДАЧА 5: Dedup с TTL

### Проблема
В `background_scan_loop` (manual_signal.py:~580) есть `sent_keys` set — после сигнала SOL LONG бот НИКОГДА не предложит SOL LONG снова до рестарта процесса.

### Решение
Заменить set на dict с timestamp: `{key: stored_at}`, чистить записи старше 2 часов.

**Файл:** `telegram_control/manual_signal.py`, функция `background_scan_loop()` (~строка 580)

```python
sent_keys: dict[str, float] = {}  # key → timestamp, вместо set

# В цикле:
now = time.time()
# Очистка старше 2ч
sent_keys = {k: v for k, v in sent_keys.items() if now - v < 7200}

key = f"{sig['symbol']}_{sig['side']}"
if key in sent_keys:
    continue  # уже отправлен за последние 2ч
sent_keys[key] = now
```

**Проверка:** получить сигнал SOL LONG → через 2+ часа получить новый SOL LONG (если условия снова ≥70)

---

## ОБЩИЕ ТРЕБОВАНИЯ

### НЕ МЕНЯТЬ:
- AUTO-контур (`data/run_observation.py`, `strategies/trade_executor.py`)
- Guardian логику (BE/Partial/Tight/Trail пороги)
- Signal Generator (`signals/signal_generator.py`)
- `operations/trading_mode.json` (AUTO config)
- Риск-параметры ($0.50 risk, max 2 positions)

### ПРАВИЛА:
- `parse_mode="HTML"` — ВСЕ динамические значения через `_esc()` (защита от `<`, `&`)
- Bybit API: raw signed POST для ордеров, recv_window в payload
- Credentials: `/root/trading_brain_v4/research/execution/.env` (НЕ load_dotenv)
- После каждого изменения: `python3 -m py_compile <file>` + `systemctl restart trading-control`
- MANUAL-позиции: отдельный журнал `memory/manual_signals.jsonl`, НЕ в AUTO stats

### ПОРЯДОК ТЕСТИРОВАНИЯ:
1. После каждой задачи — `python3 -m py_compile` проверка
2. `systemctl restart trading-control && sleep 10 && systemctl is-active trading-control`
3. Проверить в Telegram: `/signals`, `/positions`, кнопки
4. Для задач 1 и 3 — проверить на реальной Bybit позиции
