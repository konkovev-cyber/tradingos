"""
DN-sweep stop-run reversal detector (T17 CONDITIONAL verdict, BTC LONG 4h, OOS +30bps maker).

Mechanism: M5/M15 bar wick-pierces prior 4h/1d-low by >X bps, closes back above.
Entry: WAIT for confirmation bar (close above prior low), LIMIT at close_price ± buffer.
NOT a market entry — LIMIT only, with expiry.

Output: signals with predicted entry zone (limit price), SL, TP — NOT executed.
Validated on history (replay_cache + fetched data). Promoted to live only after
OOS validation across 5 different periods.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReversalSignal:
    symbol: str
    side: str  # "BUY" for DN-sweep LONG
    ts: int  # detection timestamp (ms)
    entry_zone: float  # limit price (close of sweep bar)
    sl_price: float  # below the sweep wick low
    tp_price: float  # based on R-multiple target
    confidence: float  # 0..1
    pattern: str  # "DN_SWEEP"
    horizon_bars: int  # hold period in M15 bars
    r_target: float  # TP in R-multiples (e.g., 2.0)
    metadata: dict = field(default_factory=dict)


def detect_dn_sweeps(
    df: pd.DataFrame,
    symbol: str,
    pivot_h: int = 16,    # 4h lookback in M15 bars
    sweep_pierce_bps: float = 20,  # wick must pierce prior low by >20 bps
    min_close_back_above_bps: float = 5,  # close must be >5 bps above pierced low
    hold_bars: int = 16,  # 4h hold
    r_target: float = 2.0,
    atr_period: int = 14,
    sl_max_dist_pct: float = 0.08,  # hard stop cap: SL no farther than 8% below entry
) -> list[ReversalSignal]:
    """Detect DN-sweep reversal candidates.

    df must have columns: ts (ms), o, h, l, c, v
    """
    if df is None or len(df) < pivot_h + atr_period + 2:
        return []

    df = df.sort_values("ts").reset_index(drop=True)
    # ATR(14) Wilder
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - df["c"].shift()).abs(),
        (df["l"] - df["c"].shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/atr_period, adjust=False).mean()

    # Prior low: rolling min over pivot_h (excluding current bar)
    prior_low = df["l"].rolling(pivot_h, min_periods=pivot_h).max().shift(1).bfill()
    # We'll use min instead of max for "prior low" — it's the LOW of the window

    signals = []
    for i in range(pivot_h + atr_period, len(df) - 1):
        bar = df.iloc[i]
        prev_window = df.iloc[i - pivot_h:i]
        prior_low_v = prev_window["l"].min()
        bar_range = bar["h"] - bar["l"]
        if bar_range <= 0 or prior_low_v <= 0:
            continue
        # Wick pierces prior low
        wick_pierce = (bar["l"] < prior_low_v) and (
            (prior_low_v - bar["l"]) / prior_low_v * 1e4 > sweep_pierce_bps
        )
        # Close back above prior low (at least min_close_back_above_bps)
        close_back = bar["c"] > prior_low_v and (
            (bar["c"] - prior_low_v) / prior_low_v * 1e4 > min_close_back_above_bps
        )
        if not (wick_pierce and close_back):
            continue
        # Bar must be RED-leaning (more sell pressure during the sweep)
        # — body lower half of range, indicating aggressive selling
        body_in_range = (bar["c"] - bar["l"]) / bar_range if bar_range > 0 else 0
        if body_in_range > 0.55:  # too much buying pressure → not a sweep
            continue
        # Confidence: higher if wick pierce is wider, close recovery is faster
        pierce_bps = (prior_low_v - bar["l"]) / prior_low_v * 1e4
        recovery_bps = (bar["c"] - prior_low_v) / prior_low_v * 1e4
        conf = min(1.0, 0.4 + pierce_bps / 100 + recovery_bps / 100)

        entry_zone = bar["c"]  # LIMIT at close
        sl_price = bar["l"] - bar_range * 0.2  # below sweep wick by 0.2x bar range
        # VALIDATED exit model v2 (VALIDATION_v2_SL800.md): wick-based SL is
        # median 107bps away → 72% of trades stopped by M15 noise before the
        # reversal plays out. Cap the stop distance at sl_max_dist_pct.
        sl_price = max(sl_price, entry_zone * (1 - sl_max_dist_pct))
        risk_per_unit = entry_zone - sl_price
        if risk_per_unit <= 0:
            continue
        # NO TP in v2: exit at hold end (4h close). tp_price kept as a
        # far-level for logging/compat only; live logic must not use it.
        tp_price = entry_zone + risk_per_unit * r_target

        signals.append(ReversalSignal(
            symbol=symbol,
            side="BUY",
            ts=int(bar["ts"]),
            entry_zone=round(entry_zone, 8),
            sl_price=round(sl_price, 8),
            tp_price=round(tp_price, 8),
            confidence=round(conf, 3),
            pattern="DN_SWEEP",
            horizon_bars=hold_bars,
            r_target=r_target,
            metadata={
                "prior_low": round(prior_low_v, 8),
                "wick_low": round(float(bar["l"]), 8),
                "close": round(float(bar["c"]), 8),
                "pierce_bps": round(pierce_bps, 2),
                "recovery_bps": round(recovery_bps, 2),
                "bar_range": round(bar_range, 8),
            }
        ))
    return signals


def forward_returns_for_signals(
    df: pd.DataFrame,
    signals: list[ReversalSignal],
    hold_bars: int,
) -> pd.DataFrame:
    """For each signal, compute forward return at hold_bars horizon (gross of fees)."""
    if not signals:
        return pd.DataFrame()
    df = df.sort_values("ts").reset_index(drop=True)
    rows = []
    for sig in signals:
        # Find bar closest to ts
        idx = (df["ts"] - sig.ts).abs().idxmin()
        # Forward close at idx + hold_bars (skip if out of range)
        target_idx = idx + hold_bars
        if target_idx >= len(df):
            continue
        entry = df.iloc[idx]["c"]
        future = df.iloc[target_idx]["c"]
        ret = (future - entry) / entry if entry > 0 else 0.0
        rows.append({
            "symbol": sig.symbol,
            "ts": sig.ts,
            "entry_zone": sig.entry_zone,
            "ret_gross": ret,
            "sl_hit": future <= sig.sl_price,
            "tp_hit": future >= sig.tp_price,
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    pattern = "DN_SWEEP"
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    data_path = Path(f"/root/tradingos/replay_cache/{sym}_M15.parquet")
    if not data_path.exists():
        print(f"No data for {sym}")
        sys.exit(1)
    df = pd.read_parquet(data_path)
    df["ts"] = df["ts"]  # already ms
    sigs = detect_dn_sweeps(df, symbol=sym)
    print(f"{sym}: detected {len(sigs)} DN-sweep signals")
    fwd = forward_returns_for_signals(df, sigs, hold_bars=16)
    if len(fwd) > 0:
        wr = (fwd["ret_gross"] > 0).mean()
        med = fwd["ret_gross"].median()
        print(f"  4h forward: WR={100*wr:.1f}%, median ret={100*med:.3f}%")
