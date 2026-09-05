#!/usr/bin/env python3
"""
TradingOS Profit Engines — common infrastructure.

ВЕРДИКТЫ ИССЛЕДОВАНИЯ (2026-08-10), на которых строится framework:
- 24 гипотезы отклонены, 0 подтверждённых engines при $82.
- Реальная cost model (верифицирована execution-потоком из 119 сделок):
    taker RT = 20 bps (10/side), maker RT = 4.2 bps (BTC), per-symbol slippage.
- me_lib.py COST_RT_BPS=60 — УСТАРЕВШИЙ АРТЕФАКТ (двойной счёт fee + tail-mean slippage).
  НЕ использовать для новых engines.

Каждый engine проходит PROFIT FUNNEL:
  HYPOTHESIS → DATA → GROSS → REAL COST → OOS → FALSIFICATION
  → EXECUTION REPLAY → $82 ECONOMICS → SHADOW → MICRO-LIVE → REAL NET PNL
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────
# COST MODEL — верифицированные значения (не артефакт 60bps)
# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CostModel:
    """Реальные издержки Bybit linear (VIP0), измеренные на 119 сделках."""
    taker_bps_per_side: float = 10.0      # 0.0550% → 5.5bps? нет: эмпирически 10bps/side
    maker_bps_per_side: float = 2.0       # 0.0200%
    slip_bps_median: float = 0.0
    slip_bps_p75: float = 20.0
    slip_bps_p90: float = 22.0
    slip_bps_p95: float = 24.0
    min_notional_usd: float = 5.0
    min_btc_notional: float = 63.0        # 0.001 BTC

    @property
    def rt_taker_bps(self) -> float:
        return 2 * self.taker_bps_per_side + self.slip_bps_median  # 20 bps

    @property
    def rt_maker_bps(self) -> float:
        return 2 * self.maker_bps_per_side + self.slip_bps_median  # 4 bps

    def expected_cost_bps(self, maker_fill_prob: float = 0.0) -> float:
        """Взвешенная стоимость: maker c вероятностью p, иначе taker."""
        return (maker_fill_prob * self.rt_maker_bps
                + (1 - maker_fill_prob) * self.rt_taker_bps)

    def cost_bps_at(self, level: float) -> float:
        """Стоимость по сценарию: 4.2/10/20/30/40 bps."""
        return float(level)


COST = CostModel()

# Хронологические сплиты (UTC ms). TRAIN 50 / VALIDATION 20 / OOS 30.
def split_3(df: pd.DataFrame, ts_col: str = 'ts') -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Хронологический сплит TRAIN 50 / VALIDATION 20 / OOS 30 по ts."""
    df = df.sort_values(ts_col).reset_index(drop=True)
    n = len(df)
    i1, i2 = int(n * 0.5), int(n * 0.7)
    return df.iloc[:i1].copy(), df.iloc[i1:i2].copy(), df.iloc[i2:].copy()


def load_panel(symbol: str, source: str = 'linear') -> pd.DataFrame | None:
    """Загрузка M15-панели: lag_cache (60d) → replay_cache (fallback)."""
    from pathlib import Path
    lag = Path('/tmp/lag_cache')
    f = lag / f'{source}_{symbol}_15.parquet'
    if f.exists():
        df = pd.read_parquet(f)
    else:
        rp = Path('/root/tradingos/replay_cache') / f'{symbol}_M15.parquet'
        if not rp.exists():
            return None
        df = pd.read_parquet(rp)
    df = df.sort_values('ts').drop_duplicates('ts').reset_index(drop=True)
    if df['ts'].iloc[0] > 1e12:
        df['ts'] = df['ts'] / 1000
    return df


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        (df['h'] - df['l']),
        (df['h'] - df['c'].shift(1)).abs(),
        (df['l'] - df['c'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def ema(vals: np.ndarray, span: int) -> np.ndarray:
    k = 2 / (span + 1)
    out = np.empty_like(vals)
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def shuffle_pvalue(actual: float, null: np.ndarray) -> float:
    """Доля null >= actual (permutation p-value)."""
    return float((null >= actual).mean()) if len(null) else 1.0


@dataclass
class EngineResult:
    """Результат прогона engine через фаннел."""
    engine_id: str
    verdict: str            # BUILD / PROMISING / DATA_REQUIRED / REJECT
    gross_bps: float = 0.0
    net_bps: float = 0.0
    oos_net_bps: float = 0.0
    pf: float = 0.0
    trades: int = 0
    median_mfe_r: float = 0.0
    median_mae_r: float = 0.0
    cost_survival_bps: float = 0.0   # при какой стоимости edge умирает
    reason: str = ""
    details: dict = field(default_factory=dict)
