# Order Management v1 — Multi-Leg + Multi-Symbol Grid (T1.8)

## Принцип (по владельцу): 12-20 ордеров одновременно, не по одной монете

Поведение, которое хочет владелец: в любой момент времени на бирже висит сетка
из 12-20 LIMIT ордеров на разных монетах и разных точках входа. Это даёт:
- Постоянный поток сигналов → fills (не ждать пока одна монета "даст" вход)
- Диверсификация: одна монета двинулась, другая — нет; net positive
- Лёгкая коррекция: amend 1-2 ордера под текущую картину, остальные не трогаем
- Скалирование: можно добавлять/убирать legs по мере появления новых паттернов

## Структура: 3 уровня по разным символам

**Tier 1 — Core positions (5-7 legs)**
- DN-sweep на 5-7 топ-whitelist монетах (BTWUSDT, WLDUSDT, TUTUSDT, STRKUSDT, OPUSDT, GMTUSDT, INXUSDT)
- LIMIT post-only на entry_zone (close бара sweep), expires 30 мин
- При fill → bracket SL (ниже sweep low) + TP (2R)
- Hold 4h

**Tier 2 — Aggressive fills (5-7 legs)**
- DN-sweep на остальных 7-9 монетах whitelist (ADAUSDT, ZKUSDT, ONDOUSDT, DOGEUSDT, ETHUSDT, SOLUSDT, VETUSDT)
- LIMIT post-only на entry_zone - 0.3% (более глубокая точка)
- Если цена не дошла до tier 1 за 30 мин → tier 2 продолжает висеть

**Tier 3 — Patience (3-5 legs)**
- DN-sweep на 3-5 монетах, где sweep был слабым (low confidence < 0.85)
- LIMIT post-only на entry_zone + 0.5% (поздний вход после отката)
- Expires 1 час

**Итого в любой момент: 5-7 + 5-7 + 3-5 = 13-19 legs**

## Lifecycle одного leg

```
SCANNED  ──>  PLACED  ─┬─>  FILLED  ──> BRACKET_PLACED  ─┬─>  CLOSED_TP
                      │                                  ├─>  CLOSED_SL
                      │                                  └─>  CLOSED_TIME
                      │
                      ├─>  AMENDED  ──> (новый PLACED с новой ценой)
                      │
                      ├─>  CANCELLED (структура сломалась, перешли к новой точке)
                      │
                      └─>  EXPIRED (time_in_force истёк без fill)
```

Каждый leg имеет:
- `order_link_id = "dn-sweep-{symbol}-{signal_ts}-{tier}-{seq}"`
- `signal_ts` — когда был обнаружен DN-sweep
- `placed_ts` — когда размещён LIMIT
- `entry_price` — прогнозная точка входа (close бара sweep ± tier offset)
- `tier` (1/2/3)
- `state` (PLACED / FILLED / BRACKET / AMENDED / CANCELLED / EXPIRED)

State persists в `/root/tradingos/orders/state.json`.

## Multi-symbol scanning logic

Exector сканирует ВСЕ 14 whitelist символов КАЖДЫЕ 30 секунд. На каждый новый DN-sweep:
1. Проверяет: сколько legs уже PLACED по этой монете? Если ≥2 → cancel старейший, ставим новый
2. Определяет tier:
   - Если сигнал strong (conf > 0.9) → Tier 1
   - Если normal (conf 0.7-0.9) → Tier 2 (deep entry)
   - Если weak (conf 0.5-0.7) → Tier 3 (patience)
3. Place LIMIT post-only на entry_price

## On-the-fly amend (max 2 раза за life)

Exector мониторит PLACED legs. На каждом баре (60s):
- **Approach** (price within 0.2% of entry_zone, no momentum shift): no action
- **Momentum против сигнала** (close > 0.5% past entry_zone за 4 свечи):
  - amend leg: new_price = current close (chase market)
  - только 1 amend за leg lifetime
- **Momentum в направлении** (price moves away without fill):
  - amend leg: new_price = current close ± tier offset
  - chase до 1R distance
- **Структура инвалидирована** (sweep low broken again, structure collapse):
  - cancel leg, mark CANCELLED
- **Amend budget exhausted** (2 amends already used):
  - no further amend; let expire or fill

## Bracket после fill (SL/TP attached)

Когда leg fills:
1. State → FILLED, capture `fill_price`, `fill_ts`
2. Через `set_trading_stop(symbol, stop_loss=..., take_profit=...)`:
   - SL = structure_low - 0.3% (ниже sweep wick с buffer)
   - TP = entry + 2R (entry = fill_price)
3. State → BRACKET_PLACED
4. Periodic check (every 60s):
   - Если +1R → set_trading_stop с SL = entry (breakeven)
   - Если +2R → trailing stop (SL = max(prev_sl, current_price - 1R))
5. State → CLOSED_* когда позиция закрыта

## Multi-leg coordination (OCO simulation)

Bybit v5 не имеет нативных OCO. Симулируем:
- При fill одного leg → check `get_open_orders(symbol)`:
  - Если есть другие legs по этому символу в state PLACED → cancel все
  - Это предотвращает over-exposure на одной монете
- При fill → check global exposure (max 4 positions): если ≥4 → cancel старейший FILLED (по возрасту)

## Risk management

- Per-leg risk: 0.5% equity ($500 on $100k)
- Max concurrent positions: 4 (per trading_mode.json max_positions)
- Max legs PLACED simultaneously: 20 (Tier 1: 7, Tier 2: 7, Tier 3: 5 + buffer)
- Max amends per leg: 2 (защита от over-trading)
- Per-symbol max legs PLACED: 2 (если 2 → cancel старейший при новом сигнале)

## Amend rules summary

| Condition                           | Action          |
|-------------------------------------|-----------------|
| Time to expiry < 5 min              | no amend, let expire |
| Amend count >= 2                     | no amend, let expire |
| Price within 0.2% of entry_zone     | no amend |
| Price moved 0.5% against signal     | amend: chase market (1 only) |
| Price moved 0.5% with signal (away) | amend: extend to 1R distance (1 only) |
| Structure invalidated               | cancel immediately |
| New DN-sweep on same symbol         | cancel old, place new |
| Different signal on same symbol     | cancel old, place new |

## Integration с bandit

Bandit всё ещё работает:
- После `scan_symbol` finds DN-sweep → `bandit.get_bet_size(regime)` → if >0, leg placed
- После `update(regime, win/loss)` обновляет posterior

Но bandit контролирует WHETHER leg placed, не just price.

## Files

- `/root/tradingos/orders/state.json` — persistent state всех legs
- `/root/tradingos/orders/leg_manager.py` — multi-leg coordinator
- `/root/tradingos/orders/order_executor.py` — Bybit API calls (place, amend, cancel, set_trading_stop)
- `/root/tradingos/orders/event_log.jsonl` — все events (placed/filled/amended/cancelled/closed)
- Integration в `dn_sweep_live_detector.py` — после детекта сигнала → leg_manager.place_leg()

## Live vs paper

Пока `live_trading_enabled: false`:
- Paper-trade: leg_manager.place_leg() логирует intent, не отправляет API call
- Все state transitions симулируются по цене из get_current_price
- После 2-4 недели данных на paper → если WR/avg_pnl держится → owner approval → live

## Roadmap (T1.8)

1. ✅ Схема + state machine + amend rules
2. ⏳ Leg manager (state.json persistence, transitions)
3. ⏳ Order executor (Bybit API place/amend/cancel/set_trading_stop)
4. ⏳ Integration в dn_sweep_live_detector
5. ⏳ Paper-trade validation (2-4 недели)
6. � Owner approval → live
