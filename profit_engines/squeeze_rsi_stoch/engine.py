#!/usr/bin/env python3
"""
ENGINE P4-P9 — SQUEEZE + RSI + STOCHASTIC entry engines.

Механизм-first подход: Squeeze = state (сжатие волатильности), RSI = regime,
Stochastic = момент/переход, price = подтверждение, volume = participation.
НЕ "три индикатора → BUY". Каждый слой проверяется на INCREMENTAL NET.

Движки (независимые, без преждевременного объединения):
  P4 STOCHASTIC REVERSAL        — экстремум + разворот-подтверждение (oversold-fallacy тест)
  P5 STOCHASTIC CONTINUATION    — Stoch как momentum-reset detector, не reversal
  P6 SQUEEZE BREAKOUT           — compression → expansion → breakout (false vs continuation)
  P7 SQUEEZE → STOCH PULLBACK   — sequence: squeeze → breakout → pullback → stoch reset → re-entry
  P8 RSI REGIME / FAILURE SWING — RSI как состояние (failure swing / trend regime)
  P9 COMPOSITE                  — только после P4-P8, state machine, НЕ scoring

Ключевой benchmark: median MFE существующей системы ≈ 0.32R. Движок перспективен
только если median MFE материально выше И NET > 0 после 20bps.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, "/root/tradingos")
import numpy as np
import pandas as pd

from profit_engines.common import COST, load_panel, split_3

M15_BARS_PER_DAY = 96


# ─────────────────────────────────────────────────────────────────────
# ИНДИКАТОРЫ
# ─────────────────────────────────────────────────────────────────────
def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(period).mean()
    down = (-d.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def stochastic_series(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> tuple[pd.Series, pd.Series]:
    """%K и %D Stochastic."""
    ll = df['l'].rolling(k_period).min()
    hh = df['h'].rolling(k_period).max()
    k = (df['c'] - ll) / (hh - ll).replace(0, np.nan) * 100
    d = k.rolling(d_period).mean()
    return k, d


def squeeze_state(df: pd.DataFrame, method: str = 'bb', lookback: int = 20) -> pd.Series:
    """Сжатие волатильности. True = рынок в compression.
    method='bb': BB width percentile низкий; 'atr': ATR percentile низкий."""
    if method == 'bb':
        ma = df['c'].rolling(lookback).mean()
        sd = df['c'].rolling(lookback).std()
        bbw = 2 * sd / ma.replace(0, np.nan)
        pct = bbw.rolling(120).rank(pct=True)
    else:
        tr = pd.concat([
            (df['h'] - df['l']),
            (df['h'] - df['c'].shift(1)).abs(),
            (df['l'] - df['c'].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        pct = atr.rolling(120).rank(pct=True)
    return pct < 0.25  # нижние 25% = сжатие


def squeeze_release(df: pd.DataFrame, method: str = 'bb', lookback: int = 20) -> pd.Series:
    """Момент выхода из сжатия: текущий бар НЕ в squeeze, предыдущий был."""
    sq = squeeze_state(df, method, lookback)
    return (~sq) & sq.shift(1).fillna(False)


def higher_low_structure(df: pd.DataFrame, n: int = 3) -> pd.Series:
    """HL: минимум за n баров выше предыдущего минимума за n баров."""
    lo = df['l'].rolling(n).min()
    return lo > lo.shift(n)


def lower_high_structure(df: pd.DataFrame, n: int = 3) -> pd.Series:
    hi = df['h'].rolling(n).max()
    return hi < hi.shift(n)


# ─────────────────────────────────────────────────────────────────────
# ФАННЕЛ — общий реплей для всех движков
# ─────────────────────────────────────────────────────────────────────
def simulate_entry(df: pd.DataFrame, entry_idx: int, side: str, hold: int,
                   entry_type: str = 'open_next') -> dict | None:
    """Одна сделка. entry_type: open_next (MARKET на открытии следующего бара)."""
    entry_bar = entry_idx + 1 if entry_type == 'open_next' else entry_idx
    exit_bar = entry_bar + hold
    if exit_bar >= len(df):
        return None
    entry = float(df.iloc[entry_bar]['o'])
    path = df.iloc[entry_bar:exit_bar]
    if side == 'LONG':
        gross = (float(path.iloc[-1]['c']) - entry) / entry * 1e4
        mfe = (path['h'].max() - entry) / entry * 1e4
        mae = (path['l'].min() - entry) / entry * 1e4
    else:
        gross = (entry - float(path.iloc[-1]['c'])) / entry * 1e4
        mfe = (entry - path['l'].min()) / entry * 1e4
        mae = (entry - path['h'].max()) / entry * 1e4
    return {'gross_bps': gross, 'mfe_bps': mfe, 'mae_bps': mae,
            'entry_ts': float(df.iloc[entry_bar]['ts']), 'entry': entry}


def run_engine(df: pd.DataFrame, signal: pd.Series, side_map: pd.Series,
               hold: int, min_gap: int = 4) -> pd.DataFrame:
    """Прогон сигнала (bool) со сторонами (LONG/SHORT). Min gap между сделками.
    FIX: работа по позиции (pos index), чтобы переживать reset_index в split_3."""
    trades = []
    last_entry = -1e9
    signal = signal.reset_index(drop=True).fillna(False)
    side_map = side_map.reset_index(drop=True)
    mask = signal.values
    for i in range(len(mask)):
        if not mask[i]:
            continue
        if i - last_entry < min_gap:
            continue
        r = simulate_entry(df, i, side_map.iloc[i], hold)
        if r:
            r['entry_idx'] = i
            trades.append(r)
            last_entry = i
    return pd.DataFrame(trades)


def engine_stats(tdf: pd.DataFrame) -> dict:
    if tdf is None or len(tdf) == 0:
        return {'n': 0}
    g = tdf['gross_bps'].values
    wins = g[g > 0]; losses = g[g <= 0]
    net = g - COST.rt_taker_bps
    return {
        'n': len(tdf),
        'gross_med': float(np.median(g)), 'gross_mean': float(g.mean()),
        'net_med': float(np.median(net)), 'net_mean': float(net.mean()),
        'wr': float((g > 0).mean()),
        'pf': float(abs(wins.sum()) / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float('nan'),
        'mfe_med': float(tdf['mfe_bps'].median()), 'mae_med': float(tdf['mae_bps'].median()),
        'top1_pct': float(g.max() / g.sum()) if g.sum() != 0 else 0,
        'top5_pct': float(pd.Series(g).nlargest(5).sum() / g.sum()) if g.sum() != 0 else 0,
    }


def funnel(df: pd.DataFrame, signal: pd.Series, side: pd.Series, hold: int,
           name: str) -> dict:
    """TRAIN50/VAL20/OOS30 + фальсификация, для одного engine.
    FIX: нарезка по позициям исходного df (split_3 делает reset_index)."""
    n = len(df)
    i1, i2 = int(n * 0.5), int(n * 0.7)
    sig_arr = signal.reindex(df.index).fillna(False).values
    side_arr = side.reindex(df.index).values
    res = {'name': name, 'hold': hold}
    for seg, lo, hi in (('train', 0, i1), ('val', i1, i2), ('oos', i2, n)):
        sub = df.iloc[lo:hi].reset_index(drop=True)
        if len(sub) == 0:
            res[seg] = {'n': 0}
            continue
        sig_seg = pd.Series(sig_arr[lo:hi])
        side_seg = pd.Series(side_arr[lo:hi])
        tdf = run_engine(sub, sig_seg, side_seg, hold)
        res[seg] = engine_stats(tdf)
    return res


# ─────────────────────────────────────────────────────────────────────
# ДВИЖКИ (сигнал + сторона)
# ─────────────────────────────────────────────────────────────────────
def p4_stoch_reversal(df, k, d, rsi, stoch_params=(14, 3, 3)):
    """Stochastic reversal: экстремум + разворот + подтверждение структуры."""
    # LONG: K<20 → K>D, HL структура
    long_sig = (k < 20) & (k > d) & (k.shift(1) <= d.shift(1)) & higher_low_structure(df, 3)
    # SHORT: K>80 → K<D, LH структура
    short_sig = (k > 80) & (k < d) & (k.shift(1) >= d.shift(1)) & lower_high_structure(df, 3)
    sig = long_sig | short_sig
    side = pd.Series(np.where(long_sig, 'LONG', np.where(short_sig, 'SHORT', 'FLAT')), index=df.index)
    return sig, side


def p5_stoch_continuation(df, k, d, rsi):
    """Stochastic как momentum-reset: тренд + volume + Stoch выходит из зоны."""
    vol_expand = df['v'] > df['v'].rolling(60).median().shift(1) * 1.5
    # LONG: K>D, K выходит >50, RSI>50, uptrend (close>e20)
    e20 = df['c'].ewm(span=20).mean()
    long_sig = (k > d) & (k.shift(1) <= 50) & (k > 50) & (rsi > 50) & (df['c'] > e20) & vol_expand
    short_sig = (k < d) & (k.shift(1) >= 50) & (k < 50) & (rsi < 50) & (df['c'] < e20) & vol_expand
    sig = long_sig | short_sig
    side = pd.Series(np.where(long_sig, 'LONG', np.where(short_sig, 'SHORT', 'FLAT')), index=df.index)
    return sig, side


def p6_squeeze_breakout(df, k, d, rsi):
    """Squeeze release + breakout в сторону импульса + RSI подтверждение."""
    rel = squeeze_release(df, 'bb')
    # направление первого движения после release: ближайший бар с body
    long_sig = rel & (df['c'] > df['o']) & (rsi > 50)
    short_sig = rel & (df['c'] < df['o']) & (rsi < 50)
    sig = long_sig | short_sig
    side = pd.Series(np.where(long_sig, 'LONG', np.where(short_sig, 'SHORT', 'FLAT')), index=df.index)
    return sig, side


def p7_squeeze_stoch_pullback(df, k, d, rsi):
    """Squeeze → breakout → pullback → Stochastic reset → re-entry."""
    # breakout был в последние 20 баров после release
    rel = squeeze_release(df, 'bb')
    recent_break = rel.rolling(20).max().fillna(0).shift(1) > 0
    e20 = df['c'].ewm(span=20).mean()
    # LONG: недавний breakout, цена держит e20, Stoch пересёк D вверх из <50
    long_sig = recent_break & (df['c'] > e20) & (k > d) & (k.shift(1) < 50) & (rsi > 50)
    short_sig = recent_break & (df['c'] < e20) & (k < d) & (k.shift(1) > 50) & (rsi < 50)
    sig = long_sig | short_sig
    side = pd.Series(np.where(long_sig, 'LONG', np.where(short_sig, 'SHORT', 'FLAT')), index=df.index)
    return sig, side


def p8_rsi_regime(df, k, d, rsi):
    """RSI regime: failure swing (экстремум → retest → нет нового экстремума)."""
    # Failure swing LONG: RSI<30, потом RSI>30, потом RSI снова <35 (retest) но price > prev low
    rsi_os = (rsi < 30).rolling(3).max().shift(1).fillna(0) > 0   # был oversold
    # простая версия: RSI пересёк вверх из <30
    long_sig = (rsi > 30) & (rsi.shift(1) <= 30) & (df['c'] > df['c'].rolling(5).min().shift(1))
    short_sig = (rsi < 70) & (rsi.shift(1) >= 70) & (df['c'] < df['c'].rolling(5).max().shift(1))
    sig = long_sig | short_sig
    side = pd.Series(np.where(long_sig, 'LONG', np.where(short_sig, 'SHORT', 'FLAT')), index=df.index)
    return sig, side


def p9_composite(df, k, d, rsi):
    """Композит: squeeze-состояние + направление + Stoch-подтверждение + price."""
    sq = squeeze_state(df, 'bb')
    e20 = df['c'].ewm(span=20).mean()
    vol_ok = df['v'] > df['v'].rolling(60).median().shift(1)
    # market state: направление по e20, подтверждение Stoch+RSI+price+volume
    long_sig = sq & (df['c'] > e20) & (k > d) & (rsi > 55) & (df['c'] > df['o']) & vol_ok
    short_sig = sq & (df['c'] < e20) & (k < d) & (rsi < 45) & (df['c'] < df['o']) & vol_ok
    sig = long_sig | short_sig
    side = pd.Series(np.where(long_sig, 'LONG', np.where(short_sig, 'SHORT', 'FLAT')), index=df.index)
    return sig, side


# ─────────────────────────────────────────────────────────────────────
# ДИАГНОСТИКА: прогон всех движков по пулу символов
# ─────────────────────────────────────────────────────────────────────
ENGINES = {
    'P4_stoch_reversal': p4_stoch_reversal,
    'P5_stoch_cont': p5_stoch_continuation,
    'P6_squeeze_break': p6_squeeze_breakout,
    'P7_squeeze_stoch_pb': p7_squeeze_stoch_pullback,
    'P8_rsi_regime': p8_rsi_regime,
    'P9_composite': p9_composite,
}


def run_all(symbols: list[str], hold: int = 4, stoch_params=(14, 3, 3)) -> dict:
    import glob
    results = {}
    for sym in symbols:
        df = load_panel(sym, 'linear')
        if df is None or len(df) < 300:
            continue
        k, d = stochastic_series(df, stoch_params[0], stoch_params[1])
        rsi = rsi_series(df['c'], 14)
        for name, fn in ENGINES.items():
            sig, side = fn(df, k, d, rsi)
            tdf = run_engine(df, sig, side, hold)
            if tdf is None or len(tdf) == 0:
                continue
            tdf['symbol'] = sym
            results.setdefault(name, []).append(tdf)
    return results


if __name__ == '__main__':
    # Пусть из ~50 ликвидных линейных перпов
    import glob as _g, os as _os
    files = sorted(_g.glob('/tmp/lag_cache/linear_*_15.parquet'))
    syms = [_os.path.basename(f).replace('linear_', '').replace('_15.parquet', '')
            for f in files]
    syms = [s for s in syms if s != 'BTCUSDT'][:50]
    print(f"Символов: {len(syms)}")
    agg = {}
    for sym in syms:
        df = load_panel(sym, 'linear')
        if df is None or len(df) < 300:
            continue
        k, d = stochastic_series(df, 14, 3)
        rsi = rsi_series(df['c'], 14)
        for name, fn in ENGINES.items():
            sig, side = fn(df, k, d, rsi)
            tdf = run_engine(df, sig, side, hold=4)
            if tdf is None or len(tdf) == 0:
                continue
            tdf['symbol'] = sym
            agg.setdefault(name, []).append(tdf)
    print("\n=== РЕЗУЛЬТАТЫ (hold=4 баров = 1h, 20bps RT) ===")
    for name, list_df in sorted(agg.items()):
        all_t = pd.concat(list_df, ignore_index=True)
        st = engine_stats(all_t)
        if st['n'] == 0:
            print(f"{name}: n=0")
            continue
        print(f"{name}: n={st['n']:4d} gross_med={st['gross_med']:+7.1f}bp "
              f"net_med={st['net_med']:+7.1f}bp WR={st['wr']:.2f} "
              f"PF={st['pf'] if st['pf']==st['pf'] else 0:.2f} "
              f"MFE={st['mfe_med']:+.0f}bp MAE={st['mae_med']:+.0f}bp "
              f"top5={st['top5_pct']*100:.0f}%")
