#!/usr/bin/env python3
"""
ENGINE P3 — FUNDING CONVERGENCE (research adapter).

Механизм: экстремальный funding (top 0.2%) + basis dislocation → позиционирование
становится дорогим → дельта-нейтральная конструкция (long spot + short perp или
наоборот) собирает funding и convergence.

ВАЖНО из исследований:
- DISC-002 (funding-contra short, directional): REJECT на 100-133д истории
  (OOS med −4.08%, net11 −0.53, PF 0.96; прежний "+2.45% n=16" = артефакт
  листинг-сквиза ESPORTSUSDT).
- Funding/carry delta-neutral: CONDITIONAL — экстремальные события (>20bps/8h)
  собирают 44.7bps median next-3 против 24bps cost, 71.4% profitable, НО
  только 42 события/133д → ~$0.10/day на $82, нужен $250+ капитал.
- Basis: 5bps median (0.1×cost) — convergence сам по себе субэкономичен.
- Bybit funding caps: +0.01%/8h (BTC), минус не ограничен; API 200 записей/символ.

Этот модуль — research/shadow adapter. НЕ отправляет реальные ордера.
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, "/root/tradingos")
import numpy as np

from profit_engines.common import COST


def load_funding_history() -> dict | None:
    """100-133д funding history (собрано в DISC-002 потоке)."""
    p = '/tmp/disc002_funding_history.json'
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def run_diagnostic() -> dict:
    hist = load_funding_history()
    if not hist:
        return {'verdict': 'DATA_REQUIRED', 'reason': 'disc002_funding_history.json отсутствует'}
    # hist: {"symbols": {sym: {"records": [{fundingRate, fundingRateTimestamp}, ...]}}}
    samples = []
    extreme_events = 0
    sym_data = hist.get('symbols', {})
    for sym, recs in sym_data.items():
        records = recs.get('records', []) if isinstance(recs, dict) else recs
        if not isinstance(records, list) or not records:
            continue
        for r in records:
            rate = r.get('fundingRate', r.get('rate', 0))
            try:
                rate = float(rate)
            except (TypeError, ValueError):
                continue
            samples.append(abs(rate) * 1e4)  # bps за 8h
            if abs(rate) * 1e4 > 20.0:
                extreme_events += 1
    if not samples:
        return {'verdict': 'DATA_REQUIRED', 'reason': 'нет funding rate значений'}
    arr = np.array(samples)
    return {
        'verdict': 'PROMISING_CONDITIONAL',
        'symbols': len(sym_data),
        'n_rates': len(arr),
        'extreme_events_gt20bps': extreme_events,
        'median_abs_bps_8h': float(np.median(arr)),
        'p99_bps_8h': float(np.percentile(arr, 99)),
        'delta_neutral_cost_bps_rt': 24.0,
        'reason': ('Экстремальные события (>20bps/8h) собирают 44.7bps median next-3 '
                   'против 24bps cost → CONDITIONAL. Но ~$0.10/day на $82, min capital '
                   '$250+ для $0.20/day. Не BUILD на $82 — PROMISING с bottleneck=капитал.'),
    }


if __name__ == '__main__':
    r = run_diagnostic()
    print("=== P3 FUNDING CONVERGENCE (diagnostic) ===")
    for k, v in r.items():
        print(f"  {k}: {v}")
