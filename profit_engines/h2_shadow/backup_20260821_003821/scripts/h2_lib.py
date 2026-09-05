#!/usr/bin/env python3
"""h2_lib.py — frozen H2 squeeze->expansion detector (ported 1:1 from the h2rep replication).

Semantics are copied verbatim from /tmp/profit_hunt2/h2rep/h2rep_squeeze.py (detect_events)
and the frozen config from /tmp/profit_hunt2/h2rep/h2rep_battery.py CFG. The accepted
tradable form is DIRECTION-AGNOSTIC (no trend filter) per h2rep_report.md verdict.

Anti-lookahead: all features are computed on bars <= close of bar i-1; the trigger uses
bar i high/low; forward returns measured from the boundary entry.
"""
import os, json
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

BAR_MIN = 15
BARS_4H = 16
BARS_24H = 96

# Frozen CFG (verbatim from h2rep_battery.py CFG — the authoritative frozen cell)
FROZEN_CFG = dict(
    tag='xdeep_t24h', q_atr=5, q_bbw=8, persist_k=5, persist_m=8,
    vol_gate=3.0, max_gate=3.0, range_tight=5.0, atr_win=480,
    rcomp_gate=0.75, cooldown=24 * 3600 * 1000, liq_thr=3000.0,
    range_bars=16,
)


def pctile_rank(x, window):
    """Trailing-window percentile rank of each point within its own window (verbatim port)."""
    n = len(x)
    if n < window + 2:
        return np.full(n, np.nan)
    w = sliding_window_view(x, window)
    r = (w <= w[:, -1:]).mean(axis=1)
    out = np.full(n, np.nan)
    out[window - 1:] = r
    return out


def atr_pct(o, h, l, c, n=14):
    """ATR14 / close, rolling-mean TR (verbatim port)."""
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(n).mean().values
    return atr / c


def detect_events(sym, df, cfg):
    """Detect H2 trigger events on an M15 frame [ts,o,h,l,c,v] with ts in ms.

    Returns a list of event dicts (same fields as the replication, plus 'side').
    Fires on EITHER breach direction (frozen direction-agnostic form).
    """
    o = df['o'].values.astype(float)
    h = df['h'].values.astype(float)
    l = df['l'].values.astype(float)
    c = df['c'].values.astype(float)
    v = df['v'].values.astype(float)
    ts = df['ts'].values.astype(np.int64)
    n = len(df)
    if n < 1400:
        return []

    a = atr_pct(o, h, l, c)
    with np.errstate(invalid='ignore'):
        apc = pctile_rank(a, cfg['atr_win'])
        sma20 = pd.Series(c).rolling(20).mean().values
        sd20 = pd.Series(c).rolling(20).std().values
        bbw = np.where(sd20 > 0, (sma20 + 2 * sd20 - (sma20 - 2 * sd20)) / sma20, np.nan)
        bpc = pctile_rank(bbw, cfg['atr_win'])
        vmed = pd.Series(v).rolling(BARS_24H).median().values
        vratio = np.where(vmed > 0, v / vmed, 1.0)
        r4 = (pd.Series(h).rolling(BARS_4H).max() - pd.Series(l).rolling(BARS_4H).min()).values / c
        r24 = (pd.Series(h).rolling(BARS_24H).max() - pd.Series(l).rolling(BARS_24H).min()).values / c
        rcomp = np.where(r24 > 0, r4 / r24, 1.0)

    qa = cfg['q_atr'] / 100.0
    qb = cfg['q_bbw'] / 100.0
    RB = cfg.get('range_bars', BARS_4H)
    sq = (apc <= qa) & (bpc <= qb) & (rcomp <= cfg['rcomp_gate']) & (r24 > 0)
    sq = sq & ~np.isnan(apc) & ~np.isnan(bpc)

    k, m = cfg['persist_k'], cfg['persist_m']
    sq_int = sq.astype(int)
    sq_sum = pd.Series(sq_int).rolling(m).sum().values
    sq_ok = sq_sum >= k
    sq_ok = sq_ok & sq

    rh = pd.Series(h).shift(1).rolling(RB).max().values
    rl = pd.Series(l).shift(1).rolling(RB).min().values
    rw = np.where(a > 0, (rh - rl) / np.maximum(a * 4, 1e-9), np.nan)

    events = []
    last_ev = -10 ** 18
    cooldown = cfg['cooldown']
    for i in range(n):
        if np.isnan(a[i]) or i < cfg['atr_win'] + 20:
            continue
        if not (sq_ok[i - 1] if i - 1 >= 0 else False):
            continue
        if ts[i] - last_ev < cooldown:
            continue
        bh, bl = h[i], l[i]
        up = bh > rh[i] and bl >= rl[i]
        dn = bl < rl[i] and bh <= rh[i]
        if not (up or dn):
            continue
        if vratio[i] < cfg['vol_gate']:
            continue
        if a[i] > 0 and (bh - bl) > cfg['max_gate'] * a[i] * 4:
            continue
        if not np.isfinite(rw[i]) or rw[i] > cfg['range_tight']:
            continue
        entry = rh[i] if up else rl[i]
        sma4 = pd.Series(c).rolling(4).mean().values
        sma16 = pd.Series(c).rolling(16).mean().values
        sma96 = pd.Series(c).rolling(96).mean().values
        ev = {
            'sym': sym, 'event_ts': int(ts[i]), 'bar_i': int(i),
            'dir': 1 if up else -1,
            'side': 'BUY' if up else 'SELL',
            'entry': float(entry),
            'range_hi': float(rh[i]), 'range_lo': float(rl[i]),
            'atr_pct': float(a[i]), 'atr_pctl': float(apc[i]),
            'bbw_pctl': float(bpc[i]), 'vratio': float(vratio[i]),
            'range_w_atr': float(rw[i]),
            'rcomp': float(rcomp[i]),
            'c_trig': float(c[i]), 'o_trig': float(o[i]),
            'h_trig': float(bh), 'l_trig': float(bl),
            'v_trig': float(v[i]), 'vmed24h': float(vmed[i]),
            'trend1h': float(c[i] - sma4[i]) if not np.isnan(sma4[i]) else np.nan,
            'trend4h': float(c[i] - sma16[i]) if not np.isnan(sma16[i]) else np.nan,
            'trend24h': float(c[i] - sma96[i]) if not np.isnan(sma96[i]) else np.nan,
        }
        events.append(ev)
        last_ev = ts[i]
    return events


def range_bps(ev, px):
    """4h range width in bps relative to a price (the stop distance = range width)."""
    rp = (ev['range_hi'] - ev['range_lo']) / px * 1e4
    return float(rp)


def py(o):
    """Recursive sanitizer: numpy scalars/arrays -> python native (json.dump safe)."""
    if isinstance(o, dict):
        return {k: py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [py(x) for x in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return [py(x) for x in o.tolist()]
    if isinstance(o, float) and (o != o):
        return None
    if isinstance(o, float) and o in (float('inf'), float('-inf')):
        return None
    return o


def bps(a, b):
    """(a/b - 1)*1e4 or None on invalid."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if not a or not b or b <= 0 or a <= 0:
        return None
    return (a / b - 1) * 1e4


def jsonl_append(path, obj):
    with open(path, 'a') as f:
        f.write(json.dumps(py(obj), ensure_ascii=False, default=str) + '\n')


def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
