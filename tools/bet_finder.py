#!/usr/bin/env python3
"""
bet_finder.py — Генератор ручных ставок (модель владельца).

Модель: ставка 20% депо (маржа), плечо 10x, цель = 3× от ставки.
При 10x: 3× от маржи = движение цены на +30%/10 = ±3% против входа.
  → Ищем сетапы где SL-дистанция ≤ 1.5%, TP = 3×SL (~4.5%), и TP
    реален (внутри 30D диапазона, с касаниями за месяц).

Критерии (все обязательны):
  1. Тренд D1 + H4 + H1 в одну сторону (3 таймфрейма согласны)
  2. Ликвидность: median H1 notional ≥ $100k/час
  3. Движение есть: 3H range ≥ 0.5%
  4. Вход = структура (EMA20 H1 + 0.1 ATR buffer), не рынок
  5. TP 3R внутри 30D диапазона + касания уровня есть
  6. RSI не в экстремуме против входа

Вывод: TOP-3 сетапа с полным планом ставки.
"""
import sys
sys.path.insert(0, '/root')
sys.path.insert(0, '/root/tradingos')

import httpx
from tradingos.signals.manual_scanner import _klines, _ema, _rsi_wilder

EQUITY = 179_000.0          # demo equity
BET_PCT = 20.0              # % of equity as MARGIN
LEV = 10
TARGET_MULT = 3.0           # want 3x the bet
MIN_RR = 3.0                # TP = 3 × SL distance
MAX_SL_PCT = 1.5            # SL не шире 1.5% (иначе 3R = 4.5%+ — слишком далеко)


def swing_levels(h1, atr):
    """Structural levels: EMA20 magnet, recent pullback low, swing high."""
    closes = [b['close'] for b in h1]
    e20 = _ema(closes, 20)[-2]
    price = closes[-2]
    lows = [b['low'] for b in h1[-48:]]
    highs = [b['high'] for b in h1[-48:]]
    return price, e20, min(lows[-12:]), max(highs), max(lows)


def trend_tf(closes, n=50):
    e20 = _ema(closes, 20)[-2]
    e50 = _ema(closes, 50)[-2]
    cl = closes[-2]
    if cl > e20 > e50:
        return "UP"
    if cl < e20 < e50:
        return "DOWN"
    return "MIXED"


