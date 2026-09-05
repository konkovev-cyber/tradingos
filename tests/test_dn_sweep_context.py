"""Unit tests for DN-sweep context gate (2026-08-31, video-driven).

Ключевой инсайт из реплей-скоринга: старый DN-sweep ловил ТОЛЬКО падающие
ножи (38/38 свепов, медиана роста −2.9% перед пробоем, WR ~45%). Видео
«Ложный пробой в логике сильного рынка»: фейкоут работает только ВНУТРИ
сильного ап-импульса у 21-MA. Тесты проверяют, что гейт пропускает
импульсные свепы и блокирует падающие ножи.
"""
import sys

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/tradingos/patterns/reversal_v1")

import numpy as np
import pandas as pd
import pytest

from dn_sweep_context import impulse_mask, context_gate
from dn_sweep_detector import detect_dn_sweeps


def _make_df(up_before: bool) -> pd.DataFrame:
    """Синтетика: ап-импульс (или падение) 12 баров + свеп-бар на 13."""
    n = 120
    interval = 15 * 60_000
    start = 1_800_000_000_000
    ts = [start + i * interval for i in range(n)]
    # Детерминированный чистый ряд: +5% за первые 13 баров (линейно к 105),
    # потом плато на 105. Свеп-бар на 13 — сразу после достижения пика.
    up = np.linspace(100, 105, 14)          # бары 0..13
    flat = np.full(n - 14, 105.0)
    base = np.concatenate([up, flat]) if up_before else np.concatenate([200 - up, np.full(n - 14, 195.0)])
    o = base.copy()
    c = base.copy() + 0.02
    h = base + 0.05
    l = base - 0.05
    # свеп-бар на индексе 13: даун-вик ниже минимума + закрытие обратно
    sweep_i = 13
    l[sweep_i] = float(np.min(l[:sweep_i])) - 0.3
    c[sweep_i] = o[sweep_i] + 0.05
    h[sweep_i] = c[sweep_i] + 0.01
    return pd.DataFrame({"ts": ts, "o": o, "h": h, "l": l, "c": c})


def test_impulse_mask_true_on_up():
    df = _make_df(up_before=True)
    mask = impulse_mask(df)
    assert bool(mask.iloc[13]) is True  # бар после импульсной рампы — в контексте


def test_impulse_mask_false_on_down():
    df = _make_df(up_before=False)
    mask = impulse_mask(df)
    # может не быть ап-импульса — весь ряд падает
    assert bool(mask.iloc[13]) is False


def test_context_gate_blocks_down_knife():
    df = _make_df(up_before=False)
    ok, reasons = context_gate(df, 13)
    assert ok is False
    assert any("импульс" in r for r in reasons)


def test_context_detector_requires_impulse():
    """Контекстный генератор блокирует сигналы во время падения."""
    from dn_sweep_context import detect_dn_sweeps_context
    df_down = _make_df(up_before=False)
    ctx_down = detect_dn_sweeps_context(df_down, symbol="X")
    assert len(ctx_down) == 0  # падающий нож не проходит