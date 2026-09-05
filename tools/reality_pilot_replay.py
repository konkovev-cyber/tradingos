#!/usr/bin/env python3
"""
Reality Pilot Replay v2 — хронологическая симуляция полного контура.
Позиции закрываются по SL/TP по мере движения времени (слоты освобождаются),
как в реальном production. Это даёт честную картину max_positions.
"""
import json
from datetime import datetime

# ── Загрузка ──
signals = []
with open('/root/tradingos/memory/signal_log.jsonl') as f:
    for line in f:
        if not line.strip():
            continue
        d = json.loads(line)
        if d['direction'] != 'NONE':
            ts = int(datetime.fromisoformat(d['timestamp'].replace('Z', '+00:00')).timestamp() * 1000)
            signals.append({
                'symbol': d['symbol'], 'direction': d['direction'],
                'close': d['close'], 'atr': d['atr'], 'ts': ts,
                'prob': d['final_probability'], 'score': d['score'],
                'quality': d['quality'], 'adx': d.get('adx', 0),
            })

klines = json.load(open('/tmp/klines_1h.json'))
price_idx = {}
for sym, bars in klines.items():
    price_idx[sym] = sorted(bars)

# ── Параметры (production) ──
CAND_PROB = 0.55
CAND_QUALITY = ("MEDIUM", "GOOD", "EXCELLENT")
CAND_SCORE = 55
ADX_MIN = 20
COOLDOWN_SEC = 3600
MAX_POSITIONS = 4
MAX_HOLD_MS = 48 * 3600 * 1000

# Кандидаты, прошедшие порог
cand_pool = [s for s in signals
             if s['prob'] >= CAND_PROB
             and s['quality'] in CAND_QUALITY
             and s['score'] >= CAND_SCORE]
cand_pool.sort(key=lambda s: s['ts'])

# ── Хронологическая симуляция ──
# Открытые позиции: symbol -> {entry, sl, tp, direction, open_ts}
open_pos = {}
last_proposal = {}
stats = {'candidates': len(cand_pool), 'proposals': 0, 'trades': 0,
         'wins': 0, 'losses': 0, 'not_closed': 0,
         'skipped_cooldown': 0, 'skipped_adx': 0, 'skipped_maxpos': 0}
trades_log = []

def close_expired(now_ms):
    """Закрыть позиции, где SL/TP сработали до now_ms. Освобождает слоты."""
    to_close = []
    for sym, pos in list(open_pos.items()):
        entry = pos['entry']; sl = pos['sl']; tp = pos['tp']; direction = pos['direction']
        result = None
        for t, c in price_idx.get(sym, []):
            if t < pos['open_ts']:
                continue
            if t > now_ms:
                break
            if direction == 'BUY':
                if c <= sl: result = 'SL'; break
                if c >= tp: result = 'TP'; break
            else:
                if c >= sl: result = 'SL'; break
                if c <= tp: result = 'TP'; break
        if result == 'TP':
            stats['wins'] += 1; stats['trades'] += 1
            trades_log.append({'sym': sym, 'dir': direction, 'result': 'TP'})
            to_close.append(sym)
        elif result == 'SL':
            stats['losses'] += 1; stats['trades'] += 1
            trades_log.append({'sym': sym, 'dir': direction, 'result': 'SL'})
            to_close.append(sym)
        elif now_ms - pos['open_ts'] > MAX_HOLD_MS:
            stats['not_closed'] += 1; stats['trades'] += 1
            trades_log.append({'sym': sym, 'dir': direction, 'result': 'TIMEOUT'})
            to_close.append(sym)
    for sym in to_close:
        del open_pos[sym]

# Обрабатываем кандидатов хронологически
for cand in cand_pool:
    now_ms = cand['ts']
    # Сначала закрываем позиции, сработавшие до этого момента
    close_expired(now_ms)

    # Cooldown
    last = last_proposal.get(cand['symbol'], 0)
    if now_ms / 1000.0 - last < COOLDOWN_SEC:
        stats['skipped_cooldown'] += 1
        continue

    # ADX
    if cand['adx'] < ADX_MIN:
        stats['skipped_adx'] += 1
        continue

    # max_positions
    if len(open_pos) >= MAX_POSITIONS:
        stats['skipped_maxpos'] += 1
        continue

    # SL/TP
    entry = cand['close']; atr = cand['atr'] or 0
    if atr <= 0:
        continue
    if cand['direction'] == 'BUY':
        sl = entry - atr * 2; tp = entry + atr * 4
    else:
        sl = entry + atr * 2; tp = entry - atr * 4

    stats['proposals'] += 1
    last_proposal[cand['symbol']] = now_ms / 1000.0
    open_pos[cand['symbol']] = {
        'entry': entry, 'sl': sl, 'tp': tp,
        'direction': cand['direction'], 'open_ts': now_ms,
    }

# Закрыть оставшиеся в конце
close_expired(float('inf'))

# ── Отчёт ──
print("=" * 50)
print("REALITY PILOT REPLAY v2 (хронологический)")
print("=" * 50)
print(f"Кандидатов: {stats['candidates']}")
print(f"Proposals (прошли Top-1+фильтры): {stats['proposals']}")
print(f"Пропущено: cooldown={stats['skipped_cooldown']} adx={stats['skipped_adx']} maxpos={stats['skipped_maxpos']}")
print(f"Сделок: {stats['trades']}")
print(f"  TP: {stats['wins']}, SL: {stats['losses']}, timeout: {stats['not_closed']}")
closed = stats['wins'] + stats['losses']
if closed:
    wr = stats['wins'] / closed * 100
    exp = (stats['wins'] * 2 - stats['losses']) / closed
    pf = (stats['wins'] * 2) / stats['losses'] if stats['losses'] else float('inf')
    print(f"Winrate: {wr:.1f}%")
    print(f"Expectancy: {exp:+.3f}R")
    print(f"Profit Factor (RR=2): {pf:.2f}")
print("=" * 50)