def analyze(client, sym):
    h1 = _klines(client, sym, '60', 200)
    h4 = _klines(client, sym, '240', 120)
    d1 = _klines(client, sym, 'D', 60)
    if not (h1 and h4 and d1):
        return None

    # Liquidity
    vols = [(b['close'] * b['volume']) for b in h1]
    med_notional = sorted(vols)[len(vols) // 2]
    if med_notional < 100_000:
        return None

    # Movement
    rng = max(b['high'] for b in h1[-3:]) - min(b['low'] for b in h1[-3:])
    move_pct = rng / h1[-1]['close'] * 100
    if move_pct < 0.5:
        return None

    # Multi-TF trend
    c1 = [b['close'] for b in h1]
    c4 = [b['close'] for b in h4]
    cd = [b['close'] for b in d1]
    def tt(cls):
        e20 = _ema(cls, 20)[-2]
        e50 = _ema(cls, 50)[-2]
        cl = cls[-2]
        if cl > e20 > e50:
            return "UP"
        if cl < e20 < e50:
            return "DOWN"
        return "MIXED"
    t_h1, t_h4, t_d1 = tt(c1), tt(c4), tt(cd)

    side = None
    if t_h1 == t_h4 == t_d1 == "UP":
        side = "LONG"
    elif t_h1 == t_h4 == t_d1 == "DOWN":
        side = "SHORT"
    else:
        return None  # 3TF agreement required

    # ATR
    trs = []
    for i in range(1, len(h1)):
        tr = max(h1[i]['high'] - h1[i]['low'],
                 abs(h1[i]['high'] - h1[i-1]['close']),
                 abs(h1[i]['low'] - h1[i-1]['close']))
        trs.append(tr)
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0
    if atr <= 0:
        return None

    price, e20, pb_low, high48, low48_low = swing_levels(h1, 0)
    rsi = _rsi_wilder(c1)

    if side == "LONG":
        entry = e20 + 0.1 * atr
        sl_struct = pb_low - 0.15 * atr
        sl_dist = entry - sl_struct
        # RSI extremum filter: don't buy RSI > 72
        if rsi > 72 or entry >= price:
            return None
    else:
        entry = e20 - 0.1 * atr
        sl_struct = max(lows48 := [b['high'] for b in h1[-48:]][-12:] and max(h1[-48:][-12:], key=lambda b: b['high'])['high'] + 0.15 * atr, entry + 0.5 * atr)
        sl_dist = sl_struct - entry
        if rsi < 28 or entry <= price:
            return None

    sl_pct = sl_dist / entry * 100
    if sl_pct > MAX_SL_PCT or sl_pct <= 0.2:
        return None

    tp = entry + 3 * sl_dist if side == "LONG" else entry - 3 * sl_dist
    tp_pct = 3 * sl_pct

    # TP reachability: inside 30D range
    r30 = client.get("https://api.bybit.com/v5/market/kline",
                     params={"category": "linear", "symbol": sym, "interval": "60", "limit": 720},
                     timeout=15).json()
    rows30 = (r30.get("result") or {}).get("list") or []
    if len(rows30) < 100:
        return None
    hi30 = max(float(x[2]) for x in rows30)
    lo30 = min(float(x[3]) for x in rows30)
    if side == "LONG" and tp > hi30:
        return None
    if side == "SHORT" and tp < lo30:
        return None
    touches = sum(1 for x in rows30
                  if (float(x[2]) >= tp if side == "LONG" else float(x[3]) <= tp))
    if touches < 10:
        return None  # цель мертвая — не касалась

    # Bet math
    bet = EQUITY * BET_PCT / 100
    notional = bet * LEV
    qty = notional / entry
    risk_usd = sl_dist * qty
    target_profit = bet * TARGET_MULT
    print(f"\n=== {sym} {side} ===")
    print(f"  Тренд: H1={t_h1} H4={t_h4} D1={t_d1} | RSI={rsi:.0f} move3H={move_pct:.1f}%")
    print(f"  Цена сейчас:  {price:.4f}")
    print(f"  ЛИМИТКА:      {entry:.4f} ({(entry-price)/price*100:+.2f}% от текущей)")
    print(f"  SL:           {sl_struct:.4f} (-{sl_pct:.2f}%)")
    print(f"  TP (3R):      {tp:.4f} (+{(tp-entry)/entry*100*LEV:.0f}% к марже)")
    print(f"  Ставка:       ${bet:.0f} маржа × {LEV}x = ${notional:.0f} ноушнл")
    print(f"  Риск по SL:   ${risk_usd:.0f} ({risk_usd/EQUITY*100:.2f}% депо)")
    print(f"  ЦЕЛЬ:         ${target_profit:.0f} (3× ставки) — при 10x нужно движение {(3*sl_dist/entry)*100:.2f}%")
    print(f"  30D касаний TP-зоны: {touches}")
    print(f"  qty: {qty:.4f}")
    return True


SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT',
        'LINKUSDT', 'AVAXUSDT', 'SUIUSDT', 'LTCUSDT', 'ADAUSDT', 'ONDOUSDT',
        'APTUSDT', 'UNIUSDT', 'AAVEUSDT', 'OPUSDT', 'NEARUSDT', 'WLDUSDT',
        'NVDAUSDT', 'TSLAUSDT', 'AAPLUSDT', 'INTCUSDT', 'AMDUSDT']

print("=== BET FINDER: ставка 20% депо, цель 3× (R:R 1:3, 10x) ===")
found = 0
with httpx.Client() as client:
    for sym in SYMS:
        try:
            if analyze(client, sym):
                found += 1
        except Exception:
            continue
if not found:
    print("\nСетапов 3TF+3R сейчас нет. Рынок в коррекции — лучше подождать.")