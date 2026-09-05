#!/usr/bin/env python3
"""
ENGINE P1 — BTC→ALT LAG (LONG-ONLY).

Механизм: BTC делает значимое движение → часть ALT-активов реагирует с задержкой
(underreaction) → в течение ~1h laggers догоняют BTC-имплицированное движение.

ВАЖНО из валидации (btc_alt_lag_final.md, 2026-08-10):
- Полная стратегия (LONG+SHORT): REJECT — OOS gross −911.8bps (PF 0.905),
  short-сторона OOS −194.2bps (PF 0.507), одиночный −5404.6bps blowup.
- LONG-ONLY sub-variant: OOS gross +2496bps (PF 1.914), но VAL отрицательный → PROMISING-EXPLORATORY.
- Механизм статистически реален: shuffle p=0.0000, premium +39bps над random-ALT.
- Единственный корректный следующий тест: LONG-ONLY preregistered на >=90д свежих данных.
- СТОП-ЛОССЫ УБИВАЮТ edge (vol-stops: −28.9 vs +63.0 stopless) — engine структурно
  stopless-1h-hold. РИСК ЭТОГО engine = полный ход до time-exit.

Данный модуль — research/shadow. НЕ отправляет реальные ордера.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, "/root/tradingos")
import numpy as np
import pandas as pd

from profit_engines.common import COST, load_panel, split_3, atr_series, shuffle_pvalue

# ── Конфиг (frozen после TRAIN) ──
BTC_IMPULSE_THRESHOLDS = [0.005, 0.0075, 0.01]   # abs BTC 15m return
LAG_TOP_N = 5
MIN_LAG_BPS = 50.0
HOLDING_BARS = [1, 2, 4, 8, 16]                  # 15m..4h
BETA_WINDOW = 30 * 96                            # 30d M15 (для rolling beta)
MIN_BARS = 60


def rolling_beta(alt: pd.Series, btc: pd.Series, window: int) -> pd.Series:
    """Rolling beta: cov(alt,btc)/var(btc) за window баров, shift(1) — без look-ahead."""
    df = pd.DataFrame({'a': alt, 'b': btc})
    cov = df['a'].rolling(window, min_periods=window // 2).cov(df['b'])
    var = df['b'].rolling(window, min_periods=window // 2).var()
    beta = cov / var.replace(0, np.nan)
    return beta.shift(1)


def detect_btc_events(btc: pd.DataFrame, threshold: float) -> list[dict]:
    """BTC 15m импульсы: |ret| > threshold, кластер-дедуп (min gap 2 бара)."""
    ret = btc['c'].pct_change()
    mask = ret.abs() > threshold
    events, last = [], None
    for i in btc.index[mask]:
        if i == 0:
            continue
        if last is not None and i - last <= 2:
            continue
        events.append({'idx': i, 'ts': btc.iloc[i]['ts'],
                       'ret': float(ret.iloc[i]), 'close': float(btc.iloc[i]['c'])})
        last = i
    return events


def compute_lag(alt: pd.DataFrame, btc: pd.DataFrame, event_idx: int) -> float | None:
    """Lag = beta × BTC_ret − ALT_co_move (в bps). Положительный = ALT отстал."""
    if event_idx < BETA_WINDOW or event_idx >= len(alt):
        return None
    a_ret = alt['c'].pct_change()
    b_ret = btc['c'].pct_change()
    beta = rolling_beta(a_ret, b_ret, BETA_WINDOW).iloc[event_idx]
    if not np.isfinite(beta):
        return None
    btc_ret = float(b_ret.iloc[event_idx])
    alt_ret = float(a_ret.iloc[event_idx])
    return (beta * btc_ret - alt_ret) * 1e4  # bps


def run_long_only(impulse_thr: float, hold_bars: int, top_n: int = LAG_TOP_N,
                  min_lag_bps: float = MIN_LAG_BPS) -> dict:
    """LONG-ONLY: только BTC UP события, топ-N laggers, 1h hold, market entry.

    Возвращает результат по TRAIN/VAL/OOS (net bps при 20bps RT).
    """
    btc = load_panel('BTCUSDT', 'linear')
    if btc is None:
        return {'error': 'no BTC data'}
    events = [e for e in detect_btc_events(btc, impulse_thr) if e['ret'] > 0]

    # Пул ALT (линейные перпы, минимум ликвидности по объёму)
    import glob, os
    alt_files = sorted(glob.glob('/tmp/lag_cache/linear_*_15.parquet'))
    alts = []
    for f in alt_files:
        sym = os.path.basename(f).replace('linear_', '').replace('_15.parquet', '')
        if sym in ('BTCUSDT',):
            continue
        df = load_panel(sym, 'linear')
        if df is None or len(df) < MIN_BARS:
            continue
        med_vol = df['v'].rolling(60).median().median()
        if med_vol and med_vol > 5000:   # ликвидность: средний бар > 5000 контрактов
            alts.append((sym, df))
    if not alts:
        return {'error': 'no alt data'}

    trades = []
    for ev in events:
        ei = ev['idx']
        # lag по каждому ALT
        lags = []
        for sym, df in alts:
            if ei >= len(df):
                continue
            lag = compute_lag(df, btc, ei)
            if lag is None:
                continue
            lags.append((sym, df, lag))
        if len(lags) < 3:
            continue
        # топ-N laggers (по убыванию lag), только положительный lag
        lags.sort(key=lambda x: -x[2])
        selected = [x for x in lags[:top_n] if x[2] >= min_lag_bps]
        for sym, df, lag in selected:
            entry_bar = ei + 1
            exit_bar = entry_bar + hold_bars
            if exit_bar >= len(df):
                continue
            entry = float(df.iloc[entry_bar]['o'])
            exit_px = float(df.iloc[exit_bar]['c'])
            gross = (exit_px - entry) / entry * 1e4  # bps
            # MFE/MAE на пути
            path = df.iloc[entry_bar:exit_bar]
            mfe = (path['h'].max() - entry) / entry * 1e4
            mae = (path['l'].min() - entry) / entry * 1e4
            trades.append({
                'ts': ev['ts'], 'symbol': sym, 'lag_bps': lag,
                'gross_bps': gross, 'mfe_bps': mfe, 'mae_bps': mae,
                'btc_ret': ev['ret'],
            })

    if not trades:
        return {'error': 'no trades', 'events': len(events)}

    tdf = pd.DataFrame(trades)
    # Сплит по времени события (TRAIN/VAL/OOS)
    tdf = tdf.sort_values('ts').reset_index(drop=True)
    n = len(tdf)
    i1, i2 = int(n * 0.5), int(n * 0.7)
    tr, va, oo = tdf.iloc[:i1], tdf.iloc[i1:i2], tdf.iloc[i2:]

    def stats(d):
        if len(d) == 0:
            return {'n': 0}
        gross = d['gross_bps'].values
        wins = gross[gross > 0]
        losses = gross[gross <= 0]
        net = gross - COST.rt_taker_bps
        return {
            'n': len(d), 'gross_mean': float(gross.mean()),
            'gross_med': float(np.median(gross)),
            'net_mean': float(net.mean()), 'net_med': float(np.median(net)),
            'winrate': float((gross > 0).mean()),
            'pf': float(abs(wins.sum()) / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float('nan'),
            'mfe_med': float(d['mfe_bps'].median()), 'mae_med': float(d['mae_bps'].median()),
            'top1_pct': float(d['gross_bps'].max() / gross.sum()) if gross.sum() != 0 else 0,
            'top5_pct': float(d.nlargest(5, 'gross_bps')['gross_bps'].sum() / gross.sum()) if gross.sum() != 0 else 0,
        }

    return {
        'impulse_thr': impulse_thr, 'hold_bars': hold_bars, 'top_n': top_n,
        'events': len(events), 'trades': len(tdf),
        'train': stats(tr), 'val': stats(va), 'oos': stats(oo),
        'symbols': tdf['symbol'].nunique(),
    }


def falsify_long_only(impulse_thr: float, hold_bars: int) -> dict:
    """Ключевые фальсификации для LONG-ONLY."""
    base = run_long_only(impulse_thr, hold_bars)
    if 'trades' not in base or base['trades'] == 0:
        return {'error': 'no base trades'}
    oo_n = base['oos']['n']
    # remove top-5 OOS winners
    btc = load_panel('BTCUSDT', 'linear')
    events = [e for e in detect_btc_events(btc, impulse_thr) if e['ret'] > 0]
    # упрощённо: пересчитываем и убираем топ-5 по gross в OOS через повторный прогон
    return {
        'base_oos_net_mean': base['oos']['net_mean'],
        'note': 'Полная фальсификация (shuffle, remove-top, 2x cost, delayed entry, regime) '
                'требует >=90д свежих данных — текущее 60д окно single-regime, повторный сплит '
                'недопустим (см. btc_alt_lag_final.md). Этот engine НЕ BUILD-ready.',
    }


if __name__ == '__main__':
    print("=== P1 BTC→ALT LAG LONG-ONLY (диагностика на 60д) ===")
    for thr in BTC_IMPULSE_THRESHOLDS:
        for hold in [4, 8]:
            r = run_long_only(thr, hold)
            if 'error' in r:
                print(f"thr={thr} hold={hold}: {r['error']}")
                continue
            print(f"\nthr={thr:.3f} hold={hold} (events={r['events']}, trades={r['trades']}, syms={r['symbols']})")
            for k in ('train', 'val', 'oos'):
                s = r[k]
                if s['n']:
                    print(f"  {k:5s} n={s['n']:3d} gross_med={s['gross_med']:+.1f}bp "
                          f"net_med={s['net_med']:+.1f}bp WR={s['winrate']:.2f} "
                          f"PF={s['pf'] if s['pf']==s['pf'] else 0:.2f} MFE={s['mfe_med']:+.0f}bp MAE={s['mae_med']:+.0f}bp")
