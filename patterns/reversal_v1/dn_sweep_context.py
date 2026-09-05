#!/usr/bin/env python3
"""
dn_sweep_context.py — контекстный DN-sweep (2026-08-31, owner).

Ключевой вывод из скоринга реплей-кэша: ВСЕ сигналы старого детектора
приходились на бары, где перед пробоем цена ПАДАЛА (медиана −2.9% за 12
баров) — «падающий нож», WR ~45%. Это ровно анти-сетап из видео
«Ложный пробой в логике сильного рынка»: фейкоут работает ТОЛЬКО когда
он происходит ВНУТРИ сильного ап-импульса (HH-структура + рост), у
21-MA (зона баланса), с фитилём и закрытием обратно.

Здесь — ГЕНЕРАТОР, а не пост-фильтр: детектору подаются только бары,
у которых перед ними был ап-импульс. Так мы не теряем перед пробоем
истинные сигналы, а отсекаем падающий нож.

Фильтр контекста (context_gate) оставлен как обёртка для отдельных баров.
"""
from __future__ import annotations
import pandas as pd
from dn_sweep_detector import detect_dn_sweeps

# ── Параметры контекста (на divenable, как в videos) ───────────────────
CTX_IMPULSE_BARS = 12          # окно импульса (M15)
CTX_RISE_PCT = 1.2             # мин. рост за окно перед пробоем (%)
CTX_HH_MIN = 3                 # мин. HH-структура
CTX_MA21_MAX_DIST_PCT = 0.8    # вход не дальше 0.8% от 21-MA


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _hh_structure(highs: pd.Series, window: int, n_required: int) -> bool:
    if len(highs) < window:
        return False
    seg = highs.iloc[-window:].reset_index(drop=True)
    return int((seg.diff() > 0).sum()) >= n_required


def impulse_mask(df: pd.DataFrame) -> pd.Series:
    """True на барах, перед которыми был ап-импульс (+СТRISE_PCT за
    CTX_IMPULSE_BARS, HH-структура). Бары в начале (не хватает истории) — False."""
    n = len(df)
    mask = pd.Series(False, index=df.index)
    c = df["c"].astype(float)
    h = df["h"].astype(float)
    for i in range(CTX_IMPULSE_BARS, n):
        pre = c.iloc[i - CTX_IMPULSE_BARS:i]
        base = float(pre.iloc[0])
        if base <= 0:
            continue
        rise = (float(c.iloc[i - 1]) / base - 1) * 100
        hh = _hh_structure(h.iloc[:i], CTX_IMPULSE_BARS, CTX_HH_MIN)
        if rise >= CTX_RISE_PCT and hh:
            mask.iloc[i] = True
    return mask


def detect_dn_sweeps_context(
    df: pd.DataFrame,
    symbol: str,
    pivot_h: int = 16,
    sweep_pierce_bps: float = 20,
    min_close_back_above_bps: float = 5,
    hold_bars: int = 16,
    r_target: float = 2.0,
    atr_period: int = 14,
    sl_max_dist_pct: float = 0.08,
):
    """DN-sweep ТОЛЬКО в ап-контексте: подаём детектору подмножество баров,
    где перед пробоем был сильный импульс. Дополнительно проверяем близость
    входа к 21-MA (зона баланса) и HH-структуру."""
    if df is None or len(df) < pivot_h + atr_period + CTX_IMPULSE_BARS:
        return []
    df = df.sort_values("ts").reset_index(drop=True)
    imp = impulse_mask(df)
    filled = df.copy()
    filled["_in_ctx"] = imp
    # Передаём полный df (детектору нужен полный окно для ATR/prior-low),
    # но после генерации фильтруем сигналы по маске контекста И 21-MA.
    sigs = detect_dn_sweeps(df, symbol=symbol, pivot_h=pivot_h,
                            sweep_pierce_bps=sweep_pierce_bps,
                            min_close_back_above_bps=min_close_back_above_bps,
                            hold_bars=hold_bars, r_target=r_target,
                            atr_period=atr_period, sl_max_dist_pct=sl_max_dist_pct)
    closes = df["c"].astype(float)
    out = []
    for s in sigs:
        idx = (df["ts"] - s.ts).abs().idxmin()
        if not bool(imp.iloc[idx]):
            continue  # падающий нож — вне ап-контекста
        # Зона 51: вход у 21-MA
        if idx >= 21:
            ma21 = float(_ema(closes.iloc[:idx + 1], 21).iloc[-1])
            if ma21 > 0:
                dist = abs(s.entry_zone - ma21) / ma21 * 100
                if dist > CTX_MA21_MAX_DIST_PCT:
                    s.metadata["ctx_reject"] = f"far_from_ma21:{dist:.2f}%"
                    continue
        s.metadata["impulse_verified"] = True
        s.metadata["_ctx_idx"] = idx
        out.append(s)
    return out


def context_gate(candles: pd.DataFrame, bar_idx: int) -> tuple[bool, list[str]]:
    """(Быстрая обёртка для одиночного бара — диагностика.)"""
    if candles is None or len(candles) < CTX_IMPULSE_BARS + 2:
        return True, []
    sub = candles.iloc[:bar_idx + 1].reset_index(drop=True)
    mask = impulse_mask(sub)
    if not bool(mask.iloc[-1]):
        return False, ["нет ап-импульса перед пробоем"]
    closes = candles["c"].astype(float)
    if bar_idx >= 21:
        ma21 = float(_ema(closes.iloc[:bar_idx + 1], 21).iloc[-1])
        if ma21 > 0:
            dist = abs(float(closes.iloc[bar_idx]) - ma21) / ma21 * 100
            if dist > CTX_MA21_MAX_DIST_PCT:
                return False, [f"далеко от 21-MA ({dist:.2f}%)"]
    return True, []


if __name__ == "__main__":
    import glob, sys
    files = sorted(glob.glob('/root/tradingos/replay_cache/[A-Z0-9]*_M15.parquet'))[:25]
    all_sigs = 0
    ctx_sigs = 0
    for f in files:
        sym = f.split('/')[-1].replace('_M15.parquet', '')
        try:
            df = pd.read_parquet(f)
        except Exception:
            continue
        if 'ts' not in df or len(df) < 150:
            continue
        df = df.sort_values('ts').reset_index(drop=True)
        old = detect_dn_sweeps(df, symbol=sym)
        new = detect_dn_sweeps_context(df, symbol=sym)
        all_sigs += len(old)
        ctx_sigs += len(new)
        if new:
            print(f"  {sym}: {len(old)} → {len(new)} в контексте")
    print(f"\nИТОГО: старый {all_sigs}, контекстный {ctx_sigs} "
          f"(отсеяно {all_sigs-ctx_sigs}, {100*(1-ctx_sigs/max(all_sigs,1)):.0f}%)")