#!/usr/bin/env python3
"""
ENGINE P2 — LIQUIDATION REVERSION (research adapter).

Механизм: принудительный flow (ликвидации/каскад) → кратковременная дислокация
цены → после исчерпания потока цена частично возвращается.

ВАЖНО из исследований (liquidation_reversion_report.md):
- Полный механизм REJECT: gross уже отрицателен ДО fees; shuffle показывает, что
  "liquidation label" не добавляет ценности над random-large-move; OI-collapse gate
  непроверяем (2 события); top-5-winner removal убивает; short-сторона −228bps.
- Real liquidation feed на Bybit ОТСУТСТВУЕТ (REST 404, WS никогда не накапливал).
  ВСЁ детектирование — proxy (taker flow + volume + displacement).
- Единственный transferable кусок: MAE-reduction механизм может пригодиться
  для LIMIT-входов с собственным adverse-бюджетом.

Этот модуль — research/shadow adapter. НЕ отправляет реальные ордера.
Проверяет: на 1m данных существует ли момент, где forced-flow displacement
существенно > 20bps cost (ранее было ~9bps на M15 — вопрос: даёт ли 1m больше).
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, "/root/tradingos")
import numpy as np
import pandas as pd

from profit_engines.common import COST


def load_btc_trades_1m() -> pd.DataFrame | None:
    """Минутные бары BTC из сырых трейдов (28д, агрессивная сторона).
    Проверяет наличие файла; если нет — возвращает None (DATA REQUIRED)."""
    p = '/tmp/lq_reversion/btc_minute_bars.parquet'
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df = df.sort_values('ts_min').reset_index(drop=True)
    df['ts'] = df['ts_min'] / 1000.0
    df['close'] = df['last'].astype(float)
    df['low'] = df['minp'].astype(float)
    df['high'] = df['maxp'].astype(float)
    df['vol'] = df['qty'].astype(float)
    df['taker_imb'] = ((df['buy_vol'] - df['sell_vol']) / (df['buy_vol'] + df['sell_vol'] + 1e-9)).astype(float)
    return df


def detect_cascades_1m(df: pd.DataFrame, z_imb_thr: float = 1.5, z_vol_thr: float = 2.5) -> list[dict]:
    """Proxy-каскады на 1m: экстремальный taker imbalance + объём + displacement."""
    def mad_z(x, med, mad):
        return (x - med) / (1.4826 * np.maximum(mad, 1e-9))
    med_i = df['taker_imb'].rolling(60, min_periods=20).median()
    mad_i = (df['taker_imb'] - med_i).abs().rolling(60, min_periods=20).median()
    med_v = df['vol'].rolling(60, min_periods=20).median()
    mad_v = (df['vol'] - med_v).abs().rolling(60, min_periods=20).median()
    z_i = mad_z(df['taker_imb'], med_i, mad_i)
    z_v = mad_z(df['vol'], med_v, mad_v)
    ret = df['close'].pct_change().fillna(0.0)

    events = []
    for direction in ('long', 'short'):  # long=продажи (Sell-флуд), short=покупки
        if direction == 'long':
            qual = (z_i < -z_imb_thr) & (z_v > z_vol_thr) & (ret < 0)
            ext = df['low']
        else:
            qual = (z_i > z_imb_thr) & (z_v > z_vol_thr) & (ret > 0)
            ext = df['high']
        idx = df.index[qual].tolist()
        runs, cur, last = [], [], None
        for i in idx:
            if last is None or i - last <= 2:
                cur.append(i)
            else:
                if cur:
                    runs.append(cur)
                cur = [i]
            last = i
        if cur:
            runs.append(cur)
        for run in runs:
            s, e = run[0], run[-1]
            if s == 0:
                continue
            pre_close = df.iloc[s - 1]['close']
            peak = ext.iloc[run].min() if direction == 'long' else ext.iloc[run].max()
            disp = (pre_close - peak) / pre_close if direction == 'long' else (peak - pre_close) / pre_close
            if disp <= 0:
                continue
            events.append({'direction': direction, 'start': s, 'end': e,
                           'disp_bps': disp * 1e4, 'len_min': len(run)})
    return events


def run_diagnostic() -> dict:
    df = load_btc_trades_1m()
    if df is None:
        return {'verdict': 'DATA_REQUIRED', 'reason': 'btc_minute_bars.parquet отсутствует'}
    evs = detect_cascades_1m(df)
    if not evs:
        return {'verdict': 'REJECT', 'reason': '0 каскадов детектировано (пороги недостижимы)'}
    disps = np.array([e['disp_bps'] for e in evs])
    return {
        'verdict': 'REJECT',
        'events': len(evs),
        'disp_median_bps': float(np.median(disps)),
        'disp_p90_bps': float(np.percentile(disps, 90)),
        'disp_max_bps': float(disps.max()),
        'cost_bps': COST.rt_taker_bps,
        'reason': ('Ключевой вопрос: displacement > 20bps cost? '
                   'Если median ~9-45bps как в прошлом — REJECT'),
    }


if __name__ == '__main__':
    r = run_diagnostic()
    print("=== P2 LIQUIDATION REVERSION (1m diagnostic) ===")
    for k, v in r.items():
        print(f"  {k}: {v}")
